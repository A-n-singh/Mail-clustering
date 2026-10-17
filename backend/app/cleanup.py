from sqlalchemy import delete
from sqlalchemy.orm import Session

from .models import Embedding, Message, MessageCluster, MessageTopic, Score


def delete_messages(db: Session, ids: list[str]):
    """Remove local mail and everything that points at it (needed for foreign keys on Postgres)."""
    if not ids:
        return
    for t in (Score, MessageCluster, MessageTopic, Embedding):
        db.execute(delete(t).where(t.message_id.in_(ids)))
    db.execute(delete(Message).where(Message.message_id.in_(ids)))
