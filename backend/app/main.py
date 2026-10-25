import asyncio
import hmac
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import String, func, select
from sqlalchemy.orm import Session

from . import actions as act
from . import auth
from .config import settings
from . import db as dbmod
from . import users
from .db import Base, CentralBase, engine, get_central_db, get_db
from .gmail import GmailClient, GoogleGmailClient, gmail_web_url
from .models import Action, Cluster, Message, MessageCluster, MessageTopic, Owner, Score, SyncRun, Topic, TopicRun
from .security import decrypt, encrypt
from .capacity import refill, refill_user_in_batches
from . import control
from . import hier
from . import holo_layout
from . import topics as topics_mod
from .purge import PurgeError, purge_trash, trash_summary
from .sync import close_orphan_runs, run_sync, sync_in_progress

@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    CentralBase.metadata.create_all(engine)
    with dbmod.central_session() as central:
        users.ensure_owner(central)
        keys = [u.data_key for u in users.all_users(central)]
    for key in keys:
        with dbmod.open_user_session(key) as db:
            close_orphan_runs(db)  # a restart mid-sync must not leave a phantom 'running' sync behind
    task = asyncio.create_task(_scheduler()) if settings.sync_interval_minutes > 0 else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="Gmail Smart Inbox", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_url],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


def build_gmail(db: Session) -> GmailClient | None:
    from google.oauth2.credentials import Credentials
    owner = db.scalar(select(Owner))
    if not owner:
        return None
    creds = Credentials(None, refresh_token=decrypt(owner.refresh_token_enc),
                        token_uri="https://oauth2.googleapis.com/token",
                        client_id=settings.google_client_id, client_secret=settings.google_client_secret)
    return GoogleGmailClient(creds)


def get_gmail(db: Session = Depends(get_db)) -> GmailClient:
    gmail = build_gmail(db)
    if gmail is None:
        raise HTTPException(409, "Gmail not connected")
    return gmail


def _on_batch(db: Session, _done: int):
    """After every small batch of mail: embed it and place it in a topic, so the screen grows batch by batch."""
    try:
        topics_mod.progress(db)
    except Exception:
        db.rollback()


def _refill_job(key: str):
    """After mail left (trash, or deleted in Gmail): pull older mail back in, a few at a time, until the cap is full again."""
    with control.scope(key):
        if control.sync_active.is_set() or control.refill_active.is_set():
            return
        control.refill_active.set()
        try:
            with dbmod.open_user_session(key) as db:
                gmail = build_gmail(db)
                if gmail:
                    refill_user_in_batches(db, gmail, settings.user_cap, settings.batch_size, _on_batch,
                                           cancelled=lambda: control.sync_active.is_set())
        except Exception:
            pass  # nothing is lost: the next sync or trash action tries again
        finally:
            control.refill_active.clear()


def _sync_job(key: str, already_marked: bool = False):
    """One user's sync, run in the background. `key` is that user's data key."""
    with control.scope(key):
        _sync_job_inner(key, already_marked)


def _sync_job_inner(key: str, already_marked: bool):
    if not already_marked:
        if control.sync_active.is_set():
            return
        control.sync_active.set()
    try:
        with dbmod.open_user_session(key) as db:
            gmail = build_gmail(db)
            if not gmail:
                return
            try:
                run = run_sync(db, gmail, settings.initial_fetch, settings.cluster_cap,
                               settings.user_cap, settings.batch_size, _on_batch)
            except Exception:
                return  # recorded in sync_runs.error; next run resumes
            if run.error == control.STOPPED:
                return  # the user stopped it: do not start the (long) topic build afterwards
            try:  # mail may have left Gmail meanwhile: top the mailbox back up, in batches
                refill_user_in_batches(db, gmail, settings.user_cap, settings.batch_size, _on_batch,
                                       cancelled=lambda: control.cancel_sync.is_set())
            except Exception:
                db.rollback()
            try:
                topics_mod.after_sync(db)  # weekly re-cluster, otherwise assign new mail to existing topics
            except Exception:
                db.rollback()  # topic problems (e.g. model not downloaded yet) must never break syncing
    finally:
        control.sync_active.clear()


