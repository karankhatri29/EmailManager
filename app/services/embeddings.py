"""Text embeddings (Gemini) for semantic search: 'find that invoice for the blue couch' without exact words."""

import array
import logging
from collections.abc import Sequence
from typing import Any, cast

import numpy as np
from google import genai
from google.genai import types
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..repositories import emails as emails_repo

logger = logging.getLogger(__name__)

DIMENSIONS = 256  # small vectors: ~1 KB per email, and plenty for ranking a personal inbox
BATCH_SIZE = 50
MAX_TEXT_CHARS = 1500
PENDING_PER_SYNC = 1000


def pack(vector: Sequence[float]) -> bytes:
    return array.array("f", vector).tobytes()


def unpack(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def normalise(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm else vector


def email_text(subject: str, sender: str, body: str) -> str:
    return f"{subject}\nFrom: {sender}\n{body}"[:MAX_TEXT_CHARS]


def embed_texts(texts: list[str], task_type: str) -> list[list[float]]:
    """One vector per text. task_type: RETRIEVAL_DOCUMENT for mail, RETRIEVAL_QUERY for the search box."""
    settings = get_settings()
    client = genai.Client(api_key=settings.gemini_api_key or None)
    response = client.models.embed_content(
        model=settings.gemini_embedding_model,
        contents=cast("list[Any]", texts),
        config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=DIMENSIONS),
    )
    return [list(e.values or []) for e in response.embeddings or []]


def embed_query(query: str) -> np.ndarray | None:
    """The search query as a unit vector, or None if embeddings are unavailable."""
    try:
        (vector,) = embed_texts([query], "RETRIEVAL_QUERY")
    except Exception:
        logger.warning("Query embedding failed; falling back to keyword search", exc_info=True)
        return None
    return normalise(np.asarray(vector, dtype=np.float32))


def embed_pending(db: Session, account_id: int, limit: int = PENDING_PER_SYNC) -> int:
    """Embeds a mailbox's stored emails that have no vector yet. Failures are retried on the next sync."""
    pending = emails_repo.list_pending_embeddings(db, account_id, limit)
    done = 0
    for start in range(0, len(pending), BATCH_SIZE):
        batch = pending[start : start + BATCH_SIZE]
        try:
            vectors = embed_texts(
                [email_text(e.subject, e.sender, e.body) for e in batch], "RETRIEVAL_DOCUMENT"
            )
        except Exception:
            logger.warning("Embedding batch failed; will retry next sync", exc_info=True)
            break
        emails_repo.set_embeddings(
            db,
            {
                e.id: pack(normalise(np.asarray(v, dtype=np.float32)).tolist())
                for e, v in zip(batch, vectors, strict=True)
            },
        )
        done += len(batch)
    if done:
        logger.info("Embedded %d emails", done)
    return done
