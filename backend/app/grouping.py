from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Cluster, Message, MessageCluster


def group_label(sender: str, domain: str) -> str:
    return domain or sender or "unknown"


def regroup(db: Session):
    """Phase 1 grouping: one cluster per sender domain (falls back to sender name)."""
    clusters = {c.label: c for c in db.scalars(select(Cluster))}
    counts: dict[str, int] = {}
    for m in db.scalars(select(Message)):
        label = group_label(m.sender, m.sender_domain)
        c = clusters.get(label)
        if c is None:
            c = Cluster(label=label, summary=f"Mail from {label}")
            db.add(c)
            db.flush()
            clusters[label] = c
        db.merge(MessageCluster(message_id=m.message_id, cluster_id=c.cluster_id, is_noise=False))
        counts[label] = counts.get(label, 0) + 1
    for label, c in clusters.items():
        c.size = counts.get(label, 0)
    db.flush()
