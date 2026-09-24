from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import ACTIONABLE_MAX_AGE_DAYS, SUMMARIZED_CATEGORIES
from ..db.models import Activity
from ..repositories import activities as activities_repo
from .analysis import analyse
from .temporal import ASAP, DEADLINE


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def activity_from_email(email: dict) -> dict:
    """Builds the calendar item for an Urgent/Important email: its action, and its date if it states one.

    Uses the analysis stored on the email when there is one, and analyses the message otherwise.
    """
    if not email.get("nlp_version"):
        email = {**email, **analyse(email["subject"], email["body"], email["date"])}
    kind, day = email.get("due_kind"), email.get("due_date")
    if kind == ASAP:
        day = _aware(email["date"]).date()
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) if day else None

    notes = f"From: {email['sender']}\nSubject: {email['subject']}"
    if email.get("due_text"):
        label = {DEADLINE: "Deadline", ASAP: "Requested"}.get(kind or "", "Event date")
        notes += f'\n{label} found in the email: "{email["due_text"]}"'

    return {
        "user_id": email["user_id"],
        "email_id": email["id"],
        "title": (email.get("task") or email["subject"])[:255],
        "notes": notes,
        "start_at": start,
        "all_day": start is not None,
        "status": "todo",
        "source": "email",
    }


def create_activities_for_emails(db: Session, emails: list[dict]) -> int:
    """Creates activities for the recent Urgent/Important emails that don't have one yet."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=ACTIONABLE_MAX_AGE_DAYS)
    candidates = [e for e in emails if e["category"] in SUMMARIZED_CATEGORIES and _aware(e["date"]) >= cutoff]
    if not candidates:
        return 0

    have = set(
        db.scalars(select(Activity.email_id).where(Activity.email_id.in_([e["id"] for e in candidates])))
    )
    new = [activity_from_email(e) for e in candidates if e["id"] not in have]
    if new:
        activities_repo.create_many(db, new)
    return len(new)
