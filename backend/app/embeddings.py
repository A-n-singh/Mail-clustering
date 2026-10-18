"""Local sentence embeddings (FR-19). The model runs on this machine; no mail text is sent anywhere.
Text = sender name + subject first, then a short snippet (not the whole body)."""
from typing import Protocol

import numpy as np
from sqlalchemy import func, select
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


def stale_embeddings(db: Session, model_name: str) -> int:
    """How many live mails carry a vector from a DIFFERENT embedding model than `model_name` (EMBED_MODEL was changed).
    Trashed mail is not counted: it is re-embedded only if it comes back."""
    return db.scalar(select(func.count()).select_from(Embedding).join(Message, Message.message_id == Embedding.message_id)
                     .where(Message.in_trash == False, Embedding.model != model_name)) or 0  # noqa: E712


def ensure_embeddings(db: Session, embedder: Embedder, batch: int = 64) -> int:
    """Make every live mail's vector come from `embedder`: embed mail that has none, and RE-embed mail whose vector was made by
    another model (the model was changed), so vectors of two models are never mixed. Safe to stop and run again: only
    what is still missing or stale is done."""
    todo = list(db.execute(select(Message, Embedding).outerjoin(Embedding, Embedding.message_id == Message.message_id)
                           .where(Message.in_trash == False,  # noqa: E712
                                  (Embedding.message_id.is_(None)) | (Embedding.model != embedder.name))).all())
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        vecs = embedder.embed([message_text(m) for m, _ in chunk])
        for (m, old), v in zip(chunk, vecs):
            data = v.astype(np.float32).tobytes()
            if old is None:
                db.add(Embedding(message_id=m.message_id, model=embedder.name, vector=data))
            else:
                old.model, old.vector = embedder.name, data
        db.commit()
    return len(todo)


def load_matrix(db: Session, message_ids: list[str] | None = None, model: str | None = None) -> tuple[list[str], np.ndarray]:
    """Vectors of live mail. Pass `model` (the embedder's name) so only vectors of that model are returned."""
    q = (select(Embedding.message_id, Embedding.vector)
         .join(Message, Message.message_id == Embedding.message_id).where(Message.in_trash == False))  # noqa: E712
    if model is not None:
        q = q.where(Embedding.model == model)
    if message_ids is not None:
        q = q.where(Embedding.message_id.in_(message_ids))
    rows = db.execute(q).all()
    if not rows:
        return [], np.zeros((0, 0), dtype=np.float32)
    return [r[0] for r in rows], np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