def _sync_all():
    """The scheduled sync: every user in turn (one at a time, so ten users never load at once)."""
    with dbmod.central_session() as central:
        keys = [u.data_key for u in users.all_users(central)]
    for key in keys:
        _sync_job(key)


async def _scheduler():
    """Sync everyone every SYNC_INTERVAL_MINUTES without blocking the app."""
    while True:
        await asyncio.sleep(settings.sync_interval_minutes * 60)
        await asyncio.to_thread(_sync_all)


@app.get("/health")
def health():
    return {"ok": True}


# ---------- sign-in ----------
@app.get("/auth/login")
def login():
    flow = auth.build_flow()
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    resp = RedirectResponse(url)
    resp.set_cookie(auth.OAUTH_COOKIE, auth.make_oauth_cookie(state, flow.code_verifier), httponly=True,
                    secure=settings.frontend_url.startswith("https"), samesite="lax", max_age=600)
    return resp


@app.get("/auth/callback")
def callback(code: str, state: str, request: Request, central: Session = Depends(get_central_db)):
    from google.auth.transport.requests import Request as GoogleRequest
    from google.oauth2 import id_token
    saved = auth.read_oauth_cookie(request.cookies.get(auth.OAUTH_COOKIE))
    if not hmac.compare_digest(saved["state"], state):
        raise HTTPException(400, "Sign-in state does not match. Press Sign in with Google again.")
    flow = auth.build_flow()
    flow.code_verifier = saved["verifier"]  # PKCE: must be the same one used to start the sign-in
    flow.fetch_token(code=code)
    info = id_token.verify_oauth2_token(flow.credentials.id_token, GoogleRequest(), settings.google_client_id)
    email = info["email"]
    if not info.get("email_verified", True):
        raise HTTPException(403, "Google says this e-mail address is not verified")
    if not auth.is_allowed(email):
        raise HTTPException(403, "This account is not allowed")
    try:
        user = users.get_or_create(central, email)
    except users.PilotFull as e:
        raise HTTPException(403, str(e))
    if flow.credentials.refresh_token:
        with dbmod.open_user_session(user.data_key) as udb:  # the token is kept in THAT user's own database
            udb.merge(Owner(id=1, email=email, refresh_token_enc=encrypt(flow.credentials.refresh_token)))
            udb.commit()
    resp = RedirectResponse(settings.frontend_url)
    resp.delete_cookie(auth.OAUTH_COOKIE)
    resp.set_cookie(auth.COOKIE, auth.make_session(email, user.id, user.data_key), httponly=True, secure=settings.frontend_url.startswith("https"),
                    samesite="lax", max_age=auth.MAX_AGE)
    return resp


@app.post("/auth/logout")
def logout():
    resp = RedirectResponse(settings.frontend_url, status_code=303)
    resp.delete_cookie(auth.COOKIE)
    return resp


@app.get("/me")
def me(user: auth.Principal = Depends(auth.current_user)):
    return {"email": user.email}


@app.delete("/account")
def delete_account(response: Response, db: Session = Depends(get_db), user: auth.Principal = Depends(auth.current_user),
                   central: Session = Depends(get_central_db)):
    """Revoke the Google token and erase every row of THIS user's mailbox, and take them off the user list."""
    import httpx
    owner = db.scalar(select(Owner))
    if owner:
        try:
            httpx.post("https://oauth2.googleapis.com/revoke", params={"token": decrypt(owner.refresh_token_enc)}, timeout=5)
        except Exception:
            pass
    for t in reversed(Base.metadata.sorted_tables):
        db.execute(t.delete())
    db.commit()
    users.forget(central, user.email)
    if user.data_key != "main":
        db.close()
        dbmod.drop_user_data(user.data_key)  # their own database file goes away completely
    response.delete_cookie(auth.COOKIE)  # signed out: with open sign-up a stale cookie must not quietly recreate an empty mailbox
    return {"ok": True}


