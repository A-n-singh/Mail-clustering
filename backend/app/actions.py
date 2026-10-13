import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from .gmail import GmailClient
from .models import Action, Message, Score

# action -> (labels to add, labels to remove)
LABEL_ACTIONS = {
    "star": (["STARRED"], []),
    "unstar": ([], ["STARRED"]),
    "archive": ([], ["INBOX"]),
    "mark_read": ([], ["UNREAD"]),
    "mark_unread": (["UNREAD"], []),
    "mark_important": (["IMPORTANT"], []),
}
ALL_ACTIONS = set(LABEL_ACTIONS) | {"trash", "label"}
PROTECTED = {"urgent", "needs_reply"}


class ActionError(Exception):
    pass


def _apply(db: Session, gmail: GmailClient, m: Message, action: str, param: str | None, batch: str):
    db.add(Action(message_id=m.message_id, action=action, param=param,
                  previous_labels=list(m.labels), batch_id=batch))
    db.flush()  # log first, then call Gmail
    labels = list(m.labels)
    if action == "trash":
        gmail.trash(m.message_id)
        labels = [x for x in labels if x != "INBOX"] + ["TRASH"]
        m.in_trash = True
    elif action == "label":
        if not param:
            raise ActionError("label name required")
        lid = gmail.ensure_label(param)
        gmail.modify_labels(m.message_id, [lid], [])
        labels.append(lid)
    else:
        add, rem = LABEL_ACTIONS[action]
        gmail.modify_labels(m.message_id, add, rem)
        labels = [x for x in labels if x not in rem] + [x for x in add if x not in labels]
    m.labels = labels  # keep local DB in sync so the dashboard is never stale


def perform(db: Session, gmail: GmailClient, message_ids: list[str], action: str,
            param: str | None = None, confirmed: bool = False) -> str:
    if action not in ALL_ACTIONS:
        raise ActionError(f"unknown action {action}")
    msgs = [db.get(Message, i) for i in message_ids]
    if any(m is None for m in msgs):
        raise ActionError("unknown message")
    if len(msgs) > 1:
        if not confirmed:
            raise ActionError("bulk actions need confirmation")
        classes = {s.message_id: s.cls for s in db.scalars(
            select(Score).where(Score.message_id.in_(message_ids)))}
        if any(classes.get(i) in PROTECTED for i in message_ids):
            raise ActionError("bulk actions are blocked on urgent / needs-reply mail")
    batch = uuid.uuid4().hex
    for m in msgs:
        _apply(db, gmail, m, action, param, batch)
    db.commit()
    return batch


def bulk_preview(db: Session, message_ids: list[str]) -> dict:
    msgs = [db.get(Message, i) for i in message_ids]
    classes = {s.message_id: s.cls for s in db.scalars(
        select(Score).where(Score.message_id.in_(message_ids)))}
    return {"count": len(message_ids),
            "first_subjects": [m.subject for m in msgs[:5] if m],
            "blocked": any(classes.get(i) in PROTECTED for i in message_ids)}


def undo(db: Session, gmail: GmailClient, batch_id: str) -> int:
    rows = list(db.scalars(select(Action).where(Action.batch_id == batch_id, Action.undone == False)))  # noqa: E712
    for a in rows:
        m = db.get(Message, a.message_id)
        if m is None:
            continue
        if a.action == "trash":
            gmail.untrash(m.message_id)
        cur, prev = set(m.labels), set(a.previous_labels)
        add, rem = list(prev - cur - {"TRASH"}), list(cur - prev - {"TRASH"})
        if add or rem:
            gmail.modify_labels(m.message_id, add, rem)
        m.labels = list(a.previous_labels)
        m.in_trash = "TRASH" in m.labels
        a.undone = True
    db.commit()
    return len(rows)
