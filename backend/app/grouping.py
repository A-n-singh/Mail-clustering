import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Cluster, Message, MessageCluster

FREE_MAIL = {"gmail.com", "googlemail.com", "yahoo.com", "outlook.com", "hotmail.com", "live.com",
             "icloud.com", "proton.me", "protonmail.com", "aol.com"}
_PREFIX = re.compile(r"^((re|fwd?|fw)\s*:\s*)+", re.I)


def subject_key(subject: str) -> str:
    """Subject template: 'Your order #12345 has shipped' -> 'your order # has shipped'."""
    s = _PREFIX.sub("", subject.strip().lower())
    s = re.sub(r"#?\b[\w.-]*\d[\w.-]*\b", "#", s)  # anything containing a digit (ids, dates, amounts)
    return re.sub(r"\s+", " ", s).strip()[:120]


def group_key(email: str, domain: str, sender: str) -> tuple[str, str]:
    """-> (cluster label, gmail query). Companies group by domain; people at free-mail by address."""
    if domain and domain not in FREE_MAIL:
        return domain, f"from:{domain}"
    if email:
        return email, f"from:{email}"
    return sender or "unknown", ""


def regroup(db: Session):
    clusters = {c.label: c for c in db.scalars(select(Cluster))}
    counts: dict[str, int] = {}
    for m in db.scalars(select(Message)):
        label, query = group_key(m.sender_email, m.sender_domain, m.sender)
        c = clusters.get(label)
        if c is None:
            c = Cluster(label=label, summary=f"Mail from {label}", gmail_query=query)
            db.add(c)
            db.flush()
            clusters[label] = c
        db.merge(MessageCluster(message_id=m.message_id, cluster_id=c.cluster_id, is_noise=False))
        if not m.in_trash:
            counts[label] = counts.get(label, 0) + 1
    for label, c in clusters.items():
        c.size = counts.get(label, 0)
    db.flush()
