"""The to-do list: every open or finished task, whether it came from an email or was typed in."""

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db.models import User
from ..db.session import get_db
from ..repositories import activities as activities_repo
from ..schemas import ActivityOut
from .deps import current_user

router = APIRouter(prefix="/api", tags=["todos"])


@router.get("/todos", response_model=list[ActivityOut])
def list_todos(
    status: Literal["open", "done", "all"] = "open",
    course: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=500, ge=1, le=1000),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """To-dos by due date (undated last), then priority. Finished ones come newest first.

    Create, edit, tick off and delete them with the /api/activities endpoints.
    """
    return activities_repo.list_todos(db, user.id, status, course, limit)
