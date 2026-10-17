import asyncio
import hmac
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import String, func, select
from sqlalchemy.orm import Session

from . import actions as act
from . import auth
from .config import settings
from .db import Base, SessionLocal, engine, get_db
from .gmail import GmailClient, GoogleGmailClient
from .models import Action, Cluster, Message, MessageCluster, MessageTopic, Owner, Score, SyncRun, Topic, TopicRun
from .security import decrypt, encrypt
from .capacity import refill
from . import control
from . import topics as topics_mod
from .purge import PurgeError, purge_trash, trash_summary
from .sync import close_orphan_runs, run_sync, sync_in_progress

@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
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


def _sync_job(already_marked: bool = False):
    if not already_marked:
        if control.sync_active.is_set():
            return
        control.sync_active.set()
    try:
        with SessionLocal() as db:
            gmail = build_gmail(db)
            if not gmail:
                return
            try:
                run = run_sync(db, gmail, settings.initial_fetch, settings.cluster_cap)
            except Exception:
                return  # recorded in sync_runs.error; next run resumes
            if run.error == control.STOPPED:
                return  # the owner stopped it: do not start the (long) topic build afterwards
            try:
                topics_mod.after_sync(db)  # weekly re-cluster, otherwise assign new mail to existing topics
            except Exception:
                db.rollback()  # topic problems (e.g. model not downloaded yet) must never break syncing
    finally:
        control.sync_active.clear()


async def _scheduler():
    """Sync every SYNC_INTERVAL_MINUTES without blocking the app."""
    while True:
        await asyncio.sleep(settings.sync_interval_minutes * 60)
        await asyncio.to_thread(_sync_job)


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
def callback(code: str, state: str, request: Request, db: Session = Depends(get_db)):
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
    if not auth.is_allowed(email):
        raise HTTPException(403, "This account is not allowed")
    if flow.credentials.refresh_token:
        db.merge(Owner(id=1, email=email, refresh_token_enc=encrypt(flow.credentials.refresh_token)))
        db.commit()
    resp = RedirectResponse(settings.frontend_url)
    resp.delete_cookie(auth.OAUTH_COOKIE)
    resp.set_cookie(auth.COOKIE, auth.make_session(email), httponly=True, secure=settings.frontend_url.startswith("https"),
                    samesite="lax", max_age=auth.MAX_AGE)
    return resp


@app.post("/auth/logout")
def logout():
    resp = RedirectResponse(settings.frontend_url, status_code=303)
    resp.delete_cookie(auth.COOKIE)
    return resp


@app.get("/me")
def me(email: str = Depends(auth.current_user)):
    return {"email": email}


@app.delete("/account")
def delete_account(db: Session = Depends(get_db), _: str = Depends(auth.current_user)):
    """Revoke the Google token and erase every row."""
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
    return {"ok": True}


# ---------- sync ----------
@app.post("/sync")
def sync(background: BackgroundTasks, db: Session = Depends(get_db), _: str = Depends(auth.current_user),
         __: GmailClient = Depends(get_gmail)):
    """Start a sync in the background; poll /sync/status for progress."""
    if sync_in_progress(db):
        return {"started": False, "reason": "already running"}
    control.sync_active.set()  # claim it now so a double click cannot start two syncs
    background.add_task(_sync_job, True)
    return {"started": True}


@app.post("/sync/stop")
def sync_stop(db: Session = Depends(get_db), _: str = Depends(auth.current_user)):
    """Stop the running sync as soon as possible. Mail already read is kept; Sync now continues from there."""
    if not sync_in_progress(db):
        close_orphan_runs(db)  # heal a leftover "running" row from a restart
        return {"stopping": False, "reason": "no sync is running"}
    control.cancel_sync.set()
    return {"stopping": True}


@app.get("/sync/status")
def sync_status(db: Session = Depends(get_db), _: str = Depends(auth.current_user)):
    run = db.scalar(select(SyncRun).order_by(SyncRun.id.desc()))
    stopped = bool(run and run.error == control.STOPPED)
    return {"running": sync_in_progress(db), "fetched": run.fetched if run else 0,
            "total": run.total if run else 0, "error": None if stopped else (run.error if run else None),
            "stopped": stopped,
            "connected": db.scalar(select(Owner.id)) is not None,
            "last_sync": run.ended.isoformat() if run and run.ended else None}


