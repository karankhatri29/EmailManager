from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import SUMMARIZED_CATEGORIES
from ..db.models import Activity
from ..repositories import activities as activities_repo
from .deadlines import NO_DEADLINE, resolve_deadline
from .nlp_engine import extract_action_task, extract_explicit_deadline, nlp, process_text


def activity_from_email(email: dict) -> dict:
    """Builds the calendar item for an Urgent/Important email: its action, and its deadline if it states one."""
    text = f"{email['subject']} {email['body']}"
    nlp_data = process_text(text)
    title = extract_action_task(email["subject"], nlp(text), nlp_data["clean_text_raw"])
    deadline = extract_explicit_deadline(nlp_data["clean_text_raw"], nlp_data["entities"])
    due = resolve_deadline(deadline["value"], email["date"])

    notes = f"From: {email['sender']}\nSubject: {email['subject']}"
    if deadline["value"] != NO_DEADLINE:
        notes += f"\nDeadline detected: {deadline['value']}"

    return {
        "user_id": email["user_id"],
        "email_id": email["id"],
        "title": title[:255],
        "notes": notes,
        "start_at": due,
        "all_day": due is not None,
        "status": "todo",
        "source": "email",
    }


def create_activities_for_emails(db: Session, emails: list[dict]) -> int:
    """Creates activities for the Urgent/Important emails that don't have one yet."""
    candidates = [e for e in emails if e["category"] in SUMMARIZED_CATEGORIES]
    if not candidates:
        return 0

    have = set(
        db.scalars(select(Activity.email_id).where(Activity.email_id.in_([e["id"] for e in candidates])))
    )
    new = [activity_from_email(e) for e in candidates if e["id"] not in have]
    if new:
        activities_repo.create_many(db, new)
    return len(new)
