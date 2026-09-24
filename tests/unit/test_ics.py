from datetime import datetime, timezone

from icalendar import Calendar

from app.db.models import Activity
from app.services.ics import build_calendar


def _activity(id, title, start, **kw):
    return Activity(id=id, user_id=1, title=title, start_at=start, all_day=kw.pop("all_day", False), **kw)


def _events(ics: bytes):
    return [c for c in Calendar.from_ical(ics).walk() if c.name == "VEVENT"]


def test_all_day_event_uses_date_values_with_exclusive_end():
    ics = build_calendar(
        [_activity(1, "Report due", datetime(2026, 10, 15, tzinfo=timezone.utc), all_day=True)]
    )
    assert b"DTSTART;VALUE=DATE:20261015" in ics and b"DTEND;VALUE=DATE:20261016" in ics
    event = _events(ics)[0]
    assert str(event["summary"]) == "Report due" and str(event["uid"]) == "activity-1@email-prioritizer"


def test_timed_event_defaults_to_one_hour():
    ics = build_calendar([_activity(2, "Call", datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc))])
    assert b"DTSTART:20261015T090000Z" in ics and b"DTEND:20261015T100000Z" in ics


def test_timed_event_with_explicit_end_and_notes():
    a = _activity(
        3, "Workshop", datetime(2026, 10, 15, 9, tzinfo=timezone.utc),
        end_at=datetime(2026, 10, 15, 11, 30, tzinfo=timezone.utc), notes="Room 4",
    )  # fmt: skip
    ics = build_calendar([a])
    assert b"DTEND:20261015T113000Z" in ics and str(_events(ics)[0]["description"]) == "Room 4"


def test_unscheduled_activities_are_skipped():
    ics = build_calendar([_activity(4, "Backlog", None)])
    assert _events(ics) == []


def test_special_characters_are_escaped_and_parse_back():
    ics = build_calendar([_activity(5, "Buy milk, eggs; bread", datetime(2026, 10, 15, tzinfo=timezone.utc))])
    assert b"Buy milk\\, eggs\\; bread" in ics
    assert str(_events(ics)[0]["summary"]) == "Buy milk, eggs; bread"


def test_calendar_headers_and_empty_feed_are_valid():
    ics = build_calendar([])
    assert ics.startswith(b"BEGIN:VCALENDAR") and b"VERSION:2.0" in ics and b"PRODID" in ics
    assert Calendar.from_ical(ics) is not None


def test_naive_datetimes_are_treated_as_utc():
    ics = build_calendar([_activity(6, "Naive", datetime(2026, 10, 15, 9, 0))])
    assert b"DTSTART:20261015T090000Z" in ics