# ---------- dashboard data ----------
def _row(m: Message, s: Score | None):
    return {"message_id": m.message_id, "sender": m.sender, "subject": m.subject,
            "date": m.date.isoformat(), "labels": m.labels, "gmail_link": m.gmail_link,
            "list_unsubscribe": m.list_unsubscribe, "class": s.cls if s else None,
            "score": s.importance_score if s else None, "reason": s.reason if s else None}


@app.get("/summary")
def summary(db: Session = Depends(get_db), _: str = Depends(auth.current_user)):
    live = Message.in_trash == False  # noqa: E712
    total = db.scalar(select(func.count()).select_from(Message).where(live))
    grouped = db.scalar(select(func.count()).select_from(MessageCluster).join(
        Message, Message.message_id == MessageCluster.message_id).where(MessageCluster.is_noise == False, live))  # noqa: E712
    review = db.scalar(select(func.count()).select_from(Score).join(
        Message, Message.message_id == Score.message_id).where(Score.cls.in_(["urgent", "needs_reply"]), live))
    return {"total": total, "grouped": grouped, "needs_review": review}


@app.get("/needs-action")
def needs_action(limit: int = 50, db: Session = Depends(get_db), _: str = Depends(auth.current_user)):
    q = (select(Message, Score).join(Score, Score.message_id == Message.message_id)
         .where(Score.cls.in_(["urgent", "needs_reply"]), Message.in_trash == False).order_by(Score.importance_score.desc(), Message.date.desc())
         .limit(limit))
    return [_row(m, s) for m, s in db.execute(q)]


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
def groups(db: Session = Depends(get_db), _: str = Depends(auth.current_user),
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


@app.get("/groups/{cluster_id}/subjects")
def group_subjects(cluster_id: int, db: Session = Depends(get_db), _: str = Depends(auth.current_user)):
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
def storage(db: Session = Depends(get_db), _: str = Depends(auth.current_user)):
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
                   min_score: int = 0, db: Session = Depends(get_db), _: str = Depends(auth.current_user)):
    q = (select(Message, Score).join(MessageCluster, MessageCluster.message_id == Message.message_id)
         .outerjoin(Score, Score.message_id == Message.message_id)
         .where(MessageCluster.cluster_id == cluster_id, Message.in_trash == False,  # noqa: E712
                *_filters(since_days, date_from, date_to, sender, label, min_score))
         .order_by(Message.date.desc()))
    return [_row(m, s) for m, s in db.execute(q)]


# ---------- actions ----------
class ActionIn(BaseModel):
    message_ids: list[str]
    action: str
    param: str | None = None
    confirmed: bool = False


@app.post("/actions/preview")
def preview(body: ActionIn, db: Session = Depends(get_db), _: str = Depends(auth.current_user)):
    return act.bulk_preview(db, body.message_ids)


@app.post("/actions")
def do_action(body: ActionIn, db: Session = Depends(get_db), gmail: GmailClient = Depends(get_gmail),
              _: str = Depends(auth.current_user)):
    try:
        batch = act.perform(db, gmail, body.message_ids, body.action, body.param, body.confirmed)
    except act.ActionError as e:
        raise HTTPException(400, str(e))
    refilled = 0
    if body.action == "trash":  # fill the cluster back up with older mail
        cids = set(db.scalars(select(MessageCluster.cluster_id).where(MessageCluster.message_id.in_(body.message_ids))))
        refilled = sum(refill(db, gmail, cid, settings.cluster_cap) for cid in cids)
    return {"batch_id": batch, "refilled": refilled}


@app.post("/actions/{batch_id}/undo")
def undo(batch_id: str, db: Session = Depends(get_db), gmail: GmailClient = Depends(get_gmail),
         _: str = Depends(auth.current_user)):
    return {"undone": act.undo(db, gmail, batch_id)}


@app.get("/actions/log")
def action_log(db: Session = Depends(get_db), _: str = Depends(auth.current_user)):
    rows = db.scalars(select(Action).order_by(Action.action_id.desc()).limit(100))
    return [{"batch_id": a.batch_id, "message_id": a.message_id, "action": a.action,
             "timestamp": a.timestamp.isoformat(), "undone": a.undone} for a in rows]


