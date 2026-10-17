"""Local sentence embeddings (FR-19). The model runs on this machine; no mail text is sent anywhere.
Text = sender name + subject first, then a short snippet (not the whole body)."""
from typing import Protocol

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import Embedding, Message


class Embedder(Protocol):
    name: str
    def embed(self, texts: list[str]) -> np.ndarray:
        """-> (n, d) float32, rows L2-normalised."""


class FastEmbedder:
    """ONNX sentence model via fastembed; downloaded once (~130 MB) on first use."""

    def __init__(self, model_name: str):
        self.name = model_name
        self._model = None

    def embed(self, texts):
        if self._model is None:
            from fastembed import TextEmbedding
            self._model = TextEmbedding(self.name)
        v = np.array(list(self._model.embed(texts)), dtype=np.float32)
        return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)


_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = FastEmbedder(settings.embed_model)
    return _embedder


def set_embedder(e: Embedder | None):  # used by tests / alternative models
    global _embedder
    _embedder = e


def message_text(m: Message) -> str:
    return f"{m.sender}. {m.subject}. {(m.snippet or '')[:200]}"


def ensure_embeddings(db: Session, embedder: Embedder, batch: int = 64) -> int:
    """Embed every live mail that has no embedding yet."""
    todo = list(db.scalars(select(Message).outerjoin(Embedding, Embedding.message_id == Message.message_id)
                           .where(Embedding.message_id.is_(None), Message.in_trash == False)))  # noqa: E712
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        vecs = embedder.embed([message_text(m) for m in chunk])
        for m, v in zip(chunk, vecs):
            db.add(Embedding(message_id=m.message_id, model=embedder.name, vector=v.astype(np.float32).tobytes()))
        db.commit()
    return len(todo)


def load_matrix(db: Session, message_ids: list[str] | None = None) -> tuple[list[str], np.ndarray]:
    q = (select(Embedding.message_id, Embedding.vector)
         .join(Message, Message.message_id == Embedding.message_id).where(Message.in_trash == False))  # noqa: E712
    if message_ids is not None:
        q = q.where(Embedding.message_id.in_(message_ids))
    rows = db.execute(q).all()
    if not rows:
        return [], np.zeros((0, 0), dtype=np.float32)
    return [r[0] for r in rows], np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
