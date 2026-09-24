"""Whole-conversation view and AI summary (three bullets) for an email's thread."""

from sqlalchemy.orm import Session

from ..db.models import Email, ThreadSummary
from ..repositories import emails as emails_repo
from ..repositories import threads as threads_repo
from . import ai_summarizer
from .temporal import strip_history_and_footer

PER_MESSAGE_CHARS = 1500
MAX_TRANSCRIPT_CHARS = 12000
PART_CHARS = 9000  # one condensing request
MAX_PARTS = 6


class NothingToSummarise(Exception):
    """The conversation has fewer than two stored messages."""


def messages_for(db: Session, user_id: int, email: Email) -> list[Email]:
    """The stored messages of the email's conversation, oldest first (just the email if it has no thread)."""
    if not email.thread_id:
        return [email]
    return emails_repo.thread_messages(db, user_id, email.account_id, email.thread_id) or [email]


def cached_summary(db: Session, user_id: int, email: Email) -> ThreadSummary | None:
    if not email.thread_id:
        return None
    return threads_repo.get(db, user_id, threads_repo.thread_key(email.account_id, email.thread_id))


def _chunks(messages: list[Email]) -> list[str]:
    """One text block per message, oldest first, with the quoted earlier replies and signatures left out."""
    blocks = []
    for i, m in enumerate(messages, 1):
        text = " ".join(strip_history_and_footer(m.body).split()) or " ".join(m.body.split())
        blocks.append(f"[{i}] From {m.sender} on {m.date:%Y-%m-%d %H:%M}\n{text[:PER_MESSAGE_CHARS]}")
    return blocks


def _transcript(messages: list[Email]) -> str:
    """The whole conversation as text for the summariser.

    A long chain (dozens of replies) does not fit in one request, so its older stretches are first condensed
    into short notes and only the latest messages are kept word for word. Nothing is dropped unread.
    """
    blocks = _chunks(messages)
    text = "\n\n".join(blocks)
    if len(text) <= MAX_TRANSCRIPT_CHARS:
        return text

    recent: list[str] = []
    size = 0
    while blocks and size + len(blocks[-1]) <= MAX_TRANSCRIPT_CHARS // 3:
        size += len(blocks[-1])
        recent.insert(0, blocks.pop())
    older = "\n\n".join(blocks)
    parts = [older[k : k + PART_CHARS] for k in range(0, len(older), PART_CHARS)]
    notes = [
        f"Notes on the earlier messages (part {n}):\n{ai_summarizer.condense_thread_part(part)}"
        for n, part in enumerate(parts[:MAX_PARTS], 1)
    ]
    return "\n\n".join(notes + ["Latest messages, word for word:"] + recent)


def summarize(db: Session, user_id: int, email: Email) -> ThreadSummary:
    """Summary of the conversation; reused until the thread gains a message. Raises if the AI call fails."""
    messages = messages_for(db, user_id, email)
    if len(messages) < 2 or not email.thread_id:
        raise NothingToSummarise
    cached = cached_summary(db, user_id, email)
    if cached is not None and cached.message_count == len(messages):
        return cached

    summary = ai_summarizer.summarize_thread(_transcript(messages))
    return threads_repo.save(
        db, user_id, threads_repo.thread_key(email.account_id, email.thread_id), len(messages), summary
    )
