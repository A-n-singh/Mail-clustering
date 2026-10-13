from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .cleaning import clean_body
from .gmail import GmailClient, RawMail, parse_sender
from .grouping import regroup
from .models import Message, MessageCluster, Score, SyncRun, SyncState
from .scoring import MODEL_VERSION, score_message


def _store(db: Session, raw: RawMail):
    sender, domain = parse_sender(raw.sender)
    body = clean_body(raw.body, raw.is_html)
    bulk = bool(raw.list_unsubscribe) or raw.headers.get("precedence", "").lower() in ("bulk", "list")
    link = f"https://mail.google.com/mail/u/0/#inbox/{raw.thread_id}"
    m = db.get(Message, raw.message_id) or Message(message_id=raw.message_id)
    m.thread_id, m.sender, m.sender_domain, m.subject = raw.thread_id, sender, domain, raw.subject
    m.body_clean, m.snippet, m.date, m.labels = body, raw.snippet, raw.date, raw.labels
    m.is_bulk, m.list_unsubscribe, m.gmail_link = bulk, raw.list_unsubscribe, link
    db.merge(m)  # idempotent: re-running never duplicates rows
    cls, s, reason = score_message(sender, raw.subject, body, bulk, raw.labels)
    db.merge(Score(message_id=raw.message_id, model_version=MODEL_VERSION,
                   cls=cls, importance_score=s, reason=reason))


def _remove(db: Session, ids: list[str]):
    for table in (Score, MessageCluster):
        db.execute(delete(table).where(table.message_id.in_(ids)))
    db.execute(delete(Message).where(Message.message_id.in_(ids)))


def run_sync(db: Session, gmail: GmailClient, backfill_days: int = 90) -> SyncRun:
    run = SyncRun()
    db.add(run)
    db.commit()
    try:
        state = db.get(SyncState, 1) or SyncState(id=1)
        new_history = gmail.profile_history_id()
        changes = gmail.history_changes(state.history_id) if state.history_id else None
        if changes is None:  # first run, or history ID expired -> full (re)sync
            ids, deleted = gmail.list_message_ids(backfill_days), []
        else:
            ids, deleted = changes
        for mid in ids:
            _store(db, gmail.get_message(mid))
        if deleted:
            _remove(db, deleted)
        run.fetched = len(ids)
        regroup(db)
        state.history_id, state.last_sync = new_history, datetime.now(timezone.utc)
        db.merge(state)
    except Exception as e:  # leave history_id untouched so the next run resumes
        db.rollback()
        run = db.merge(run)
        run.error = str(e)
        run.ended = datetime.now(timezone.utc)
        db.commit()
        raise
    run.ended = datetime.now(timezone.utc)
    db.commit()
    return run