# ---------- sync ----------
@app.post("/sync")
def sync(background: BackgroundTasks, db: Session = Depends(get_db), user: auth.Principal = Depends(auth.current_user),
         __: GmailClient = Depends(get_gmail)):
    """Start a sync in the background; poll /sync/status for progress."""
    if sync_in_progress(db):
        return {"started": False, "reason": "already running"}
    control.sync_active.set()  # claim it now so a double click cannot start two syncs
    background.add_task(_sync_job, user.data_key, True)
    return {"started": True}


@app.post("/sync/stop")
def sync_stop(db: Session = Depends(get_db), user: auth.Principal = Depends(auth.current_user)):
    """Stop the running sync as soon as possible. Mail already read is kept; Sync now continues from there."""
    if not sync_in_progress(db):
        close_orphan_runs(db)  # heal a leftover "running" row from a restart
        return {"stopping": False, "reason": "no sync is running"}
    control.cancel_sync.set()
    return {"stopping": True}


@app.get("/sync/status")
def sync_status(db: Session = Depends(get_db), user: auth.Principal = Depends(auth.current_user)):
    run = db.scalar(select(SyncRun).order_by(SyncRun.id.desc()))
    stopped = bool(run and run.error == control.STOPPED)
    return {"running": sync_in_progress(db), "fetched": run.fetched if run else 0,
            "total": run.total if run else 0, "error": None if stopped else (run.error if run else None),
            "stopped": stopped, "refilling": control.refill_active.is_set(),
            "connected": db.scalar(select(Owner.id)) is not None,
            "last_sync": run.ended.isoformat() if run and run.ended else None}


# ---------- dashboard data ----------
def _row(m: Message, s: Score | None, email: str):
    return {"message_id": m.message_id, "sender": m.sender, "subject": m.subject,
            "date": m.date.isoformat(), "labels": m.labels, "gmail_link": gmail_web_url(m.thread_id, email),
            "list_unsubscribe": m.list_unsubscribe, "class": s.cls if s else None,
            "score": s.importance_score if s else None, "reason": s.reason if s else None}


@app.get("/summary")
def summary(db: Session = Depends(get_db), user: auth.Principal = Depends(auth.current_user)):
    live = Message.in_trash == False  # noqa: E712
    total = db.scalar(select(func.count()).select_from(Message).where(live))
    grouped = db.scalar(select(func.count()).select_from(MessageCluster).join(
        Message, Message.message_id == MessageCluster.message_id).where(MessageCluster.is_noise == False, live))  # noqa: E712
    review = db.scalar(select(func.count()).select_from(Score).join(
        Message, Message.message_id == Score.message_id).where(Score.cls.in_(["urgent", "needs_reply"]), live))
    return {"total": total, "grouped": grouped, "needs_review": review}


@app.get("/needs-action")
def needs_action(limit: int = 50, db: Session = Depends(get_db), user: auth.Principal = Depends(auth.current_user)):
    q = (select(Message, Score).join(Score, Score.message_id == Message.message_id)
         .where(Score.cls.in_(["urgent", "needs_reply"]), Message.in_trash == False).order_by(Score.importance_score.desc(), Message.date.desc())
         .limit(limit))
    return [_row(m, s, user.email) for m, s in db.execute(q)]


def _filters(since_days, date_from, date_to, sender, label, min_score):
    """Shared filter conditions (FR-17 / FR-35): date range, sender, label, score threshold."""
    conds = []
    if since_days:
        conds.append(Message.date >= datetime.now(timezone.utc) - timedelta(days=since_days))
    if date_from:
        conds.append(Message.date >= date_from)
    if date_to:
        conds.append(Message.date < date_to + timedelta(days=1))
    if sender:
        conds.append(Message.sender.ilike(f"%{sender}%") | Message.sender_email.ilike(f"%{sender}%"))
    if label:
        conds.append(Message.labels.cast(String).like(f'%"{label}"%'))
    if min_score:
        conds.append(Score.importance_score >= min_score)
    return conds