# ---------- Phase 2: topic clusters + map ----------
def _live_topic_rows(db: Session):
    return (select(MessageTopic, Message).join(Message, Message.message_id == MessageTopic.message_id)
            .where(Message.in_trash == False))  # noqa: E712


@app.get("/topics")
def topics(db: Session = Depends(get_db), _: str = Depends(auth.current_user)):
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
def cluster_map(db: Session = Depends(get_db), _: str = Depends(auth.current_user)):
    """Stored 2D coordinates only (no live UMAP). Compact keys: i=id, x, y, z, t=topic (null = review bucket), s=subject, f=sender."""
    rows = db.execute(select(MessageTopic.message_id, MessageTopic.x, MessageTopic.y, MessageTopic.z, MessageTopic.topic_id,
                             Message.subject, Message.sender)
                      .join(Message, Message.message_id == MessageTopic.message_id)
                      .where(Message.in_trash == False).limit(100_000))  # noqa: E712
    return {"points": [{"i": i, "x": round(x, 4), "y": round(y, 4), "z": round(z or 0, 4), "t": t, "s": s, "f": f}
                       for i, x, y, z, t, s, f in rows]}


@app.get("/topics/status")
def topics_status(db: Session = Depends(get_db), _: str = Depends(auth.current_user)):
    run = db.scalar(select(TopicRun).order_by(TopicRun.id.desc()))
    running = control.topics_active.is_set()
    return {"running": running, "topics": run.topics if run else 0, "noise": run.noise if run else 0,
            "messages": run.messages if run else 0, "error": run.error if run else None,
            "min_messages": settings.topic_min_messages,
            "last_run": run.ended.isoformat() if run and run.ended else None}


def _recluster_job():
    with SessionLocal() as db:
        try:
            topics_mod.recluster(db)
        except Exception:
            pass  # recorded in topic_runs.error


@app.post("/topics/recluster")
def topics_recluster(background: BackgroundTasks, db: Session = Depends(get_db), _: str = Depends(auth.current_user)):
    if topics_status(db)["running"]:
        return {"started": False, "reason": "already running"}
    background.add_task(_recluster_job)
    return {"started": True}


@app.get("/topics/{topic_id}/messages")
def topic_messages(topic_id: int, db: Session = Depends(get_db), _: str = Depends(auth.current_user)):
    """topic_id 0 = the review bucket (mail that fits no topic), most important first (FR-23)."""
    cond = MessageTopic.topic_id.is_(None) if topic_id == 0 else MessageTopic.topic_id == topic_id
    q = (select(Message, Score).join(MessageTopic, MessageTopic.message_id == Message.message_id)
         .outerjoin(Score, Score.message_id == Message.message_id)
         .where(cond, Message.in_trash == False)  # noqa: E712
         .order_by(Score.importance_score.desc(), Message.date.desc()).limit(500))
    return [_row(m, s) for m, s in db.execute(q)]


@app.get("/messages/{message_id}")
def message_detail(message_id: str, db: Session = Depends(get_db), _: str = Depends(auth.current_user)):
    m = db.get(Message, message_id)
    if not m:
        raise HTTPException(404, "not found")
    return {**_row(m, db.scalar(select(Score).where(Score.message_id == message_id))), "snippet": m.snippet,
            "preview": m.body_clean[:600]}


# ---------- permanent delete of mail already in Trash ----------
CONFIRM_PHRASE = "DELETE FOREVER"


@app.get("/trash")
def trash_info(db: Session = Depends(get_db), _: str = Depends(auth.current_user)):
    return {**trash_summary(db), "enabled": settings.allow_permanent_delete, "confirm_phrase": CONFIRM_PHRASE}


class PurgeIn(BaseModel):
    confirm: str
    message_ids: list[str] | None = None  # omit = everything currently in Trash


@app.post("/trash/purge")
def trash_purge(body: PurgeIn, db: Session = Depends(get_db), gmail: GmailClient = Depends(get_gmail),
                _: str = Depends(auth.current_user)):
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
def digest(db: Session = Depends(get_db), _: str = Depends(auth.current_user)):
    return {"needs_action": needs_action(10, db), "groups": sorted(groups(db), key=lambda g: -g["count"])[:10]}
