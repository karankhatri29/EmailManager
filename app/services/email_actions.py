"""What a user can do to an email: correct its category, mute a sender, mark it done, snooze it.

Category changes also keep the calendar consistent: mail that becomes Urgent/Important gets an
activity; mail that stops being actionable loses its still-open one.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from ..core.config import SUMMARIZED_CATEGORIES
from ..db.models import Email
from ..repositories import activities as activities_repo
from ..repositories import emails as emails_repo
from ..repositories import rules as rules_repo
from .activities_service import create_activities_for_emails
from .analysis import analyse_email
from .email_processor import classify_email
from .nlp_engine import category_score
from .rules import to_specs


def _as_dict(email: Email) -> dict:
    """The fields activities_service needs from a stored email."""
    return {
        "id": email.id,
        "user_id": email.user_id,
        "subject": email.subject,
        "body": email.body,
        "sender": email.sender,
        "date": email.date,
        "category": email.category,
        "task": email.task,
        "nlp_version": email.nlp_version,
        "due_date": email.due_date,
        "due_kind": email.due_kind,
        "due_text": email.due_text,
    }


def _refresh_activities(db: Session, changed: list[Email]) -> None:
    actionable = [e for e in changed if e.category in SUMMARIZED_CATEGORIES]
    if actionable:
        create_activities_for_emails(db, [_as_dict(e) for e in actionable])
    activities_repo.delete_open_for_emails(
        db, [e.id for e in changed if e.category not in SUMMARIZED_CATEGORIES]
    )


def _set(email: Email, category: str, source: str, reason: str, score: float | None = None) -> bool:
    """Applies a classification; returns True if anything visible changed."""
    new_score = score if score is not None else category_score(category)
    changed = (email.category, email.category_source, email.reason) != (category, source, reason)
    email.category, email.category_source, email.reason, email.score = category, source, reason, new_score
    if changed:
        analyse_email(email)  # actionable mail gets its task and date; other mail is cleared
    return changed


def set_category(db: Session, email: Email, category: str, apply_to_sender: bool = False) -> list[Email]:
    """The user's own decision about an email (and optionally about everything from its sender).

    Returns every email whose category changed, the corrected one first.
    """
    changed = [email]
    _set(email, category, "user", f"You set this to {category}.")

    if apply_to_sender and email.sender_address:
        rules_repo.upsert(db, email.user_id, "sender", email.sender_address, category)
        changed += [
            e for e in reapply_rules(db, email.user_id, "sender", email.sender_address) if e.id != email.id
        ]

    db.commit()
    _refresh_activities(db, changed)
    return changed


def reapply_rules(db: Session, user_id: int, kind: str, pattern: str) -> list[Email]:
    """Re-decides the emails a rule touches, using all of the user's current rules.

    Emails the user corrected by hand are never overridden. Used after a rule is added or removed.
    """
    specs = to_specs(rules_repo.list_for_user(db, user_id))
    changed = []
    for email in emails_repo.list_matching_rule(db, user_id, kind, pattern):
        if email.category_source == "user":
            continue
        score, category, reason, source = classify_email(
            email.subject, email.body, email.sender_address, specs
        )
        if _set(email, category, source, reason, score):
            changed.append(email)
    db.commit()
    _refresh_activities(db, changed)
    return changed


def set_done(db: Session, email: Email, done: bool) -> None:
    """Marks an email handled (or open again), and its calendar item with it."""
    email.is_done = done
    email.snoozed_until = None
    db.commit()
    activities_repo.set_status_for_email(db, email.id, "done" if done else "todo")


def snooze(db: Session, email: Email, until: datetime | None) -> None:
    """Hides an email until the given time (None wakes it now)."""
    email.snoozed_until = until
    db.commit()
