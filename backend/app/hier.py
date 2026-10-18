"""Hologram inside hologram: cluster again inside a cluster, as deep as it still makes sense.

A node is addressed by a path:  "t5" = the stored topic 5,  "k" = ALL mail grouped by what the subject says (kinds),
"t5.2" / "k.3.1" = child 2 of "t5" / child 1 of child 3 of "k".  Child ids are 1..n, 0 = the review bucket of that node.

Nothing is stored: nodes are computed when you open them (a few seconds, the first "k" a little longer) and kept in memory
until the next topic rebuild. Same local embedding model + UMAP + HDBSCAN as the topic map; no LLM, nothing leaves the server."""
import threading
import time
from collections import Counter

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .embeddings import ensure_embeddings, get_embedder, load_matrix
from .models import Message, MessageTopic, Score, TopicRun
from .topics import _label_clusters, _reduce, demote_outliers

MAX_DEPTH = 4          # k.3.1.2 is as deep as it goes
LEAF_BELOW = 16        # a group smaller than this is just shown as a list
TTL = 6 * 3600

_lock = threading.RLock()
_cache: dict[str, dict] = {}
_cache_sig: object = None
_subject_vecs: dict[str, np.ndarray] = {}


def _sig(db: Session):
    return db.scalar(select(TopicRun.id).order_by(TopicRun.id.desc()))


def _live_ids(db: Session) -> list[str]:
    return list(db.scalars(select(Message.message_id).where(Message.in_trash == False)))  # noqa: E712


def members(db: Session, path: str) -> list[str]:
    parts = path.split(".")
    if len(parts) > MAX_DEPTH:
        raise KeyError(path)
    if len(parts) == 1:
        root = parts[0]
        if root == "k":
            return _live_ids(db)
        if root[:1] == "t" and root[1:].isdigit():
            ids = list(db.scalars(select(MessageTopic.message_id).join(Message, Message.message_id == MessageTopic.message_id)
                                  .where(MessageTopic.topic_id == int(root[1:]), Message.in_trash == False)))  # noqa: E712
            if not ids:
                raise KeyError(path)
            return ids
        raise KeyError(path)
    if not parts[-1].isdigit():
        raise KeyError(path)
    node = get_node(db, ".".join(parts[:-1]))
    child = int(parts[-1])
    got = node["noise"] if child == 0 else node["members"].get(child)
    if got is None:
        raise KeyError(path)
    return got


def _subject_matrix(ids: list[str], msgs: dict[str, Message]) -> np.ndarray:
    """What the subject says, nothing else, so 'Delivered' from Amazon and from Flipkart land together."""
    emb = get_embedder()
    todo = [i for i in ids if i not in _subject_vecs]
    for s in range(0, len(todo), 64):
        chunk = todo[s:s + 64]
        vecs = emb.embed([msgs[i].subject or msgs[i].sender or "(no subject)" for i in chunk])
        for i, v in zip(chunk, vecs):
            _subject_vecs[i] = v / max(float(np.linalg.norm(v)), 1e-9)
    return np.vstack([_subject_vecs[i] for i in ids])


def _leaf(path: str, n: int, reason: str) -> dict:
    return {"path": path, "count": n, "leaf": True, "reason": reason, "topics": [], "members": {}, "noise": [], "xyz": {}}


