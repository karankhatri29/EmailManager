"""Orders the user's action items: what is due soonest first, then dated events, then "as soon as possible",
then everything that names no date. Dates come from the analysis stored on each email (analysis.py)."""

from datetime import date, timedelta
from typing import Any

from ..db.models import Email
from .temporal import ASAP, DEADLINE, deadline_label

NEAR_DAYS = 7
EXCLUDED = ("Promotional", "General")


def _tier(kind: str | None, day: date | None, today: date) -> int:
    if kind == DEADLINE and day is not None:
        return 1 if day <= today + timedelta(days=NEAR_DAYS) else 2
    if kind is not None and day is not None:  # an event on a known date
        return 2
    if kind == ASAP:
        return 3
    return 4


def build_schedule(emails: list[Email], today: date) -> list[dict]:
    """Action items in priority order: tier first (1 = most pressing), then date, then classifier score."""
    tasks: list[dict[str, Any]] = []
    for email in emails:
        if email.category in EXCLUDED:
            continue
        tier = _tier(email.due_kind, email.due_date, today)
        tasks.append(
            {
                "id": email.id,
                "sender": email.sender.split("<")[0].strip() or email.sender,
                "task": email.task or email.subject,
                "deadline": deadline_label(email.due_kind, email.due_date, today),
                "sort_tier": tier,
                "base_score": email.score,
                "due_date": email.due_date,
                "deadline_kind": email.due_kind,
                "deadline_text": email.due_text,
            }
        )
    tasks.sort(key=lambda t: (t["sort_tier"], t["due_date"] or date.max, -float(t["base_score"] or 0)))
    return tasks
