"""Permanent deletion of mail that is already in Trash. Irreversible, so it is guarded:
  * only mail the app knows is in Trash, re-checked in Gmail right before deleting
  * every purge is written to the action log before Gmail is called
  * the typed confirmation phrase is enforced by the API layer
"""
import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .cleanup import delete_messages
from .gmail import GmailClient
from .grouping import regroup
from .models import Action, Message, MessageCluster, Score


class PurgeError(Exception):
    pass


def trash_summary(db: Session) -> dict:
    n, b = db.execute(select(func.count(), func.coalesce(func.sum(Message.size_bytes), 0))
                      .where(Message.in_trash == True)).one()  # noqa: E712
    return {"count": n, "bytes": b}


def purge_trash(db: Session, gmail: GmailClient, message_ids: list[str] | None = None) -> dict:
    q = select(Message).where(Message.in_trash == True)  # noqa: E712
    if message_ids is not None:
        q = q.where(Message.message_id.in_(message_ids))
    msgs = list(db.scalars(q))
    if message_ids is not None and len(msgs) != len(set(message_ids)):
        raise PurgeError("some of those mails are not in Trash")
    # Re-check Gmail: the mail may have been restored (e.g. by Undo or in Gmail itself) since we last looked.
    safe = []
    for m in msgs:
        if "TRASH" in gmail.get_message(m.message_id).labels:
            safe.append(m)
    batch = uuid.uuid4().hex
    for m in safe:  # log first, then delete
        db.add(Action(message_id=m.message_id, action="purge", previous_labels=list(m.labels), batch_id=batch, undone=False))
    db.flush()
    ids = [m.message_id for m in safe]
    if ids:
        gmail.delete_permanently(ids)
        delete_messages(db, ids)
        regroup(db)
    db.commit()
    return {"deleted": len(ids), "bytes_freed": sum(m.size_bytes for m in safe),
            "skipped_restored": len(msgs) - len(safe)}
