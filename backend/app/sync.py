from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .cleaning import clean_body
from .gmail import GmailClient, RawMail, parse_sender
from .capacity import enforce_cap
from .sync_store import store_raw
from .grouping import regroup
from .models import Message, MessageCluster, Score, SyncRun, SyncState
from .scoring import MODEL_VERSION, score_message


def _remove(db: Session, ids: list[str]):
    for table in (Score, MessageCluster):
        db.execute(delete(table).where(table.message_id.in_(ids)))
    db.execute(delete(Message).where(Message.message_id.in_(ids)))


def run_sync(db: Session, gmail: GmailClient, initial_fetch: int = 3000, cap: int = 360) -> SyncRun:
    run = SyncRun()
    db.add(run)
    db.commit()
    try:
        state = db.get(SyncState, 1) or SyncState(id=1)
        new_history = gmail.profile_history_id()
        changes = gmail.history_changes(state.history_id) if state.history_id else None
        if changes is None:  # first run, or history ID expired -> full (re)sync
            ids, deleted = gmail.search("-in:trash -in:spam", initial_fetch)[0], []
        else:
            ids, deleted = changes
        for mid in ids:
            store_raw(db, gmail.get_message(mid))
        if deleted:
            _remove(db, deleted)
        run.fetched = len(ids)
        regroup(db)
        enforce_cap(db, cap)
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
