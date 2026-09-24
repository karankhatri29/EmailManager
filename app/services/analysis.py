"""Reads one email for what it asks and by when, and keeps the result on the stored email.

Two questions, both answered from the words of the message alone: what should the reader do (actions.py) and
which date matters (temporal.py). Only mail that asks for something is analysed; everything else stays empty.
"""

import logging
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import SUMMARIZED_CATEGORIES
from ..db.models import Email
from ..repositories import settings as settings_repo
from .actions import extract_action_title
from .temporal import date_order_for_timezone, extract_deadline

logger = logging.getLogger(__name__)

NLP_VERSION = 1  # bump when extraction changes, so stored results are recomputed
EMPTY: dict[str, Any] = {
    "due_date": None,
    "due_kind": None,
    "due_text": None,
    "due_time": None,
    "due_confidence": None,
}


def _sent(value: datetime | None) -> datetime:
    value = value or datetime.now(timezone.utc)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def analyse(subject: str, body: str, sent_at: datetime | None, order: str = "DMY") -> dict:
    """The stored analysis fields: task, due_* and nlp_version."""
    found = extract_deadline(subject, body, _sent(sent_at), order)
    fields = dict(EMPTY)
    if found:
        fields.update(
            due_date=found.day,
            due_kind=found.kind,
            due_text=found.text[:80],
            due_time=found.time,
            due_confidence=found.confidence,
        )
    return {"task": extract_action_title(subject, body)[:255], "nlp_version": NLP_VERSION, **fields}


def order_for_user(db: Session, user_id: int) -> str:
    row = settings_repo.find(db, user_id)  # read-only: never creates the row from a background sync
    return date_order_for_timezone(row.timezone if row else None)


def analyse_email(email: Email, order: str = "DMY") -> None:
    """(Re)analyses a stored email in place; mail that is not actionable is cleared."""
    if email.category in SUMMARIZED_CATEGORIES:
        for name, value in analyse(email.subject, email.body, email.date, order).items():
            setattr(email, name, value)
    else:
        email.nlp_version = NLP_VERSION
        email.task = None
        for name, value in EMPTY.items():
            setattr(email, name, value)


def reanalyze_outdated(db: Session, user_id: int, limit: int = 200) -> int:
    """Analyses actionable mail stored before this version (or before analysis existed), newest first."""
    rows = list(
        db.scalars(
            select(Email)
            .where(
                Email.user_id == user_id,
                Email.nlp_version < NLP_VERSION,
                Email.category.in_(SUMMARIZED_CATEGORIES),
            )
            .order_by(Email.date.desc())
            .limit(limit)
        )
    )
    if not rows:
        return 0
    order = order_for_user(db, user_id)
    for row in rows:
        try:
            analyse_email(row, order)
        except Exception:  # one odd message must not block the rest
            logger.warning("Could not analyse email %s", row.id, exc_info=True)
            row.nlp_version = NLP_VERSION
    db.commit()
    return len(rows)


def as_date(value: date | datetime | None) -> date | None:
    return value.date() if isinstance(value, datetime) else value
