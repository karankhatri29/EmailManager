from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import FollowUp

WAITING = "waiting"
REPLIED = "replied"
DISMISSED = "dismissed"


def get_for_user(db: Session, user_id: int, followup_id: int) -> FollowUp | None:
    return db.scalar(select(FollowUp).where(FollowUp.id == followup_id, FollowUp.user_id == user_id))


def get_by_thread(db: Session, account_id: int, thread_id: str) -> FollowUp | None:
    return db.scalar(
        select(FollowUp).where(FollowUp.account_id == account_id, FollowUp.thread_id == thread_id)
    )


def list_for_user(db: Session, user_id: int, status: str | None = WAITING) -> list[FollowUp]:
    query = select(FollowUp).where(FollowUp.user_id == user_id)
    if status:
        query = query.where(FollowUp.status == status)
    return list(db.scalars(query.order_by(FollowUp.sent_at)))  # oldest (longest waiting) first


def list_due_for_nudge(db: Session, now: datetime | None = None) -> list[FollowUp]:
    """Waiting follow-ups whose reminder time has passed and that have not been nudged yet."""
    now = now or datetime.now(timezone.utc)
    query = select(FollowUp).where(
        FollowUp.status == WAITING, FollowUp.nudged_at.is_(None), FollowUp.nudge_at <= now
    )
    return list(db.scalars(query))
