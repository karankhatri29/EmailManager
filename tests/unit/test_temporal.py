from datetime import date

import pytest

from app.services.temporal import (
    date_order_for_timezone,
    deadline_label,
    extract_deadline,
    strip_history_and_footer,
)
from tests.nlp_corpus import POSITIVES, SENT, TRAPS

TODAY = SENT.date()


def _found(subject, body, order="DMY"):
    return extract_deadline(subject, body, SENT, order)


@pytest.mark.parametrize("case", POSITIVES, ids=lambda c: c["name"])
def test_real_dates_are_found(case):
    found = _found(case["subject"], case["body"], case["order"])
    kind, day = case["expect"]
    assert found is not None, case["name"]
    assert (found.kind, found.day.isoformat() if found.day else None) == (kind, day)


@pytest.mark.parametrize("case", TRAPS, ids=lambda c: c["name"])
def test_things_that_only_look_like_dates_show_nothing(case):
    assert _found(case["subject"], case["body"], case["order"]) is None


# Wording the corpus does not contain: the rules are general, not a list of known sentences.
@pytest.mark.parametrize(
    "body,expected",
    [
        (
            "Kindly settle the outstanding balance on or before 9 November 2026.",
            ("deadline", date(2026, 11, 9)),
        ),
        ("Your tickets must be collected before Sat.", None),  # abbreviations are not guessed
        ("Applications close on 1st December 2026.", ("deadline", date(2026, 12, 1))),
        ("Please pay within two weeks.", ("deadline", date(2026, 10, 8))),
        ("The board will meet on Tuesday 6 October to vote.", ("event", date(2026, 10, 6))),
        ("Submit your answers before November 3rd, 2026.", ("deadline", date(2026, 11, 3))),
        ("We shipped it on 2 September and it arrived on time.", None),
        ("Remember to renew by next Monday.", ("deadline", date(2026, 9, 28))),
        ("Version 4.2.1 was released; call 2026 for details, 50/50 chance.", None),
    ],
)
def test_other_wordings(body, expected):
    found = _found("Note", body)
    assert (found and (found.kind, found.day)) == expected


def test_numeric_order_follows_the_readers_region_only_when_ambiguous():
    body = "Please reply by 03/04/2026."
    assert _found("x", body, "DMY") is None  # 3 April 2026 is already past
    body = "Please reply by 03/11/2026."
    assert _found("x", body, "DMY").day == date(2026, 11, 3)
    assert _found("x", body, "MDY") is None  # read month-first it is 11 March, already past
    assert _found("x", "Please reply by 25/11/2026.", "MDY").day == date(2026, 11, 25)  # 25 cannot be a month


def test_time_of_day_is_captured():
    found = _found("x", "Please confirm by 5 pm on 15 October.")
    assert found.time.hour == 17 and found.day == date(2026, 10, 15)


def test_dates_in_quoted_history_and_footers_are_ignored():
    body = "Thanks.\n\nOn Mon, 21 Sep 2026, Sam wrote:\n> Submit by 15 October 2026."
    assert strip_history_and_footer(body) == "Thanks."
    assert strip_history_and_footer("Hello\n-- \nSam") == "Hello"


def test_deadline_beats_event_and_result_keeps_the_phrase():
    found = _found("Course", "Register by 10 October. The exam is on 25 October.")
    assert found.kind == "deadline" and found.text == "by 10 October"
    assert any(other.day == date(2026, 10, 25) for other in found.others)


def test_empty_input():
    assert _found("", "") is None and _found(None, None) is None


@pytest.mark.parametrize(
    "kind,day,label",
    [
        ("deadline", date(2026, 9, 25), "Due Tomorrow"),
        ("deadline", date(2026, 9, 24), "Due Today"),
        ("deadline", date(2026, 9, 21), "Overdue by 3 days"),
        ("deadline", date(2026, 10, 2), "Due Fri 2 Oct"),
        ("event", date(2026, 10, 2), "Fri 2 Oct"),
        ("asap", None, "ASAP"),
        (None, None, "No date"),
    ],
)
def test_labels(kind, day, label):
    assert deadline_label(kind, day, TODAY) == label


@pytest.mark.parametrize(
    "zone,order",
    [
        ("America/New_York", "MDY"),
        ("Asia/Kolkata", "DMY"),
        ("Europe/London", "DMY"),
        ("UTC", "DMY"),
        (None, "DMY"),
        ("Nope/Zone", "DMY"),
    ],
)
def test_date_order_by_timezone(zone, order):
    assert date_order_for_timezone(zone) == order
