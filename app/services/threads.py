"""Whole-conversation view and AI summary for an email's thread."""

from sqlalchemy.orm import Session

from ..db.models import Email, ThreadSummary
from ..repositories import emails as emails_repo
from ..repositories import threads as threads_repo
from . import ai_summarizer

PER_MESSAGE_CHARS = 1500
MAX_TRANSCRIPT_CHARS = 12000


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


def _transcript(messages: list[Email]) -> str:
    """Oldest-first text of the conversation, trimmed from the *old* end if it is too long."""
    chunks = [
        f"[{i}] From {m.sender} on {m.date:%Y-%m-%d %H:%M}\n{' '.join(m.body.split())[:PER_MESSAGE_CHARS]}"
        for i, m in enumerate(messages, 1)
    ]
    text = "\n\n".join(chunks)
    return text[-MAX_TRANSCRIPT_CHARS:]


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
