"""The student timetable: weekly classes, and the classes on a given day."""

from datetime import date, time

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from ..db.models import ClassSlot, User
from ..db.session import get_db
from ..repositories import settings as settings_repo
from ..repositories import timetable as timetable_repo
from ..schemas import ClassSlotCreate, ClassSlotOut, ClassSlotUpdate
from ..services import briefing as briefing_service
from ..services.timetable import conflict_message, default_color
from .deps import current_user

router = APIRouter(prefix="/api", tags=["timetable"])

# Fields that describe the course rather than one particular meeting of it.
COURSE_WIDE_FIELDS = ("title", "code", "color", "instructor", "term_start", "term_end")


@router.get("/timetable", response_model=list[ClassSlotOut])
def list_timetable(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return timetable_repo.list_for_user(db, user.id)


@router.get("/timetable/day", response_model=list[ClassSlotOut])
def classes_on_day(
    day: date | None = Query(default=None, alias="date"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """The classes held on a date (default: today in your time zone), in order."""
    if day is None:
        day = briefing_service.local_now(settings_repo.get_or_create(db, user.id)).date()
    return timetable_repo.slots_on(db, user.id, day)


@router.get("/courses", response_model=list[str])
def list_courses(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Course names from your timetable and to-dos, for pickers."""
    return timetable_repo.course_names(db, user.id)


@router.post("/timetable", response_model=list[ClassSlotOut], status_code=201)
def add_class(payload: ClassSlotCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Adds a class, one slot per weekday it meets. Rejected if it overlaps a class you already have."""
    if timetable_repo.count_for_user(db, user.id) + len(payload.weekdays) > timetable_repo.MAX_SLOTS_PER_USER:
        raise HTTPException(status_code=422, detail="Your timetable is full")

    for weekday in payload.weekdays:
        clash = timetable_repo.find_conflict(
            db, user.id, weekday, payload.start_time, payload.end_time, payload.term_start, payload.term_end
        )
        if clash is not None:
            raise HTTPException(status_code=409, detail=conflict_message(clash))

    color = payload.color or default_color(payload.title)
    slots = [
        ClassSlot(
            user_id=user.id,
            title=payload.title,
            code=payload.code,
            weekday=weekday,
            start_time=payload.start_time,
            end_time=payload.end_time,
            room=payload.room,
            instructor=payload.instructor,
            color=color,
            term_start=payload.term_start,
            term_end=payload.term_end,
            notes=payload.notes,
        )
        for weekday in payload.weekdays
    ]
    timetable_repo.add_all(db, slots)
    return sorted(slots, key=lambda s: (s.weekday, s.start_time))


@router.patch("/timetable/{slot_id}", response_model=ClassSlotOut)
def edit_class(
    slot_id: int, payload: ClassSlotUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    slot = timetable_repo.get_for_user(db, user.id, slot_id)
    if slot is None:
        raise HTTPException(status_code=404, detail="Class not found")

    changes = payload.model_dump(exclude_unset=True)
    whole_course = changes.pop("apply_to_course", False)
    for required in ("title", "weekday", "start_time", "end_time"):
        if required in changes and changes[required] is None:
            raise HTTPException(status_code=422, detail=f"{required} cannot be empty")
    if "title" in changes and not changes["title"]:
        raise HTTPException(status_code=422, detail="title cannot be empty")

    weekday: int = changes.get("weekday", slot.weekday)
    start: time = changes.get("start_time", slot.start_time)
    end: time = changes.get("end_time", slot.end_time)
    term_start: date | None = changes.get("term_start", slot.term_start)
    term_end: date | None = changes.get("term_end", slot.term_end)
    if end <= start:
        raise HTTPException(status_code=422, detail="The class must end after it starts")
    if term_start and term_end and term_end < term_start:
        raise HTTPException(status_code=422, detail="The term cannot end before it starts")

    clash = timetable_repo.find_conflict(db, user.id, weekday, start, end, term_start, term_end, (slot.id,))
    if clash is not None:
        raise HTTPException(status_code=409, detail=conflict_message(clash))

    siblings: list[ClassSlot] = []
    if whole_course:
        shared = {k: v for k, v in changes.items() if k in COURSE_WIDE_FIELDS}
        siblings = [s for s in timetable_repo.same_course(db, user.id, slot.title) if s.id != slot.id]
        if "term_start" in shared or "term_end" in shared:  # a new term can make other meetings overlap
            moved = tuple(s.id for s in siblings) + (slot.id,)
            for sibling in siblings:
                clash = timetable_repo.find_conflict(
                    db,
                    user.id,
                    sibling.weekday,
                    sibling.start_time,
                    sibling.end_time,
                    shared.get("term_start", sibling.term_start),
                    shared.get("term_end", sibling.term_end),
                    moved,
                )
                if clash is not None:
                    raise HTTPException(status_code=409, detail=conflict_message(clash))
        for sibling in siblings:
            for key, value in shared.items():
                setattr(sibling, key, value)

    for key, value in changes.items():
        setattr(slot, key, value)
    db.commit()
    return slot


@router.delete("/timetable/{slot_id}", status_code=204)
def delete_class(slot_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    slot = timetable_repo.get_for_user(db, user.id, slot_id)
    if slot is None:
        raise HTTPException(status_code=404, detail="Class not found")
    db.delete(slot)
    db.commit()
    return Response(status_code=204)


@router.delete("/timetable", status_code=204)
def delete_course(
    course: str = Query(min_length=1, max_length=120),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Removes every meeting of a course."""
    if timetable_repo.delete_course(db, user.id, course) == 0:
        raise HTTPException(status_code=404, detail="Course not found")
    return Response(status_code=204)