@app.get("/groups")
def groups(db: Session = Depends(get_db), user: auth.Principal = Depends(auth.current_user),
           since_days: int | None = None, date_from: date | None = None, date_to: date | None = None,
           sender: str | None = None, label: str | None = None, min_score: int = 0):
    """Bubble data: size = mail count, color = average importance. Filters update it live."""
    q = (select(Cluster.cluster_id, Cluster.label, func.count(Message.message_id), func.avg(Score.importance_score),
                func.sum(Message.size_bytes), Cluster.gmail_total)
         .join(MessageCluster, MessageCluster.cluster_id == Cluster.cluster_id)
         .join(Message, Message.message_id == MessageCluster.message_id)
         .join(Score, Score.message_id == Message.message_id)
         .where(Message.in_trash == False, *_filters(since_days, date_from, date_to, sender, label, min_score))  # noqa: E712
         .group_by(Cluster.cluster_id, Cluster.label, Cluster.gmail_total))
    return [{"cluster_id": i, "label": l, "count": c, "importance": round(a or 0), "bytes": b or 0,
             "gmail_total": t or c} for i, l, c, a, b, t in db.execute(q)]


@app.get("/groups/hologram")
def groups_hologram(db: Session = Depends(get_db), user: auth.Principal = Depends(auth.current_user),
                    since_days: int | None = None, date_from: date | None = None, date_to: date | None = None,
                    sender: str | None = None, label: str | None = None, min_score: int = 0):
    """The Groups tab as a hologram: same data as /groups, but every mail is a node. Same shape as /map
    (points i, x, y, z, t, s, f + topics) with t = the group's id."""
    q = (select(Cluster.cluster_id, Cluster.label, Message.message_id, Message.subject, Message.sender)
         .join(MessageCluster, MessageCluster.cluster_id == Cluster.cluster_id)
         .join(Message, Message.message_id == MessageCluster.message_id)
         .join(Score, Score.message_id == Message.message_id)
         .where(Message.in_trash == False, *_filters(since_days, date_from, date_to, sender, label, min_score))  # noqa: E712
         .limit(30_000))
    ids: dict[int, list[str]] = {}
    labels: dict[int, str] = {}
    meta: dict[str, tuple[int, str, str]] = {}
    for cid, lab, mid, subj, snd in db.execute(q):
        ids.setdefault(cid, []).append(mid)
        labels[cid] = lab
        meta[mid] = (cid, subj, snd)
    pos = holo_layout.layout([(str(c), m) for c, m in ids.items()])
    return {"points": [{"i": m, "x": round(pos[m][0], 4), "y": round(pos[m][1], 4), "z": round(pos[m][2], 4),
                        "t": c, "s": s_, "f": f} for m, (c, s_, f) in meta.items()],
            "topics": [{"topic_id": c, "label": labels[c], "count": len(m)}
                       for c, m in sorted(ids.items(), key=lambda kv: -len(kv[1]))]}


@app.get("/groups/{cluster_id}/hologram")
def group_hologram(cluster_id: int, since_days: int | None = None, date_from: date | None = None,
                   date_to: date | None = None, sender: str | None = None, label: str | None = None,
                   min_score: int = 0, db: Session = Depends(get_db), user: auth.Principal = Depends(auth.current_user)):
    """Inside one group: its mails as a hologram, one ball per subject template ('Your order # has shipped').
    The 12 biggest templates get a ball; the rest are loose grey nodes (t = null)."""
    q = (select(Message.message_id, Message.subject, Message.sender, Message.subject_key)
         .join(MessageCluster, MessageCluster.message_id == Message.message_id)
         .outerjoin(Score, Score.message_id == Message.message_id)
         .where(MessageCluster.cluster_id == cluster_id, Message.in_trash == False,  # noqa: E712
                *_filters(since_days, date_from, date_to, sender, label, min_score)))
    rows = list(db.execute(q))
    by_key: dict[str, list[str]] = {}
    for mid, _s, _f, key in rows:
        by_key.setdefault(key or "(no subject)", []).append(mid)
    top = sorted(by_key, key=lambda k: -len(by_key[k]))[:12]
    tid = {k: n for n, k in enumerate(top, 1)}
    pos = holo_layout.layout([(k, m) for k, m in by_key.items()])
    key_of = {mid: (key or "(no subject)") for mid, _s, _f, key in rows}
    return {"points": [{"i": mid, "x": round(pos[mid][0], 4), "y": round(pos[mid][1], 4), "z": round(pos[mid][2], 4),
                        "t": tid.get(key_of[mid]), "s": subj, "f": snd} for mid, subj, snd, _k in rows],
            "topics": [{"topic_id": tid[k], "label": k[:48], "count": len(by_key[k])} for k in top]}


