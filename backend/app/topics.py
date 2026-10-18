"""Phase 2: content clusters. embeddings -> UMAP -> HDBSCAN (FR-20), 2D coords stored (FR-25),
keyword labels (no LLM), stable topic IDs across re-clustering, daily assignment of new mail (FR-24)."""
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from . import control
from . import naming
from .config import settings
from .embeddings import Embedder, ensure_embeddings, get_embedder, load_matrix
from .models import Message, MessageTopic, Score, Topic, TopicRun

_STOP = set("""the and for you your are with this that from have has was were will not but all can our out new get
just now more than then them they their about into over how what when who why com www http https mail email via
re fwd fw inc ltd team hello dear thanks thank please""".split())


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z]{3,}", text.lower()) if t not in _STOP]


def _label_clusters(docs: dict[int, str], senders: dict[int, Counter]) -> dict[int, tuple[str, str]]:
    """c-TF-IDF-style keywords per cluster: words common in one cluster but rare across the others."""
    tf = {k: Counter(_tokens(d)) for k, d in docs.items()}
    df = Counter(t for c in tf.values() for t in c)
    n = len(tf)
    out = {}
    for k, c in tf.items():
        total = sum(c.values()) or 1
        scored = sorted(c, key=lambda t: -(c[t] / total) * math.log(1 + n / df[t]))
        words = scored[:3]
        top = senders[k].most_common(2)
        label = " · ".join(words) if words else "mixed"
        size = sum(senders[k].values())
        out[k] = (label, f"{size} mails · mostly " + ", ".join(f"{s} ({c})" for s, c in top))
    return out


def _reduce(X: np.ndarray, n_components: int, n_neighbors: int) -> np.ndarray:
    import umap
    return umap.UMAP(n_components=n_components, n_neighbors=n_neighbors, min_dist=0.0 if n_components > 2 else 0.3,
                     metric="cosine", random_state=42).fit_transform(X)


def demote_outliers(X: np.ndarray, labels: np.ndarray, min_size: int, z: float = 3.0) -> np.ndarray:
    """UMAP packs points into dense blobs, so HDBSCAN rarely says 'noise'. Check each mail against its own cluster in the
    ORIGINAL embedding space: if it is far less similar to the cluster centre than its neighbours (robust median/MAD test),
    it goes to the review bucket. Clusters left too small are dissolved."""
    labels = labels.copy()
    for lab in set(labels.tolist()) - {-1}:
        idx = np.where(labels == lab)[0]
        if len(idx) < 5:
            continue
        c = X[idx].mean(axis=0)
        c /= max(np.linalg.norm(c), 1e-9)
        sims = X[idx] @ c
        med = np.median(sims)
        mad = max(np.median(np.abs(sims - med)) * 1.4826, 0.02)
        labels[idx[sims < med - z * mad]] = -1
    for lab in set(labels.tolist()) - {-1}:
        if (labels == lab).sum() < min_size:
            labels[labels == lab] = -1
    return labels


def recluster(db: Session, embedder: Embedder | None = None) -> TopicRun:
    control.topics_active.set()  # the dashboard's "Rebuilding…" comes from this, never from a leftover database row
    try:
        return _recluster(db, embedder)
    finally:
        control.topics_active.clear()


def _recluster(db: Session, embedder: Embedder | None = None) -> TopicRun:
    run = TopicRun()
    db.add(run)
    db.commit()
    try:
        ensure_embeddings(db, embedder or get_embedder())
        ids, X = load_matrix(db)
        run.messages = len(ids)
        if len(ids) < settings.topic_min_messages:
            run.ended = datetime.now(timezone.utc)
            db.commit()
            return run
        from sklearn.cluster import HDBSCAN
        nn = max(2, min(15, len(ids) - 1))
        Z = _reduce(X, min(10, len(ids) - 2), nn)
        xyz = _reduce(X, 3, nn)  # stored once; the flat map uses x,y and the hologram uses x,y,z
        labels = HDBSCAN(min_cluster_size=settings.topic_min_cluster_size).fit_predict(Z)
        labels = demote_outliers(X, labels, settings.topic_min_cluster_size)
        _store(db, run, ids, xyz, labels)
    except Exception as e:
        db.rollback()
        run = db.merge(run)
        run.error, run.ended = str(e), datetime.now(timezone.utc)
        db.commit()
        raise
    return run


