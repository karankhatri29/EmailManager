from datetime import datetime

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy import delete as sql_delete
from sqlalchemy import update as sql_update
from sqlalchemy.orm import Session

from ..db.models import Activity


def list_for_user(
    db: Session,
    user_id: int,
    start: datetime | None = None,
    end: datetime | None = None,
    include_unscheduled: bool = True,
) -> list[Activity]:
    """Scheduled activities inside [start, end) plus (optionally) the unscheduled backlog."""
    conditions: list[ColumnElement[bool]] = []
    if start is not None or end is not None:
        scheduled: list[ColumnElement[bool]] = [Activity.start_at.is_not(None)]
        if start is not None:
            scheduled.append(Activity.start_at >= start)
        if end is not None:
            scheduled.append(Activity.start_at < end)
        conditions.append(and_(*scheduled))
    else:
        conditions.append(Activity.start_at.is_not(None))
    if include_unscheduled:
        conditions.append(Activity.start_at.is_(None))

    query = (
        select(Activity)
        .where(Activity.user_id == user_id, or_(*conditions))
        .order_by(Activity.start_at.is_(None), Activity.start_at, Activity.id)
    )
    return list(db.scalars(query))


def get_for_user(db: Session, user_id: int, activity_id: int) -> Activity | None:
    return db.scalar(select(Activity).where(Activity.id == activity_id, Activity.user_id == user_id))


def create(db: Session, user_id: int, **fields) -> Activity:
    activity = Activity(user_id=user_id, **fields)
    db.add(activity)
    db.commit()
    return activity


def create_many(db: Session, activities: list[dict]) -> None:
    db.add_all(Activity(**a) for a in activities)
    db.commit()


def update(db: Session, activity: Activity, **fields) -> Activity:
    for key, value in fields.items():
        setattr(activity, key, value)
    db.commit()
    return activity


def delete(db: Session, activity: Activity) -> None:
    db.delete(activity)
    db.commit()


def list_scheduled_open(db: Session, user_id: int) -> list[Activity]:
    """Open (not done) scheduled activities, for the calendar feed."""
    query = select(Activity).where(
        Activity.user_id == user_id, Activity.start_at.is_not(None), Activity.status != "done"
    )
    return list(db.scalars(query.order_by(Activity.start_at)))


def delete_open_for_emails(db: Session, email_ids: list[str]) -> int:
    """Removes the still-open calendar items that were created from these emails."""
    if not email_ids:
        return 0
    result = db.execute(
        sql_delete(Activity).where(
            Activity.email_id.in_(email_ids), Activity.status != "done", Activity.source == "email"
        )
    )
    db.commit()
    return getattr(result, "rowcount", 0) or 0


def set_status_for_email(db: Session, email_id: str, status: str) -> None:
    """Marks the calendar item created from an email as done / open again."""
    db.execute(sql_update(Activity).where(Activity.email_id == email_id).values(status=status))
    db.commit()


def list_todos(
    db: Session, user_id: int, status: str = "open", course: str | None = None, limit: int = 500
) -> list[Activity]:
    """To-dos for the list view. Open ones: due date first (undated last), then priority. Others: newest first."""
    query = select(Activity).where(Activity.user_id == user_id)
    if status == "open":
        query = query.where(Activity.status != "done")
    elif status == "done":
        query = query.where(Activity.status == "done")
    if course:
        query = query.where(func.lower(Activity.course) == course.lower())

    if status == "open":
        query = query.order_by(
            Activity.start_at.is_(None), Activity.start_at, Activity.priority.desc(), Activity.id
        )
    else:
        query = query.order_by(Activity.updated_at.desc(), Activity.id.desc())
    return list(db.scalars(query.limit(limit)))
