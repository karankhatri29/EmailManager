"""Settings, the daily briefing, notifications and follow-ups."""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..db.models import FollowUp, User
from ..db.session import get_db
from ..repositories import activities as activities_repo
from ..repositories import followups as followups_repo
from ..repositories import notifications as notifications_repo
from ..repositories import settings as settings_repo
from ..schemas import (
    ActivityOut,
    BriefingOut,
    BriefingSent,
    FollowUpOut,
    MarkReadRequest,
    NotificationList,
    NotificationOut,
    SettingsOut,
    SettingsUpdate,
    SnoozeRequest,
)
from ..services import briefing as briefing_service
from ..services.followups import waiting_days
from ..services.jobs import briefing_subject
from ..services.notifier import EmailNotConfigured, EmailSendError, send_email
from .deps import current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["personal"])


# --- settings ------------------------------------------------------------------------------------


def _settings_out(row) -> SettingsOut:
    return SettingsOut(
        timezone=row.timezone,
        briefing_enabled=row.briefing_enabled,
        briefing_hour=row.briefing_hour,
        urgent_alerts=row.urgent_alerts,
        reminder_emails=row.reminder_emails,
        followup_days=row.followup_days,
        email_configured=get_settings().smtp_configured,
    )


@router.get("/settings", response_model=SettingsOut)
def read_settings(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _settings_out(settings_repo.get_or_create(db, user.id))


@router.put("/settings", response_model=SettingsOut)
def update_settings(
    payload: SettingsUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    changes = payload.model_dump(exclude_unset=True)
    if any(v is None for v in changes.values()):
        raise HTTPException(status_code=422, detail="Settings cannot be null")
    if "timezone" in changes and not briefing_service.valid_timezone(changes["timezone"]):
        raise HTTPException(status_code=422, detail="Unknown time zone")
    row = settings_repo.get_or_create(db, user.id)
    if "timezone" in changes or "briefing_hour" in changes:
        changes["briefing_last_sent"] = None  # a changed schedule may still send today's briefing
    return _settings_out(settings_repo.update(db, row, **changes))


# --- daily briefing ------------------------------------------------------------------------------


@router.get("/briefing", response_model=BriefingOut)
def read_briefing(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Today's briefing, built fresh from the stored mail and calendar."""
    return briefing_service.build_briefing(db, user, settings_repo.get_or_create(db, user.id))


@router.post("/briefing/send", response_model=BriefingSent)
def email_briefing_now(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Emails today's briefing to your login address right now (handy to test the setup)."""
    briefing = briefing_service.build_briefing(db, user, settings_repo.get_or_create(db, user.id))
    try:
        send_email(
            user.email,
            briefing_subject(briefing),
            briefing_service.render_text(briefing),
            briefing_service.render_html(briefing),
        )
    except EmailNotConfigured:
        raise HTTPException(status_code=503, detail="This server is not set up to send email.") from None
    except EmailSendError:
        logger.exception("Briefing email failed")
        raise HTTPException(status_code=502, detail="The email could not be sent.") from None
    return BriefingSent(sent_to=user.email)


# --- notifications -------------------------------------------------------------------------------


@router.get("/notifications", response_model=NotificationList)
def list_notifications(
    unread_only: bool = False,
    limit: int = Query(default=30, ge=1, le=100),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    items = notifications_repo.list_for_user(db, user.id, unread_only, limit)
    return NotificationList(
        unread=notifications_repo.unread_count(db, user.id),
        items=[NotificationOut.model_validate(n) for n in items],
    )


@router.post("/notifications/read", status_code=204)
def mark_notifications_read(
    payload: MarkReadRequest, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    """Marks the listed notifications (or all of them, if no ids are given) as read."""
    notifications_repo.mark_read(db, user.id, payload.ids)
    return Response(status_code=204)


# --- follow-ups ----------------------------------------------------------------------------------


def _followup_out(followup: FollowUp) -> FollowUpOut:
    return FollowUpOut(
        id=followup.id,
        account_id=followup.account_id,
        thread_id=followup.thread_id,
        subject=followup.subject,
        recipient=followup.recipient,
        sent_at=followup.sent_at,
        status=followup.status,
        nudge_at=followup.nudge_at,
        waiting_days=waiting_days(followup),
    )


def _own_followup(db: Session, user: User, followup_id: int) -> FollowUp:
    followup = followups_repo.get_for_user(db, user.id, followup_id)
    if followup is None:
        raise HTTPException(status_code=404, detail="Follow-up not found")
    return followup


@router.get("/followups", response_model=list[FollowUpOut])
def list_followups(
    status: str = Query(default="waiting", pattern="^(waiting|replied|dismissed|all)$"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Conversations where you wrote last and nobody has answered (longest wait first)."""
    rows = followups_repo.list_for_user(db, user.id, None if status == "all" else status)
    return [_followup_out(f) for f in rows]


@router.post("/followups/{followup_id}/dismiss", response_model=FollowUpOut)
def dismiss_followup(followup_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    followup = _own_followup(db, user, followup_id)
    followup.status = followups_repo.DISMISSED
    db.commit()
    return _followup_out(followup)


@router.post("/followups/{followup_id}/snooze", response_model=FollowUpOut)
def snooze_followup(
    followup_id: int,
    payload: SnoozeRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Ask again in a few days."""
    followup = _own_followup(db, user, followup_id)
    followup.nudge_at = datetime.now(timezone.utc) + timedelta(days=payload.days)
    followup.nudged_at = None
    db.commit()
    return _followup_out(followup)


@router.post("/followups/{followup_id}/schedule", response_model=ActivityOut, status_code=201)
def schedule_followup(followup_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Puts 'Follow up: <subject>' on tomorrow in your calendar."""
    followup = _own_followup(db, user, followup_id)
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return activities_repo.create(
        db,
        user.id,
        source="manual",
        title=f"Follow up: {followup.subject}"[:255],
        notes=f"You wrote to {followup.recipient} and have not had a reply.",
        start_at=tomorrow,
        all_day=True,
    )
