from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import ThreadSummary


def thread_key(account_id: int, thread_id: str) -> str:
    return f"{account_id}:{thread_id}"


def get(db: Session, user_id: int, key: str) -> ThreadSummary | None:
    return db.scalar(select(ThreadSummary).where(ThreadSummary.user_id == user_id, ThreadSummary.thread_key == key))


def save(db: Session, user_id: int, key: str, message_count: int, summary: str) -> ThreadSummary:
    row = get(db, user_id, key)
    if row is None:
        row = ThreadSummary(user_id=user_id, thread_key=key, message_count=message_count, summary=summary)
        db.add(row)
    else:
        row.message_count, row.summary = message_count, summary
    db.commit()
    return row
