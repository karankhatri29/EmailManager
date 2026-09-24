from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..core.config import SUMMARIZED_CATEGORIES
from ..db.models import Email, SyncState


def make_email_id(account_id: int, provider_message_id: str) -> str:
    """Message ids are only unique within a mailbox, so the stored id is prefixed with the account."""
    return f"{account_id}:{provider_message_id}"


def get_existing_ids(db: Session, ids: Iterable[str]) -> set[str]:
    ids = list(ids)
    if not ids:
        return set()
    return set(db.scalars(select(Email.id).where(Email.id.in_(ids))))


def upsert_many(db: Session, emails: Iterable[dict]) -> None:
    for email in emails:
        db.merge(Email(**email))
    db.commit()


def list_in_window(db: Session, user_id: int, days: int) -> list[Email]:
    """The user's emails newer than `days` days, most recent first."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    query = select(Email).where(Email.user_id == user_id, Email.date >= cutoff).order_by(Email.date.desc())
    return list(db.scalars(query))


def list_pending_summaries(db: Session, account_id: int) -> list[Email]:
    """Urgent/Important emails of a mailbox that still have no AI summary."""
    query = select(Email).where(
        Email.account_id == account_id,
        Email.category.in_(SUMMARIZED_CATEGORIES),
        Email.summary.is_(None),
    )
    return list(db.scalars(query.order_by(Email.date.desc())))


def set_summary(db: Session, email_id: str, summary: str) -> None:
    db.execute(update(Email).where(Email.id == email_id).values(summary=summary))
    db.commit()


def get_synced_at(db: Session, account_id: int, timeframe: str) -> datetime | None:
    state = db.get(SyncState, (account_id, timeframe))
    if state is None:
        return None
    synced_at = state.synced_at
    return synced_at if synced_at.tzinfo else synced_at.replace(tzinfo=timezone.utc)


def mark_synced(db: Session, account_id: int, timeframe: str) -> None:
    db.merge(SyncState(account_id=account_id, timeframe=timeframe, synced_at=datetime.now(timezone.utc)))
    db.commit()
