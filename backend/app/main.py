from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import actions as act
from . import auth
from .config import settings
from .db import Base, engine, get_db
from .gmail import GmailClient, GoogleGmailClient
from .models import Action, Cluster, Message, MessageCluster, Owner, Score, SyncRun
from .security import decrypt, encrypt
from .sync import run_sync

app = FastAPI(title="Gmail Smart Inbox")
app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_url],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def _init():
    Base.metadata.create_all(engine)


def get_gmail(db: Session = Depends(get_db)) -> GmailClient:
    from google.oauth2.credentials import Credentials
    owner = db.scalar(select(Owner))
    if not owner:
        raise HTTPException(409, "Gmail not connected")
    creds = Credentials(None, refresh_token=decrypt(owner.refresh_token_enc),
                        token_uri="https://oauth2.googleapis.com/token",
                        client_id=settings.google_client_id, client_secret=settings.google_client_secret)
    return GoogleGmailClient(creds)


# ---------- sign-in ----------
@app.get("/auth/login")
def login():
    flow = auth.build_flow()
    url, _ = flow.authorization_url(access_type="offline", prompt="consent")
    return RedirectResponse(url)


@app.get("/auth/callback")
def callback(code: str, db: Session = Depends(get_db)):
    from google.auth.transport.requests import Request
    from google.oauth2 import id_token
    flow = auth.build_flow()
    flow.fetch_token(code=code)
    info = id_token.verify_oauth2_token(flow.credentials.id_token, Request(), settings.google_client_id)
    email = info["email"]
    if not auth.is_allowed(email):
        raise HTTPException(403, "This account is not allowed")
    if flow.credentials.refresh_token:
        db.merge(Owner(id=1, email=email, refresh_token_enc=encrypt(flow.credentials.refresh_token)))
        db.commit()
    resp = RedirectResponse(settings.frontend_url)
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
            httpx.post("https://oauth2.googleapis.com/revoke", params={"token": decrypt(owner.refresh_token_enc)})
        except Exception:
            pass
    for t in reversed(Base.metadata.sorted_tables):
        db.execute(t.delete())
    db.commit()
    return {"ok": True}


# ---------- sync ----------
@app.post("/sync")
def sync(db: Session = Depends(get_db), gmail: GmailClient = Depends(get_gmail),
         _: str = Depends(auth.current_user)):
    run = run_sync(db, gmail, settings.backfill_days)
    return {"fetched": run.fetched}


@app.get("/sync/status")
def sync_status(db: Session = Depends(get_db), _: str = Depends(auth.current_user)):
    run = db.scalar(select(SyncRun).order_by(SyncRun.id.desc()))
    return {"running": bool(run and run.ended is None), "fetched": run.fetched if run else 0,
            "error": run.error if run else None}


# ---------- dashboard data ----------
def _row(m: Message, s: Score | None):
    return {"message_id": m.message_id, "sender": m.sender, "subject": m.subject,
            "date": m.date.isoformat(), "labels": m.labels, "gmail_link": m.gmail_link,
            "list_unsubscribe": m.list_unsubscribe, "class": s.cls if s else None,
            "score": s.importance_score if s else None, "reason": s.reason if s else None}


@app.get("/summary")
def summary(db: Session = Depends(get_db), _: str = Depends(auth.current_user)):
    total = db.scalar(select(func.count()).select_from(Message))
    grouped = db.scalar(select(func.count()).select_from(MessageCluster).where(MessageCluster.is_noise == False))  # noqa: E712
    review = db.scalar(select(func.count()).select_from(Score).where(Score.cls.in_(["urgent", "needs_reply"])))
    return {"total": total, "grouped": grouped, "needs_review": review}


@app.get("/needs-action")
def needs_action(limit: int = 50, db: Session = Depends(get_db), _: str = Depends(auth.current_user)):
    q = (select(Message, Score).join(Score, Score.message_id == Message.message_id)
         .where(Score.cls.in_(["urgent", "needs_reply"])).order_by(Score.importance_score.desc(), Message.date.desc())
         .limit(limit))
    return [_row(m, s) for m, s in db.execute(q)]


@app.get("/groups")
def groups(db: Session = Depends(get_db), _: str = Depends(auth.current_user),
           since_days: int | None = None, min_score: int = 0):
    """Bubble data: size = mail count, color = average importance."""
    q = (select(Cluster.cluster_id, Cluster.label, func.count(Message.message_id), func.avg(Score.importance_score))
         .join(MessageCluster, MessageCluster.cluster_id == Cluster.cluster_id)
         .join(Message, Message.message_id == MessageCluster.message_id)
         .join(Score, Score.message_id == Message.message_id)
         .group_by(Cluster.cluster_id, Cluster.label))
    if since_days:
        q = q.where(Message.date >= datetime.now(timezone.utc) - timedelta(days=since_days))
    if min_score:
        q = q.where(Score.importance_score >= min_score)
    return [{"cluster_id": i, "label": l, "count": c, "importance": round(a or 0)} for i, l, c, a in db.execute(q)]


@app.get("/groups/{cluster_id}/messages")
def group_messages(cluster_id: int, sender: str | None = None, label: str | None = None,
                   db: Session = Depends(get_db), _: str = Depends(auth.current_user)):
    q = (select(Message, Score).join(MessageCluster, MessageCluster.message_id == Message.message_id)
         .outerjoin(Score, Score.message_id == Message.message_id)
         .where(MessageCluster.cluster_id == cluster_id).order_by(Message.date.desc()))
    if sender:
        q = q.where(Message.sender == sender)
    rows = [_row(m, s) for m, s in db.execute(q)]
    return [r for r in rows if not label or label in r["labels"]]


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
        return {"batch_id": act.perform(db, gmail, body.message_ids, body.action, body.param, body.confirmed)}
    except act.ActionError as e:
        raise HTTPException(400, str(e))


@app.post("/actions/{batch_id}/undo")
def undo(batch_id: str, db: Session = Depends(get_db), gmail: GmailClient = Depends(get_gmail),
         _: str = Depends(auth.current_user)):
    return {"undone": act.undo(db, gmail, batch_id)}


@app.get("/actions/log")
def action_log(db: Session = Depends(get_db), _: str = Depends(auth.current_user)):
    rows = db.scalars(select(Action).order_by(Action.action_id.desc()).limit(100))
    return [{"batch_id": a.batch_id, "message_id": a.message_id, "action": a.action,
             "timestamp": a.timestamp.isoformat(), "undone": a.undone} for a in rows]


# ---------- digest ----------
@app.get("/digest")
def digest(db: Session = Depends(get_db), _: str = Depends(auth.current_user)):
    return {"needs_action": needs_action(10, db), "groups": sorted(groups(db), key=lambda g: -g["count"])[:10]}
