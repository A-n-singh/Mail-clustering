from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, LargeBinary, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def now():
    return datetime.now(timezone.utc)


class Owner(Base):
    """The one signed-in Google account and its encrypted refresh token."""
    __tablename__ = "owner"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True)
    refresh_token_enc: Mapped[str] = mapped_column(Text)


class Message(Base):
    __tablename__ = "messages"
    message_id: Mapped[str] = mapped_column(String, primary_key=True)
    thread_id: Mapped[str] = mapped_column(String)
    sender: Mapped[str] = mapped_column(String, default="")
    sender_email: Mapped[str] = mapped_column(String, default="")
    sender_domain: Mapped[str] = mapped_column(String, default="", index=True)
    subject_key: Mapped[str] = mapped_column(String, default="", index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    in_trash: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    subject: Mapped[str] = mapped_column(String, default="")
    body_clean: Mapped[str] = mapped_column(Text, default="")
    snippet: Mapped[str] = mapped_column(String, default="")
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    labels: Mapped[list] = mapped_column(JSON, default=list)
    is_bulk: Mapped[bool] = mapped_column(Boolean, default=False)
    list_unsubscribe: Mapped[str | None] = mapped_column(String, nullable=True)
    gmail_link: Mapped[str] = mapped_column(String, default="")


class Score(Base):
    __tablename__ = "scores"
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.message_id"), primary_key=True)
    model_version: Mapped[str] = mapped_column(String, primary_key=True)
    cls: Mapped[str] = mapped_column(String)  # urgent | needs_reply | fyi | junk
    importance_score: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String)
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Cluster(Base):
    """Phase 1: one row per sender group. Phase 2 reuses it for content clusters."""
    __tablename__ = "clusters"
    cluster_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(String, unique=True)
    summary: Mapped[str] = mapped_column(String, default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    run_id: Mapped[str] = mapped_column(String, default="sender")
    gmail_query: Mapped[str] = mapped_column(String, default="")  # used to refill from Gmail
    gmail_total: Mapped[int | None] = mapped_column(Integer, nullable=True)  # true count in Gmail


class MessageCluster(Base):
    __tablename__ = "message_cluster"
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.message_id"), primary_key=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("clusters.cluster_id"))
    is_noise: Mapped[bool] = mapped_column(Boolean, default=False)


class Action(Base):
    __tablename__ = "actions"
    action_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(String, index=True)
    action: Mapped[str] = mapped_column(String)
    param: Mapped[str | None] = mapped_column(String, nullable=True)
    previous_labels: Mapped[list] = mapped_column(JSON, default=list)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    batch_id: Mapped[str] = mapped_column(String, index=True)
    undone: Mapped[bool] = mapped_column(Boolean, default=False)


class SyncState(Base):
    __tablename__ = "sync_state"
    id: Mapped[int] = mapped_column(primary_key=True)
    history_id: Mapped[str | None] = mapped_column(String, nullable=True)
    last_sync: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SyncRun(Base):
    __tablename__ = "sync_runs"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    started: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    ended: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)  # for the onboarding progress bar
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------- Phase 2: topic clusters ----------------
class Embedding(Base):
    """One local embedding per mail (float32 bytes). Built from sender name + subject + snippet."""
    __tablename__ = "embeddings"
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.message_id"), primary_key=True)
    model: Mapped[str] = mapped_column(String)
    vector: Mapped[bytes] = mapped_column(LargeBinary)


class Topic(Base):
    __tablename__ = "topics"
    topic_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(String, default="")
    summary: Mapped[str] = mapped_column(String, default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MessageTopic(Base):
    """Topic membership + stored 2D map coordinates, so the dashboard only reads them (no live UMAP)."""
    __tablename__ = "message_topic"
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.message_id"), primary_key=True)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.topic_id"), nullable=True)
    x: Mapped[float] = mapped_column(Float)
    y: Mapped[float] = mapped_column(Float)
    is_noise: Mapped[bool] = mapped_column(Boolean, default=False)


class TopicRun(Base):
    __tablename__ = "topic_runs"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    started: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    ended: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    messages: Mapped[int] = mapped_column(Integer, default=0)
    topics: Mapped[int] = mapped_column(Integer, default=0)
    noise: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