@app.get("/groups/{cluster_id}/subjects")
def group_subjects(cluster_id: int, db: Session = Depends(get_db), user: auth.Principal = Depends(auth.current_user)):
    """Subject templates inside a group ('Your order # has shipped'), biggest first, with message ids
    so a whole template can be bulk-actioned."""
    rows = db.execute(select(Message.subject_key, Message.message_id, Message.size_bytes)
                      .join(MessageCluster, MessageCluster.message_id == Message.message_id)
                      .where(MessageCluster.cluster_id == cluster_id, Message.in_trash == False))  # noqa: E712
    out: dict[str, dict] = {}
    for key, mid, size in rows:
        g = out.setdefault(key, {"subject": key, "count": 0, "bytes": 0, "message_ids": []})
        g["count"] += 1; g["bytes"] += size or 0; g["message_ids"].append(mid)
    return sorted(out.values(), key=lambda g: -g["count"])


@app.get("/storage")
def storage(db: Session = Depends(get_db), user: auth.Principal = Depends(auth.current_user)):
    """Where the space is going, and how much Trash will free up once Gmail empties it (after 30 days)."""
    def total(flag):
        return db.scalar(select(func.coalesce(func.sum(Message.size_bytes), 0)).where(Message.in_trash == flag))
    top = db.execute(select(Cluster.label, func.sum(Message.size_bytes).label("b"))
                     .join(MessageCluster, MessageCluster.cluster_id == Cluster.cluster_id)
                     .join(Message, Message.message_id == MessageCluster.message_id)
                     .where(Message.in_trash == False).group_by(Cluster.label)  # noqa: E712
                     .order_by(func.sum(Message.size_bytes).desc()).limit(10))
    return {"kept_bytes": total(False), "in_trash_bytes": total(True),
            "biggest_groups": [{"label": l, "bytes": b or 0} for l, b in top]}


@app.get("/groups/{cluster_id}/messages")
def group_messages(cluster_id: int, since_days: int | None = None, date_from: date | None = None,
                   date_to: date | None = None, sender: str | None = None, label: str | None = None,
                   min_score: int = 0, db: Session = Depends(get_db), user: auth.Principal = Depends(auth.current_user)):
    q = (select(Message, Score).join(MessageCluster, MessageCluster.message_id == Message.message_id)
         .outerjoin(Score, Score.message_id == Message.message_id)
         .where(MessageCluster.cluster_id == cluster_id, Message.in_trash == False,  # noqa: E712
                *_filters(since_days, date_from, date_to, sender, label, min_score))
         .order_by(Message.date.desc()))
    return [_row(m, s, user.email) for m, s in db.execute(q)]


# ---------- actions ----------
class ActionIn(BaseModel):
    message_ids: list[str]
    action: str
    param: str | None = None
    confirmed: bool = False


@app.post("/actions/preview")
def preview(body: ActionIn, db: Session = Depends(get_db), user: auth.Principal = Depends(auth.current_user)):
    return act.bulk_preview(db, body.message_ids)


