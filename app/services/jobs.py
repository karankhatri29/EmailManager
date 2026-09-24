"""Recurring jobs that are not mailbox syncs: briefings, reminders and follow-up nudges.

Each job is idempotent (it records what it did), so running it often, or twice, is harmless.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Activity, User
from ..db.session import SessionLocal
from ..repositories import followups as followups_repo
from ..repositories import notifications as notifications_repo
from ..repositories import settings as settings_repo
from . import briefing as briefing_service
from . import digest as digest_service
from . import newsletter_cleanup
from .followups import waiting_days
from .notifier import try_send_email

logger = logging.getLogger(__name__)


def briefing_subject(b: dict) -> str:
    urgent = b["counts"]["urgent"]
    if urgent:
        return f"Your briefing: {urgent} urgent, {len(b['today'])} due today"
    return f"Your briefing for {b['date']}"


def send_due_briefings(db: Session, now: datetime | None = None) -> int:
    """Sends each opted-in user's briefing once per local day, at or after their chosen hour."""
    now = now or datetime.now(timezone.utc)
    sent = 0
    for settings in settings_repo.list_with_briefing_enabled(db):
        local = briefing_service.local_now(settings, now)
        if local.hour < settings.briefing_hour or settings.briefing_last_sent == local.date():
            continue
        user = db.get(User, settings.user_id)
        if user is None:
            continue

        briefing = briefing_service.build_briefing(db, user, settings, now)
        notifications_repo.create(
            db, user.id, "briefing", briefing_subject(briefing), briefing_service.render_text(briefing)
        )
        try_send_email(
            user.email,
            briefing_subject(briefing),
            briefing_service.render_text(briefing),
            briefing_service.render_html(briefing),
        )
        settings.briefing_last_sent = local.date()  # even if email is off: the in-app copy was delivered
        db.commit()
        sent += 1
    return sent


def send_due_digests(db: Session, now: datetime | None = None) -> int:
    """Sends each opted-in user's evening promotions digest once per local day, at or after their hour."""
    now = now or datetime.now(timezone.utc)
    sent = 0
    for settings in settings_repo.list_with_digest_enabled(db):
        if not digest_service.is_due(settings, now):
            continue
        user = db.get(User, settings.user_id)
        if user is None:
            continue
        digest = digest_service.build_digest(db, user, settings, now)
        if digest["count"]:  # a day without promotions needs no message
            notifications_repo.create(
                db, user.id, "digest", "Your promotions digest", digest_service.render_text(digest)
            )
            try_send_email(
                user.email,
                "Your promotions digest",
                digest_service.render_text(digest),
                digest_service.render_html(digest),
            )
            sent += 1
        settings.digest_last_sent = briefing_service.local_now(settings, now).date()
        db.commit()
    return sent


def run_auto_cleanups(db: Session, now: datetime | None = None) -> int:
    """Once a local day, cleans up the unopened newsletters of users who turned automatic cleanup on."""
    now = now or datetime.now(timezone.utc)
    cleaned = 0
    for settings in settings_repo.list_with_auto_cleanup(db):
        today = briefing_service.local_now(settings, now).date()
        user = db.get(User, settings.user_id)
        if user is None or settings.cleanup_last_run == today:
            continue
        cleaned += newsletter_cleanup.run_for_user(db, user, settings, now)
        settings.cleanup_last_run = today
        db.commit()
    return cleaned


def fire_due_reminders(db: Session, now: datetime | None = None) -> int:
    """Turns due activity reminders into notifications (and emails, for users who asked for them)."""
    now = now or datetime.now(timezone.utc)
    due = db.scalars(
        select(Activity).where(
            Activity.remind_at.is_not(None),
            Activity.remind_at <= now,
            Activity.reminded_at.is_(None),
            Activity.status != "done",
        )
    ).all()
    for activity in due:
        activity.reminded_at = now
        notifications_repo.create(
            db,
            activity.user_id,
            "reminder",
            f"Reminder: {activity.title}",
            activity.notes,
            f"activity:{activity.id}",
        )
        settings = settings_repo.get_or_create(db, activity.user_id)
        user = db.get(User, activity.user_id)
        if settings.reminder_emails and user is not None:
            try_send_email(
                user.email, f"Reminder: {activity.title}", (activity.notes or activity.title) + "\n"
            )
    db.commit()
    return len(due)


def nudge_followups(db: Session, now: datetime | None = None) -> int:
    """One notification per follow-up when it has waited long enough."""
    now = now or datetime.now(timezone.utc)
    due = followups_repo.list_due_for_nudge(db, now)
    for followup in due:
        followup.nudged_at = now
        days = waiting_days(followup, now)
        notifications_repo.create(
            db,
            followup.user_id,
            "followup",
            f"No reply yet: {followup.subject}"[:255],
            f"You wrote to {followup.recipient} {days} day{'s' if days != 1 else ''} ago.",
            f"followup:{followup.id}",
        )
    db.commit()
    return len(due)


def run_periodic_jobs(now: datetime | None = None) -> dict[str, int]:
    """Runs every recurring job once, each in its own session so one failure cannot stop the others."""
    results = {}
    for name, job in (
        ("briefings", send_due_briefings),
        ("digests", send_due_digests),
        ("cleanups", run_auto_cleanups),
        ("reminders", fire_due_reminders),
        ("followups", nudge_followups),
        ("pruned", lambda db, now=None: notifications_repo.prune(db)),
    ):
        try:
            with SessionLocal() as db:
                results[name] = job(db, now)
        except Exception:
            logger.exception("Periodic job %s failed", name)
            results[name] = 0
    return results
