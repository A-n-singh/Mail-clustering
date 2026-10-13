from sqlalchemy.orm import Session

from .cleaning import clean_body
from .gmail import RawMail, parse_sender
from .grouping import subject_key
from . import llm
from .models import Message, Score
from .scoring import MODEL_VERSION, score_message


def store_raw(db: Session, raw: RawMail):
    sender, email, domain = parse_sender(raw.sender)
    body = clean_body(raw.body, raw.is_html)
    bulk = bool(raw.list_unsubscribe) or raw.headers.get("precedence", "").lower() in ("bulk", "list")
    link = f"https://mail.google.com/mail/u/0/#inbox/{raw.thread_id}"
    m = db.get(Message, raw.message_id) or Message(message_id=raw.message_id)
    m.thread_id, m.sender, m.sender_email, m.sender_domain, m.subject = raw.thread_id, sender, email, domain, raw.subject
    m.subject_key, m.size_bytes, m.in_trash = subject_key(raw.subject), raw.size_estimate, "TRASH" in raw.labels
    m.body_clean, m.snippet, m.date, m.labels = body, raw.snippet, raw.date, raw.labels
    m.is_bulk, m.list_unsubscribe, m.gmail_link = bulk, raw.list_unsubscribe, link
    db.merge(m)  # idempotent: re-running never duplicates rows
    cls, s, reason = score_message(sender, raw.subject, body, bulk, raw.labels)
    if cls == "fyi" and not bulk:  # rules were not decisive -> optional LLM second opinion
        refined = llm.classify(db, sender, raw.subject, body)
        if refined:
            cls, s, reason = refined
    db.merge(Score(message_id=raw.message_id, model_version=MODEL_VERSION,
                   cls=cls, importance_score=s, reason=reason))