@app.post("/actions")
def do_action(body: ActionIn, background: BackgroundTasks, db: Session = Depends(get_db),
              gmail: GmailClient = Depends(get_gmail), user: auth.Principal = Depends(auth.current_user)):
    try:
        batch = act.perform(db, gmail, body.message_ids, body.action, body.param, body.confirmed)
    except act.ActionError as e:
        raise HTTPException(400, str(e))
    refilled = 0
    if body.action == "trash":  # fill the cluster back up with older mail
        cids = set(db.scalars(select(MessageCluster.cluster_id).where(MessageCluster.message_id.in_(body.message_ids))))
        refilled = sum(refill(db, gmail, cid, settings.cluster_cap) for cid in cids)
        background.add_task(_refill_job, user.data_key)  # and the whole mailbox back up to the cap, a few mails at a time
    return {"batch_id": batch, "refilled": refilled}


@app.post("/actions/{batch_id}/undo")
def undo(batch_id: str, db: Session = Depends(get_db), gmail: GmailClient = Depends(get_gmail),
         user: auth.Principal = Depends(auth.current_user)):
    return {"undone": act.undo(db, gmail, batch_id)}


@app.get("/actions/log")
def action_log(db: Session = Depends(get_db), user: auth.Principal = Depends(auth.current_user)):
    rows = db.scalars(select(Action).order_by(Action.action_id.desc()).limit(100))
    return [{"batch_id": a.batch_id, "message_id": a.message_id, "action": a.action,
             "timestamp": a.timestamp.isoformat(), "undone": a.undone} for a in rows]


# ---------- Phase 2: topic clusters + map ----------
def _live_topic_rows(db: Session):
    return (select(MessageTopic, Message).join(Message, Message.message_id == MessageTopic.message_id)
            .where(Message.in_trash == False))  # noqa: E712


@app.get("/topics")
def topics(db: Session = Depends(get_db), user: auth.Principal = Depends(auth.current_user)):
    q = (select(Topic.topic_id, Topic.label, Topic.summary, func.count(Message.message_id), func.avg(Score.importance_score))
         .join(MessageTopic, MessageTopic.topic_id == Topic.topic_id)
         .join(Message, Message.message_id == MessageTopic.message_id)
         .outerjoin(Score, Score.message_id == Message.message_id)
         .where(Message.in_trash == False).group_by(Topic.topic_id, Topic.label, Topic.summary))  # noqa: E712
    noise = db.scalar(select(func.count()).select_from(MessageTopic).join(
        Message, Message.message_id == MessageTopic.message_id)
        .where(MessageTopic.topic_id.is_(None), Message.in_trash == False))  # noqa: E712
    return {"topics": sorted(({"topic_id": i, "label": l, "summary": s, "count": c, "importance": round(a or 0)}
                              for i, l, s, c, a in db.execute(q)), key=lambda t: -t["count"]), "noise": noise}


@app.get("/map")
def cluster_map(db: Session = Depends(get_db), user: auth.Principal = Depends(auth.current_user)):
    """Stored 2D coordinates only (no live UMAP). Compact keys: i=id, x, y, z, t=topic (null = review bucket), s=subject, f=sender."""
    rows = db.execute(select(MessageTopic.message_id, MessageTopic.x, MessageTopic.y, MessageTopic.z, MessageTopic.topic_id,
                             Message.subject, Message.sender)
                      .join(Message, Message.message_id == MessageTopic.message_id)
                      .where(Message.in_trash == False).limit(100_000))  # noqa: E712
    return {"points": [{"i": i, "x": round(x, 4), "y": round(y, 4), "z": round(z or 0, 4), "t": t, "s": s, "f": f}
                       for i, x, y, z, t, s, f in rows]}


@app.get("/topics/status")
def topics_status(db: Session = Depends(get_db), user: auth.Principal = Depends(auth.current_user)):
    run = db.scalar(select(TopicRun).order_by(TopicRun.id.desc()))
    running = control.topics_active.is_set()
    return {"running": running, "topics": run.topics if run else 0, "noise": run.noise if run else 0,
            "messages": run.messages if run else 0, "error": run.error if run else None,
            "min_messages": settings.topic_min_messages,
            "last_run": run.ended.isoformat() if run and run.ended else None}


