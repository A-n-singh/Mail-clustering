from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
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
    sender_domain: Mapped[str] = mapped_column(String, default="", index=True)
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


class MessageCluster(Base):
    __tablename__ = "message_cluster"
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.message_id"), primary_key=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("clusters.cluster_id"))
    umap_x: Mapped[float | None] = mapped_column(Float, nullable=True)  # Phase 2
    umap_y: Mapped[float | None] = mapped_column(Float, nullable=True)  # Phase 2
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
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
