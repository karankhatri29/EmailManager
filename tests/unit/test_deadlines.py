from datetime import datetime, timezone

import pytest

from app.services.deadlines import NO_DEADLINE, resolve_deadline
from app.services.nlp_engine import extract_explicit_deadline

REF = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)  # a Thursday


def day(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


@pytest.mark.parametrize("value", [NO_DEADLINE, "", None])
def test_no_deadline(value):
    assert resolve_deadline(value, REF) is None


def test_immediate_means_the_day_the_email_was_sent():
    assert resolve_deadline("Immediate / End of Day Target", REF) == day(2026, 9, 24)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Upcoming Friday", day(2026, 9, 25)),
        ("Upcoming Thursday", day(2026, 9, 24)),  # same weekday = today
        ("Upcoming Monday", day(2026, 9, 28)),
        ("Upcoming Someday", None),
    ],
)
def test_upcoming_weekday(value, expected):
    assert resolve_deadline(value, REF) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Date: 15/10/2026", day(2026, 10, 15)),  # day-first
        ("Date: 22-07-27", day(2027, 7, 22)),  # two-digit year
        ("Date: 05.10", day(2026, 10, 5)),  # no year: next occurrence
        ("Date: 05.09", day(2027, 9, 5)),  # already passed this year -> next year
        ("Date: 22/09", day(2026, 9, 22)),  # just past (within a week) stays this year
    ],
)
def test_numeric_dates(value, expected):
    assert resolve_deadline(value, REF) == expected


@pytest.mark.parametrize(
    "value",
    ["Date: 31/02/2026", "Date: 15/10/2035", "Date: 01/01/2020", "Date: 13/13/2026"],
)
def test_implausible_or_invalid_numeric_dates_are_rejected(value):
    assert resolve_deadline(value, REF) is None


@pytest.mark.parametrize(
    "value,expected",
    [
        ("15 October", day(2026, 10, 15)),
        ("October 15", day(2026, 10, 15)),
        ("2Nd July", day(2027, 7, 2)),  # the NLP layer title-cases what it finds
        ("Tomorrow", day(2026, 9, 25)),
    ],
)
def test_textual_dates(value, expected):
    assert resolve_deadline(value, REF) == expected


def test_unparseable_text_is_none():
    assert resolve_deadline("Blah Blah", REF) is None


def test_naive_reference_is_treated_as_utc():
    assert resolve_deadline("Immediate / End of Day Target", datetime(2026, 9, 24, 12)) == day(2026, 9, 24)


@pytest.mark.parametrize(
    "text",
    ["submit by 15/10/2026", "need this asap", "send it by friday", "due 2nd july please", "nothing here"],
)
def test_resolver_understands_everything_the_extractor_emits(text):
    """extract_explicit_deadline's output format and resolve_deadline's parsing must stay in sync."""
    meta = extract_explicit_deadline(text, {})
    resolved = resolve_deadline(meta["value"], REF)
    if meta["value"] == NO_DEADLINE:
        assert resolved is None
    else:
        assert resolved is not None, meta
