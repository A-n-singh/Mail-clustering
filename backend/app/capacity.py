"""Each cluster keeps at most `cap` live mails. Eviction only drops the LOCAL copy; Gmail is untouched.
When mails leave a cluster (trash/archive), older ones are pulled from Gmail to fill it back up."""
from datetime import timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .cleanup import delete_messages
from .gmail import GmailClient
from .grouping import regroup
from .models import Cluster, Message, MessageCluster, Score
from .sync_store import store_raw

PROTECTED = ("urgent", "needs_reply")


def _live(cluster_id: int):
    return (select(Message).join(MessageCluster, MessageCluster.message_id == Message.message_id)
            .where(MessageCluster.cluster_id == cluster_id, Message.in_trash == False))  # noqa: E712


def enforce_cap(db: Session, cap: int):
    for c in db.scalars(select(Cluster)):
        rows = list(db.execute(
            select(Message.message_id, Message.date, Score.cls, Score.importance_score)
            .join(MessageCluster, MessageCluster.message_id == Message.message_id)
            .outerjoin(Score, Score.message_id == Message.message_id)
            .where(MessageCluster.cluster_id == c.cluster_id, Message.in_trash == False)))  # noqa: E712
        excess = len(rows) - cap
        if excess > 0:
            # least important first, then oldest; urgent / needs-reply are never evicted
            cand = sorted((r for r in rows if r.cls not in PROTECTED), key=lambda r: (r.importance_score or 0, r.date))
            ids = [r.message_id for r in cand[:excess]]
            delete_messages(db, ids)
        c.size = db.scalar(select(func.count()).select_from(MessageCluster)
                           .join(Message, Message.message_id == MessageCluster.message_id)
                           .where(MessageCluster.cluster_id == c.cluster_id, Message.in_trash == False))  # noqa: E712
    db.flush()


def refill(db: Session, gmail: GmailClient, cluster_id: int, cap: int) -> int:
    """Top the cluster back up to `cap` with the next-older mails from the same sender."""
    c = db.get(Cluster, cluster_id)
    if not c or not c.gmail_query:
        return 0
    live = list(db.scalars(_live(cluster_id)))
    ids, total = gmail.search(c.gmail_query + " -in:trash -in:spam", cap + 50)
    c.gmail_total = total
    need = cap - len(live)
    if need <= 0:
        return 0
    known = set(db.scalars(select(Message.message_id).where(Message.message_id.in_(ids))))
    added = 0
    for mid in ids:
        if added >= need:
            break
        if mid in known:
            continue
        store_raw(db, gmail.get_message(mid))
        added += 1
    if added:
        regroup(db)
        enforce_cap(db, cap)
    db.commit()
    return added


def _live_rows(db: Session):
    return list(db.execute(
        select(Message.message_id, Message.date, Score.cls, Score.importance_score)
        .outerjoin(Score, Score.message_id == Message.message_id)
        .where(Message.in_trash == False)))  # noqa: E712


def enforce_user_cap(db: Session, cap: int) -> int:
    """The whole mailbox keeps at most `cap` live mails locally (Gmail is untouched). Least important first, then oldest;
    urgent / needs-reply are never evicted, so a mailbox full of those can stay above the cap."""
    rows = _live_rows(db)
    excess = len(rows) - cap
    if excess <= 0:
        return 0
    cand = sorted((r for r in rows if r.cls not in PROTECTED), key=lambda r: (r.importance_score or 0, r.date))
    ids = [r.message_id for r in cand[:excess]]
    delete_messages(db, ids)
    regroup(db)  # group sizes and the group list follow the mail that is left
    return len(ids)


def refill_user(db: Session, gmail: GmailClient, cap: int, limit: int | None = None) -> int:
    """Top the mailbox back up towards `cap` with mail OLDER than the oldest one kept (so evicted newer mail never comes back
    and the cap cannot churn). `limit` bounds one step so the caller can work in small batches. Returns how many were added."""
    rows = _live_rows(db)
    need = cap - len(rows)
    if limit is not None:
        need = min(need, limit)
    if need <= 0:
        return 0
    query = "-in:trash -in:spam"
    if rows:
        oldest = min(r.date for r in rows)
        oldest = oldest if oldest.tzinfo else oldest.replace(tzinfo=timezone.utc)
        query += f" before:{int(oldest.timestamp())}"
    ids, _ = gmail.search(query, need + 100)
    known = set(db.scalars(select(Message.message_id).where(Message.message_id.in_(ids))))
    added = 0
    for mid in ids:
        if added >= need:
            break
        if mid in known:
            continue
        store_raw(db, gmail.get_message(mid))
        added += 1
    if added:
        regroup(db)
    db.flush()
    return added


def refill_user_in_batches(db: Session, gmail: GmailClient, cap: int, batch: int, on_batch=None, cancelled=lambda: False) -> int:
    """refill_user() `batch` mails at a time; `on_batch(db, total_added)` runs after each step so the screen can grow."""
    total = 0
    while not cancelled():
        n = refill_user(db, gmail, cap, batch)
        if not n:
            break
        total += n
        db.commit()
        if on_batch:
            on_batch(db, total)
    return total
