from datetime import date, time

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Activity, ClassSlot

MAX_SLOTS_PER_USER = 300


def list_for_user(db: Session, user_id: int) -> list[ClassSlot]:
    query = select(ClassSlot).where(ClassSlot.user_id == user_id)
    return list(db.scalars(query.order_by(ClassSlot.weekday, ClassSlot.start_time, ClassSlot.id)))


def count_for_user(db: Session, user_id: int) -> int:
    return db.scalar(select(func.count()).select_from(ClassSlot).where(ClassSlot.user_id == user_id)) or 0


def get_for_user(db: Session, user_id: int, slot_id: int) -> ClassSlot | None:
    return db.scalar(select(ClassSlot).where(ClassSlot.id == slot_id, ClassSlot.user_id == user_id))


def same_course(db: Session, user_id: int, title: str) -> list[ClassSlot]:
    """Every meeting of a course (matched by title, ignoring case)."""
    query = select(ClassSlot).where(
        ClassSlot.user_id == user_id, func.lower(ClassSlot.title) == title.lower()
    )
    return list(db.scalars(query.order_by(ClassSlot.weekday, ClassSlot.start_time)))


def add_all(db: Session, slots: list[ClassSlot]) -> None:
    db.add_all(slots)
    db.commit()


def delete_course(db: Session, user_id: int, title: str) -> int:
    result = db.execute(
        sql_delete(ClassSlot).where(
            ClassSlot.user_id == user_id, func.lower(ClassSlot.title) == title.lower()
        )
    )
    db.commit()
    return getattr(result, "rowcount", 0) or 0


def slots_on(db: Session, user_id: int, day: date) -> list[ClassSlot]:
    """The classes held on a given calendar day: same weekday, and inside the slot's term if it has one."""
    query = select(ClassSlot).where(ClassSlot.user_id == user_id, ClassSlot.weekday == day.weekday())
    return [
        s
        for s in db.scalars(query.order_by(ClassSlot.start_time, ClassSlot.id))
        if (s.term_start is None or s.term_start <= day) and (s.term_end is None or day <= s.term_end)
    ]


def _terms_overlap(
    a_start: date | None, a_end: date | None, b_start: date | None, b_end: date | None
) -> bool:
    return (a_start or date.min) <= (b_end or date.max) and (b_start or date.min) <= (a_end or date.max)


def find_conflict(
    db: Session,
    user_id: int,
    weekday: int,
    start: time,
    end: time,
    term_start: date | None,
    term_end: date | None,
    exclude_ids: tuple[int, ...] = (),
) -> ClassSlot | None:
    """A different class that meets at an overlapping time on the same weekday during an overlapping term."""
    query = select(ClassSlot).where(ClassSlot.user_id == user_id, ClassSlot.weekday == weekday)
    for slot in db.scalars(query):
        if slot.id in exclude_ids:
            continue
        if (
            start < slot.end_time
            and slot.start_time < end
            and _terms_overlap(term_start, term_end, slot.term_start, slot.term_end)
        ):
            return slot
    return None


def course_names(db: Session, user_id: int) -> list[str]:
    """Every course the user has: from the timetable and from the courses their to-dos mention."""
    from_slots = db.scalars(select(ClassSlot.title).where(ClassSlot.user_id == user_id))
    from_todos = db.scalars(
        select(Activity.course).where(Activity.user_id == user_id, Activity.course.is_not(None))
    )
    unique: dict[str, str] = {}
    for name in [*from_slots, *from_todos]:
        if name:
            unique.setdefault(name.lower(), name)
    return sorted(unique.values(), key=str.lower)
