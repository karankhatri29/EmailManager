from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db.models import UserSettings


def find(db: Session, user_id: int) -> UserSettings | None:
    return db.scalar(select(UserSettings).where(UserSettings.user_id == user_id))


def get_or_create(db: Session, user_id: int) -> UserSettings:
    """The user's settings row (created with defaults on first use)."""
    row = db.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
    if row is None:
        row = UserSettings(
            user_id=user_id,
            timezone="UTC",
            briefing_enabled=False,
            briefing_hour=8,
            urgent_alerts=True,
            reminder_emails=False,
            followup_days=3,
        )
        db.add(row)
        try:
            db.commit()
        except IntegrityError:  # another request or sync created it first
            db.rollback()
            row = db.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
            if row is None:
                raise
    return row


def update(db: Session, row: UserSettings, **fields) -> UserSettings:
    for key, value in fields.items():
        setattr(row, key, value)
    db.commit()
    return row


def list_with_auto_cleanup(db: Session) -> list[UserSettings]:
    return list(db.scalars(select(UserSettings).where(UserSettings.auto_cleanup.is_(True))))


def list_with_digest_enabled(db: Session) -> list[UserSettings]:
    return list(db.scalars(select(UserSettings).where(UserSettings.digest_enabled.is_(True))))


def list_with_briefing_enabled(db: Session) -> list[UserSettings]:
    return list(db.scalars(select(UserSettings).where(UserSettings.briefing_enabled.is_(True))))
