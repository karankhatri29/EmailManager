"""Turns the deadline text found by nlp_engine.extract_explicit_deadline into a real date."""

import re
from datetime import date, datetime, timedelta, timezone

import dateparser

NO_DEADLINE = "No Explicit Deadline Stated"
IMMEDIATE_PREFIX = "Immediate"
UPCOMING_PREFIX = "Upcoming "
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

# Dates further out than this from the email, or older than MAX_PAST_DAYS, are treated as false positives
# (e.g. "3/4" in a recipe or "1.5" in a version number).
MAX_PAST_DAYS = 7
MAX_FUTURE_DAYS = 366

_NUMERIC = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2,4}))?$")


def _midnight_utc(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def _parse_numeric(m: re.Match, ref_date: date) -> date | None:
    """Day-first numeric dates (15/06/2026, 22-07-26, 05.08). Missing year means the next occurrence."""
    day, month = int(m.group(1)), int(m.group(2))
    year_text = m.group(3)
    try:
        if year_text:
            year = int(year_text)
            return date(year + 2000 if year < 100 else year, month, day)
        candidate = date(ref_date.year, month, day)
        if candidate < ref_date - timedelta(days=MAX_PAST_DAYS):
            candidate = date(ref_date.year + 1, month, day)
        return candidate
    except ValueError:
        return None


def resolve_deadline(value: str, reference: datetime) -> datetime | None:
    """Midnight UTC of the deadline day, or None if there is no usable deadline.

    `reference` is when the email was sent; relative phrases ("Friday", "asap") resolve against it.
    """
    value = (value or "").strip()
    if not value or value == NO_DEADLINE:
        return None

    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    ref_date = reference.date()

    if value.startswith(IMMEDIATE_PREFIX):
        return _midnight_utc(ref_date)

    if value.startswith(UPCOMING_PREFIX):
        name = value[len(UPCOMING_PREFIX) :].lower()
        if name not in WEEKDAYS:
            return None
        days_ahead = (WEEKDAYS.index(name) - ref_date.weekday()) % 7
        return _midnight_utc(ref_date + timedelta(days=days_ahead))

    text = value.removeprefix("Date: ").strip()
    numeric = _NUMERIC.match(text)
    if numeric:
        found = _parse_numeric(numeric, ref_date)
    else:
        parsed = dateparser.parse(
            text,
            settings={
                "DATE_ORDER": "DMY",
                "PREFER_DATES_FROM": "future",
                "RELATIVE_BASE": reference.replace(tzinfo=None),
            },
        )
        found = parsed.date() if parsed else None

    if found is None:
        return None
    if not (ref_date - timedelta(days=MAX_PAST_DAYS) <= found <= ref_date + timedelta(days=MAX_FUTURE_DAYS)):
        return None
    return _midnight_utc(found)