def _store(db: Session, run: TopicRun, ids: list[str], xyz: np.ndarray, labels: np.ndarray):
    msgs = {m.message_id: m for m in db.scalars(select(Message).where(Message.message_id.in_(ids)))}
    members: dict[int, list[str]] = defaultdict(list)
    for mid, lab in zip(ids, labels):
        if lab >= 0:
            members[int(lab)].append(mid)

    # Stable IDs: a new cluster inherits the old topic that shares most of its mails (>=50%).
    old = dict(db.execute(select(MessageTopic.message_id, MessageTopic.topic_id).where(MessageTopic.topic_id.is_not(None))).all())
    claimed: set[int] = set()
    topic_of: dict[int, Topic] = {}
    for lab, mids in sorted(members.items(), key=lambda kv: -len(kv[1])):
        votes = Counter(old[m] for m in mids if m in old and old[m] not in claimed)
        best = votes.most_common(1)
        if best and best[0][1] / len(mids) >= 0.5:
            t = db.get(Topic, best[0][0])
            if t:
                claimed.add(t.topic_id)
                topic_of[lab] = t
                continue
        t = Topic()
        db.add(t)
        db.flush()
        claimed.add(t.topic_id)
        topic_of[lab] = t

    docs = {l: " ".join(f"{msgs[m].subject_key} {msgs[m].sender}" for m in mids) for l, mids in members.items()}
    senders = {l: Counter(msgs[m].sender for m in mids) for l, mids in members.items()}
    names = naming.rename(db, members, msgs, _label_clusters(docs, senders))
    for lab, t in topic_of.items():
        t.label, t.summary = names[lab]
        t.size = len(members[lab])

    db.execute(delete(MessageTopic))
    for i, (mid, lab) in enumerate(zip(ids, labels)):
        t = topic_of.get(int(lab))
        db.add(MessageTopic(message_id=mid, topic_id=t.topic_id if t else None,
                            x=float(xyz[i][0]), y=float(xyz[i][1]), z=float(xyz[i][2]), is_noise=t is None))
    for t in db.scalars(select(Topic).where(Topic.topic_id.not_in(claimed) if claimed else True)):
        db.delete(t)
    run.topics, run.noise = len(topic_of), int((labels < 0).sum())
    run.ended = datetime.now(timezone.utc)
    db.commit()


def assign_new(db: Session, embedder: Embedder | None = None, k: int = 5, min_sim: float = 0.5) -> int:
    """Daily step: put mail that arrived since the last clustering into the nearest existing topic.
    Coordinates are the average of the nearest neighbours', so no UMAP model needs to be kept."""
    ensure_embeddings(db, embedder or get_embedder())
    placed = {r[0] for r in db.execute(select(MessageTopic.message_id)).all()}
    base_ids, Xb = load_matrix(db, list(placed)) if placed else ([], None)
    if not base_ids:
        return 0
    new_ids, Xn = load_matrix(db)
    keep = [i for i, m in enumerate(new_ids) if m not in placed]
    if not keep:
        return 0
    new_ids, Xn = [new_ids[i] for i in keep], Xn[keep]
    mt = {r.message_id: r for r in db.scalars(select(MessageTopic).where(MessageTopic.message_id.in_(base_ids)))}
    bt = [mt[m] for m in base_ids]
    coords = np.array([[r.x, r.y, r.z] for r in bt])
    for s in range(0, len(new_ids), 512):
        sims = Xn[s:s + 512] @ Xb.T
        for row, mid in zip(sims, new_ids[s:s + 512]):
            top = np.argsort(-row)[:k]
            votes: Counter = Counter()
            for j in top:
                if bt[j].topic_id is not None:
                    votes[bt[j].topic_id] += float(row[j])
            tid = votes.most_common(1)[0][0] if votes and row[top[0]] >= min_sim and \
                votes.most_common(1)[0][1] > 0.5 * sum(float(row[j]) for j in top) else None
            x, y, z = coords[top].mean(axis=0)
            db.add(MessageTopic(message_id=mid, topic_id=tid, x=float(x), y=float(y), z=float(z), is_noise=tid is None))
    db.flush()
    refresh_sizes(db)
    db.commit()
    return len(new_ids)


def refresh_sizes(db: Session):
    counts = dict(db.execute(select(MessageTopic.topic_id, func.count()).join(
        Message, Message.message_id == MessageTopic.message_id)
        .where(Message.in_trash == False, MessageTopic.topic_id.is_not(None))  # noqa: E712
        .group_by(MessageTopic.topic_id)).all())
    for t in db.scalars(select(Topic)):
        t.size = counts.get(t.topic_id, 0)


def prune_orphans(db: Session):
    """Drop topic rows for mail that no longer exists locally (evicted / purged)."""
    db.execute(delete(MessageTopic).where(~MessageTopic.message_id.in_(select(Message.message_id))))
    from .models import Embedding
    db.execute(delete(Embedding).where(~Embedding.message_id.in_(select(Message.message_id))))


def recluster_due(db: Session) -> bool:
    last = db.scalar(select(TopicRun).where(TopicRun.ended.is_not(None), TopicRun.error.is_(None))
                     .order_by(TopicRun.id.desc()))
    if last is None or last.messages < settings.topic_min_messages:
        return True
    ended = last.ended if last.ended.tzinfo else last.ended.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - ended > timedelta(days=settings.recluster_days)


def after_sync(db: Session, embedder: Embedder | None = None):
    """Called after every sync: weekly full re-cluster, otherwise assign the new mail to existing topics."""
    if not settings.topics_enabled:
        return
    prune_orphans(db)
    db.commit()
    if recluster_due(db):
        recluster(db, embedder)
    else:
        assign_new(db, embedder)
        refresh_sizes(db)
        db.commit()