def _recluster_job(key: str):
    with control.scope(key), dbmod.open_user_session(key) as db:
        try:
            topics_mod.recluster(db)
        except Exception:
            pass  # recorded in topic_runs.error


@app.post("/topics/recluster")
def topics_recluster(background: BackgroundTasks, db: Session = Depends(get_db), user: auth.Principal = Depends(auth.current_user)):
    if topics_status(db)["running"]:
        return {"started": False, "reason": "already running"}
    background.add_task(_recluster_job, user.data_key)
    return {"started": True}


@app.get("/topics/{topic_id}/messages")
def topic_messages(topic_id: int, db: Session = Depends(get_db), user: auth.Principal = Depends(auth.current_user)):
    """topic_id 0 = the review bucket (mail that fits no topic), most important first (FR-23)."""
    cond = MessageTopic.topic_id.is_(None) if topic_id == 0 else MessageTopic.topic_id == topic_id
    q = (select(Message, Score).join(MessageTopic, MessageTopic.message_id == Message.message_id)
         .outerjoin(Score, Score.message_id == Message.message_id)
         .where(cond, Message.in_trash == False)  # noqa: E712
         .order_by(Score.importance_score.desc(), Message.date.desc()).limit(500))
    return [_row(m, s, user.email) for m, s in db.execute(q)]


@app.get("/hier/{path}")
def hier_view(path: str, db: Session = Depends(get_db), user: auth.Principal = Depends(auth.current_user)):
    """One level of 'hologram inside hologram'. path: t<topic id> | k, then .<child id> per level. Can take a while the first time."""
    try:
        return hier.view(db, path)
    except KeyError:
        raise HTTPException(404, "unknown group")


@app.get("/hier/{path}/messages")
def hier_messages(path: str, sub: int | None = None, db: Session = Depends(get_db), user: auth.Principal = Depends(auth.current_user)):
    try:
        q = hier.messages_query(db, path, sub)
    except KeyError:
        raise HTTPException(404, "unknown group")
    return [_row(m, s, user.email) for m, s in db.execute(q)]


@app.get("/messages/{message_id}")
def message_detail(message_id: str, db: Session = Depends(get_db), user: auth.Principal = Depends(auth.current_user)):
    m = db.get(Message, message_id)
    if not m:
        raise HTTPException(404, "not found")
    return {**_row(m, db.scalar(select(Score).where(Score.message_id == message_id)), user.email), "snippet": m.snippet,
            "preview": m.body_clean[:600], "body": m.body_clean[:20000], "size_bytes": m.size_bytes,
            "sender_email": m.sender_email}


# ---------- permanent delete of mail already in Trash ----------
CONFIRM_PHRASE = "DELETE FOREVER"


@app.get("/trash")
def trash_info(db: Session = Depends(get_db), user: auth.Principal = Depends(auth.current_user)):
    return {**trash_summary(db), "enabled": settings.allow_permanent_delete, "confirm_phrase": CONFIRM_PHRASE}


class PurgeIn(BaseModel):
    confirm: str
    message_ids: list[str] | None = None  # omit = everything currently in Trash


@app.post("/trash/purge")
def trash_purge(body: PurgeIn, db: Session = Depends(get_db), gmail: GmailClient = Depends(get_gmail),
                user: auth.Principal = Depends(auth.current_user)):
    if not settings.allow_permanent_delete:
        raise HTTPException(403, "Permanent delete is turned off (set ALLOW_PERMANENT_DELETE=true and sign in again)")
    if body.confirm != CONFIRM_PHRASE:
        raise HTTPException(400, f'Type "{CONFIRM_PHRASE}" to confirm')
    try:
        return purge_trash(db, gmail, body.message_ids)
    except PurgeError as e:
        raise HTTPException(400, str(e))


# ---------- digest ----------
@app.get("/digest")
def digest(db: Session = Depends(get_db), user: auth.Principal = Depends(auth.current_user)):
    return {"needs_action": needs_action(10, db, user), "groups": sorted(groups(db), key=lambda g: -g["count"])[:10]}
