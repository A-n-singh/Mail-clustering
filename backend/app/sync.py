from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .cleaning import clean_body
from .gmail import GmailClient, RawMail, parse_sender
from . import control
from .capacity import enforce_cap, enforce_user_cap
from .cleanup import delete_messages
from .sync_store import store_raw
from .grouping import regroup
from .models import Message, MessageCluster, Score, SyncRun, SyncState
from .scoring import MODEL_VERSION, score_message


def _remove(db: Session, ids: list[str]):
    delete_messages(db, ids)


def sync_in_progress(db: Session | None = None) -> bool:
    """True only while a sync is really running in this process (not just an unfinished row left by a restart)."""
    return control.sync_active.is_set()


def close_orphan_runs(db: Session) -> int:
    """Mark sync/topic runs that were cut off by a restart as finished, so nothing looks stuck 'running'."""
    from .models import TopicRun
    n = 0
    now_ = datetime.now(timezone.utc)
    for r in db.scalars(select(SyncRun).where(SyncRun.ended.is_(None))):
        r.ended, r.error = now_, control.STOPPED
        n += 1
    for r in db.scalars(select(TopicRun).where(TopicRun.ended.is_(None))):
        r.ended, r.error = now_, "interrupted by a restart"
        n += 1
    db.commit()
    return n


def _cap(db: Session, cap: int, user_cap: int | None):
    enforce_cap(db, cap)
    if user_cap:
        enforce_user_cap(db, user_cap)


def run_sync(db: Session, gmail: GmailClient, initial_fetch: int = 3000, cap: int = 360,
             user_cap: int | None = None, batch_size: int = 25, on_batch=None) -> SyncRun:
    """on_batch(db, mails_read_so_far) runs after every `batch_size` mails, so topics can grow while a long load is still going."""
    control.cancel_sync.clear()  # a Stop pressed for an earlier run must not cancel this one
    run = SyncRun()
    db.add(run)
    db.commit()
    done = 0
    try:
        state = db.get(SyncState, 1) or SyncState(id=1)
        new_history = gmail.profile_history_id()
        changes = gmail.history_changes(state.history_id) if state.history_id else None
        if changes is None:  # first run, or history ID expired -> full (re)sync
            ids, deleted = gmail.search("-in:trash -in:spam", initial_fetch)[0], []
            if not state.history_id:  # an interrupted FIRST sync: skip mail already stored so it resumes where it stopped
                have = set(db.scalars(select(Message.message_id)))
                ids = [i for i in ids if i not in have]
            # (expired history: re-read everything, so labels changed elsewhere are refreshed)
        else:
            ids, deleted = changes
        run.total = len(ids)
        db.commit()
        for n, mid in enumerate(ids, 1):
            if control.cancel_sync.is_set():
                raise control.SyncCancelled()
            store_raw(db, gmail.get_message(mid))
            done = n
            if n % 25 == 0:
                run.fetched = n
                if n == 100 or n % 500 == 0:
                    regroup(db)  # group as we go, so Groups/Grouped fill in during a long first sync
                db.commit()  # lets /sync/status show progress
            if on_batch and n % batch_size == 0:
                db.commit()
                try:
                    on_batch(db, n)
                except Exception:  # topic trouble (e.g. model not downloaded yet) must never stop the mail from loading
                    db.rollback()
        if deleted:
            _remove(db, deleted)
        run.fetched = len(ids)
        regroup(db)
        _cap(db, cap, user_cap)
        state.history_id, state.last_sync = new_history, datetime.now(timezone.utc)
        db.merge(state)
    except control.SyncCancelled:  # the owner pressed Stop: keep everything read so far, leave history_id so Sync resumes
        db.commit()
        regroup(db)
        _cap(db, cap, user_cap)
        run = db.merge(run)
        run.fetched, run.error, run.ended = done, control.STOPPED, datetime.now(timezone.utc)
        db.commit()
        return run
    except Exception as e:  # leave history_id untouched so the next run resumes
        try:
            db.commit()  # keep every mail already read (a Gmail error is not a database error)
        except Exception:
            db.rollback()
        try:  # show what we did manage to read, instead of an empty dashboard
            regroup(db)
            _cap(db, cap, user_cap)
            db.commit()
        except Exception:
            db.rollback()
        run = db.merge(run)
        run.error = str(e)
        run.ended = datetime.now(timezone.utc)
        db.commit()
        raise
    run.ended = datetime.now(timezone.utc)
    db.commit()
    return run
