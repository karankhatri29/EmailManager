from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..db.models import User
from ..db.session import get_db
from ..repositories import activities as activities_repo
from ..repositories import users as users_repo
from ..schemas import CalendarFeedOut
from ..services.ics import build_calendar
from .deps import current_user

router = APIRouter(tags=["calendar"])


def _feed_url(user: User) -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/calendar/{user.calendar_token}.ics"


@router.get("/api/calendar/feed", response_model=CalendarFeedOut)
def get_feed_url(user: User = Depends(current_user)):
    """The private link to subscribe to from Google Calendar, Outlook or Apple Calendar."""
    return CalendarFeedOut(url=_feed_url(user))


@router.post("/api/calendar/feed/rotate", response_model=CalendarFeedOut)
def rotate_feed_url(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Issues a new link and invalidates the old one (use if the link leaked)."""
    users_repo.rotate_calendar_token(db, user)
    return CalendarFeedOut(url=_feed_url(user))


@router.get("/calendar/{token}.ics", include_in_schema=False)
def calendar_feed(token: str, db: Session = Depends(get_db)):
    """Public (no login) because calendar apps can't log in; the secret token is the credential."""
    user = users_repo.get_by_calendar_token(db, token)
    if user is None:
        raise HTTPException(status_code=404, detail="Not found")
    body = build_calendar(activities_repo.list_scheduled_open(db, user.id))
    return Response(
        content=body, media_type="text/calendar; charset=utf-8", headers={"Cache-Control": "no-store"}
    )
