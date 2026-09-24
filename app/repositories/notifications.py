from datetime import datetime, timedelta, timezone

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..db.models import Notification

KEEP_DAYS = 60


def create(
    db: Session, user_id: int, kind: str, title: str, body: str | None = None, ref: str | None = None
) -> Notification:
    row = Notification(user_id=user_id, kind=kind, title=title[:255], body=body, ref=ref)
    db.add(row)
    db.commit()
    return row


def exists(db: Session, user_id: int, kind: str, ref: str) -> bool:
    """Whether this user already has a notification of this kind for this reference (to avoid repeats)."""
    return (
        db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == user_id, Notification.kind == kind, Notification.ref == ref)
        )
        or 0
    ) > 0


def list_for_user(
    db: Session, user_id: int, unread_only: bool = False, limit: int = 30
) -> list[Notification]:
    query = select(Notification).where(Notification.user_id == user_id)
    if unread_only:
        query = query.where(Notification.read_at.is_(None))
    return list(
        db.scalars(query.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit))
    )


def unread_count(db: Session, user_id: int) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == user_id, Notification.read_at.is_(None))
        )
        or 0
    )


def mark_read(db: Session, user_id: int, ids: list[int] | None = None) -> int:
    """Marks the given notifications (or all of the user's) as read."""
    query = update(Notification).where(Notification.user_id == user_id, Notification.read_at.is_(None))
    if ids is not None:
        query = query.where(Notification.id.in_(ids))
    result = db.execute(query.values(read_at=datetime.now(timezone.utc)))
    db.commit()
    return getattr(result, "rowcount", 0) or 0


def prune(db: Session) -> int:
    """Deletes notifications older than KEEP_DAYS."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)
    result = db.execute(sql_delete(Notification).where(Notification.created_at < cutoff))
    db.commit()
    return getattr(result, "rowcount", 0) or 0
