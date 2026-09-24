from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from icalendar import Calendar

from app.db.models import ClassSlot
from app.services.ics import build_calendar
from app.services.timetable import conflict_message, default_color, describe, time_range

TODAY = date(2026, 9, 24)  # a Thursday


def _slot(**fields):
    base = dict(
        id=1, user_id=1, title="Physics", code=None, weekday=0, start_time=time(9, 0), end_time=time(10, 30),
        room=None, instructor=None, color="#3b82f6", term_start=None, term_end=None, notes=None,
    )  # fmt: skip
    return ClassSlot(**{**base, **fields})


def _events(ics):
    return [c for c in Calendar.from_ical(ics).walk() if c.name == "VEVENT"]


def test_a_class_becomes_a_weekly_recurring_event_in_the_students_time_zone():
    slot = _slot(
        code="PHY101",
        room="Lab 3",
        instructor="Dr Rao",
        notes="Bring the manual",
        term_start=date(2026, 8, 3),
        term_end=date(2026, 12, 14),
    )
    ics = build_calendar([], [slot], "Europe/Amsterdam", TODAY)
    (event,) = _events(ics)

    assert str(event["summary"]) == "Physics (PHY101)" and str(event["location"]) == "Lab 3"
    assert str(event["description"]) == "Dr Rao\nBring the manual"
    assert event["dtstart"].dt == datetime(
        2026, 8, 3, 9, 0, tzinfo=ZoneInfo("Europe/Amsterdam")
    )  # the first Monday of the term
    assert event["dtend"].dt == datetime(2026, 8, 3, 10, 30, tzinfo=ZoneInfo("Europe/Amsterdam"))
    rule = event["rrule"]
    assert rule["FREQ"] == ["WEEKLY"] and rule["BYDAY"] == ["MO"]
    assert rule["UNTIL"][0] == datetime(
        2026, 12, 14, 9, 30, tzinfo=timezone.utc
    )  # 10:30 Amsterdam time, in UTC
    assert (
        b"TZID=Europe/Amsterdam" in ics and b"BEGIN:VTIMEZONE" in ics
    )  # times survive daylight-saving changes
    assert str(event["uid"]) == "class-1@email-prioritizer"


def test_without_a_term_the_class_repeats_from_its_next_occurrence_with_no_end():
    ics = build_calendar([], [_slot(weekday=1)], "UTC", TODAY)  # Tuesday; today is a Thursday
    (event,) = _events(ics)
    assert event["dtstart"].dt.date() == date(2026, 9, 29)
    assert "UNTIL" not in event["rrule"]


def test_a_term_starting_mid_week_starts_on_the_right_weekday():
    (event,) = _events(
        build_calendar([], [_slot(weekday=4, term_start=date(2026, 8, 3))], "UTC", TODAY)
    )  # Friday
    assert event["dtstart"].dt.date() == date(2026, 8, 7)


def test_a_class_on_the_term_start_day_starts_that_day():
    (event,) = _events(build_calendar([], [_slot(weekday=0, term_start=date(2026, 8, 3))], "UTC", TODAY))
    assert event["dtstart"].dt.date() == date(2026, 8, 3)


def test_several_meetings_and_optional_fields():
    slots = [_slot(id=1, weekday=0), _slot(id=2, weekday=2, title="Maths", code="MAT1")]
    events = _events(build_calendar([], slots, "UTC", TODAY))
    assert [str(e["summary"]) for e in events] == ["Physics", "Maths (MAT1)"]
    assert [e["rrule"]["BYDAY"] for e in events] == [["MO"], ["WE"]]
    assert "location" not in events[0] and "description" not in events[0]


def test_the_feed_is_valid_without_classes():
    assert _events(build_calendar([], [], "UTC", TODAY)) == []


def test_default_colours_are_stable_and_come_from_the_palette():
    assert default_color("Physics") == default_color("  physics ") == default_color("PHYSICS")
    assert default_color("Physics").startswith("#") and len(default_color("Physics")) == 7
    assert (
        len({default_color(t) for t in ("Physics", "Maths", "History", "Art", "Music", "Biology", "Law")}) > 3
    )


def test_descriptions():
    slot = _slot(weekday=2)
    assert describe(slot) == "Physics on Wednesday 09:00-10:30"
    assert conflict_message(slot) == "That time overlaps with Physics on Wednesday 09:00-10:30."
    assert time_range(slot) == "09:00-10:30"