def _build(db: Session, path: str) -> dict:
    ids = members(db, path)
    n = len(ids)
    if n < LEAF_BELOW:
        return _leaf(path, n, f"Only {n} mails here, so there is nothing more to split.")
    if len(path.split(".")) >= MAX_DEPTH:
        return _leaf(path, n, "This is as deep as it goes.")
    msgs = {m.message_id: m for m in db.scalars(select(Message).where(Message.message_id.in_(ids)))}
    by_subject = path == "k"
    if by_subject:
        ids = [i for i in ids if i in msgs]
        X = _subject_matrix(ids, msgs)
    else:
        ensure_embeddings(db, get_embedder())  # the stored vectors know the sender too, so a split can follow who sent it
        ids, X = load_matrix(db, ids)
        if len(ids) < LEAF_BELOW:
            return _leaf(path, len(ids), f"Only {len(ids)} mails here, so there is nothing more to split.")
    n = len(ids)
    min_size = max(4, min(settings.topic_min_cluster_size, n // 6))
    try:
        from sklearn.cluster import HDBSCAN
        nn = max(2, min(15, n - 1))
        Z = _reduce(X, max(2, min(10, n - 3)), nn)
        xyz = _reduce(X, 3, nn)
        labels = HDBSCAN(min_cluster_size=min_size).fit_predict(Z)
        labels = demote_outliers(X, labels, min_size)
    except Exception as e:  # tiny or degenerate group: show the list instead of failing
        return _leaf(path, n, f"Could not split this group further ({type(e).__name__}).")
    sizes = Counter(int(l) for l in labels if l >= 0)
    if len(sizes) < 2:
        return _leaf(path, n, "These mails are all alike, so there is nothing more to split.")
    order = {old: new for new, (old, _) in enumerate(sizes.most_common(), start=1)}
    mem: dict[int, list[str]] = {new: [] for new in order.values()}
    noise: list[str] = []
    for mid, lab in zip(ids, labels):
        (mem[order[int(lab)]] if lab >= 0 else noise).append(mid)
    docs = {k: " ".join((msgs[m].subject_key or msgs[m].subject or "") + ("" if by_subject else " " + msgs[m].sender) for m in v)
            for k, v in mem.items()}
    senders = {k: Counter(msgs[m].sender for m in v) for k, v in mem.items()}
    names = _label_clusters(docs, senders)
    seen = Counter(n[0] for n in names.values())
    for k in names:   # siblings that would read the same get told apart by who sends most of them
        if seen[names[k][0]] > 1:
            who = senders[k].most_common(1)[0][0].split(" <")[0][:18]
            names[k] = (f"{names[k][0]} — {who}", names[k][1])
    seen = Counter(n[0] for n in names.values())
    for k in names:
        if seen[names[k][0]] > 1:
            names[k] = (f"{names[k][0]} #{k}", names[k][1])
    topics = [{"topic_id": k, "label": names[k][0], "summary": names[k][1], "count": len(v)} for k, v in mem.items()]
    return {"path": path, "count": n, "leaf": False, "reason": "", "topics": topics, "members": mem, "noise": noise,
            "xyz": {mid: (float(p[0]), float(p[1]), float(p[2])) for mid, p in zip(ids, xyz)},
            "of": {mid: (order[int(l)] if l >= 0 else None) for mid, l in zip(ids, labels)}}


def get_node(db: Session, path: str) -> dict:
    global _cache_sig
    with _lock:
        sig = _sig(db)
        if sig != _cache_sig:   # topics were rebuilt: every saved result is out of date
            _cache.clear()
            _cache_sig = sig
        ent = _cache.get(path)
        if ent and time.time() - ent["at"] < TTL:
            return ent["node"]
        node = _build(db, path)
        _cache[path] = {"at": time.time(), "node": node}
        return node


def view(db: Session, path: str) -> dict:
    """What the dashboard draws for one node (trashed mail is dropped here, so Undo/Trash never needs a rebuild)."""
    node = get_node(db, path)
    all_ids = [i for k in node["members"].values() for i in k] + node["noise"] if not node["leaf"] else members(db, path)
    live = {r[0]: r for r in db.execute(select(Message.message_id, Message.subject, Message.sender)
                                        .where(Message.message_id.in_(all_ids), Message.in_trash == False))}  # noqa: E712
    if node["leaf"]:
        return {"path": path, "leaf": True, "reason": node["reason"], "count": len(live), "topics": [], "points": [], "noise": 0}
    pts = []
    counts: Counter = Counter()
    for mid, (x, y, z) in node["xyz"].items():
        if mid not in live:
            continue
        t = node["of"][mid]
        counts[t] += 1
        pts.append({"i": mid, "x": round(x, 4), "y": round(y, 4), "z": round(z, 4), "t": t, "s": live[mid][1], "f": live[mid][2]})
    topics = [{**t, "count": counts[t["topic_id"]]} for t in node["topics"] if counts[t["topic_id"]] > 0]
    return {"path": path, "leaf": False, "reason": "", "count": len(pts), "topics": topics, "points": pts, "noise": counts[None]}


def messages_query(db: Session, path: str, sub: int | None):
    """Mails of the node itself (sub=None), of its child (sub>=1) or of its review bucket (sub=0), most important first."""
    ids = members(db, path if sub is None else f"{path}.{sub}")
    return (select(Message, Score).outerjoin(Score, Score.message_id == Message.message_id)
            .where(Message.message_id.in_(ids), Message.in_trash == False)  # noqa: E712
            .order_by(Score.importance_score.desc(), Message.date.desc()).limit(500))
