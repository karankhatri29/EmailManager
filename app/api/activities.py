from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from ..db.models import User
from ..db.session import get_db
from ..repositories import activities as activities_repo
from ..schemas import ActivityCreate, ActivityOut, ActivityUpdate
from .deps import current_user

router = APIRouter(prefix="/api/activities", tags=["activities"])


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


@router.get("", response_model=list[ActivityOut])
def list_activities(
    start: datetime | None = None,
    end: datetime | None = None,
    include_unscheduled: bool = True,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Scheduled activities in [start, end) (all scheduled ones if omitted) plus the unscheduled backlog."""
    return activities_repo.list_for_user(db, user.id, _aware(start), _aware(end), include_unscheduled)


@router.post("", response_model=ActivityOut, status_code=201)
def create_activity(
    payload: ActivityCreate, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    fields = payload.model_dump()
    fields["all_day"] = fields["all_day"] and fields["start_at"] is not None
    return activities_repo.create(db, user.id, source="manual", **fields)


@router.patch("/{activity_id}", response_model=ActivityOut)
def update_activity(
    activity_id: int,
    payload: ActivityUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    activity = activities_repo.get_for_user(db, user.id, activity_id)
    if activity is None:
        raise HTTPException(status_code=404, detail="Activity not found")

    changes = payload.model_dump(exclude_unset=True)
    if "remind_at" in changes:
        changes["reminded_at"] = None  # a changed reminder time fires again
    for required in ("title", "status", "all_day"):
        if required in changes and changes[required] is None:
            raise HTTPException(status_code=422, detail=f"{required} cannot be null")

    start = changes.get("start_at", _aware(activity.start_at))
    end = changes.get("end_at", _aware(activity.end_at))
    if "start_at" in changes and changes["start_at"] is None:  # unscheduling clears the rest of the schedule
        changes.update(end_at=None, all_day=False)
        end = None
    if end is not None and start is None:
        raise HTTPException(status_code=422, detail="end_at requires start_at")
    if start is not None and end is not None and end < start:
        raise HTTPException(status_code=422, detail="end_at must not be before start_at")

    return activities_repo.update(db, activity, **changes)


@router.delete("/{activity_id}", status_code=204)
def delete_activity(activity_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    activity = activities_repo.get_for_user(db, user.id, activity_id)
    if activity is None:
        raise HTTPException(status_code=404, detail="Activity not found")
    activities_repo.delete(db, activity)
    return Response(status_code=204)
