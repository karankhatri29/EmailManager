"""iCalendar (.ics) feed of a user's scheduled activities, subscribable from any calendar app."""

from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event

from ..db.models import Activity, ClassSlot
from .timetable import WEEKDAY_NAMES


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def build_calendar(
    activities: Iterable[Activity],
    class_slots: Iterable[ClassSlot] = (),
    tzname: str = "UTC",
    today: date | None = None,
) -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//Email Prioritizer//Activities//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "Email Prioritizer")

    now = datetime.now(timezone.utc)
    for activity in activities:
        if activity.start_at is None:
            continue
        start = _aware(activity.start_at)

        event = Event()
        event.add("uid", f"activity-{activity.id}@email-prioritizer")
        event.add("dtstamp", now)
        event.add("summary", activity.title)
        if activity.notes:
            event.add("description", activity.notes)

        if activity.all_day:
            end = _aware(activity.end_at) if activity.end_at else start
            event.add("dtstart", start.date())
            event.add("dtend", end.date() + timedelta(days=1))  # DTEND is exclusive for all-day events
        else:
            event.add("dtstart", start)
            event.add("dtend", _aware(activity.end_at) if activity.end_at else start + timedelta(hours=1))
        cal.add_component(event)

    tz = ZoneInfo(tzname)
    today = today or datetime.now(tz).date()
    for slot in class_slots:
        cal.add_component(_class_event(slot, tz, today))
    cal.add_missing_timezones()  # VTIMEZONE, so class times stay right across daylight-saving changes
    return cal.to_ical()


def _first_meeting(slot: ClassSlot, today: date) -> date:
    """The first date this weekly class meets on: the term start, else the next occurrence from today."""
    start = slot.term_start or today
    return start + timedelta(days=(slot.weekday - start.weekday()) % 7)


def _class_event(slot: ClassSlot, tz: ZoneInfo, today: date) -> Event:
    first = _first_meeting(slot, today)
    event = Event()
    event.add("uid", f"class-{slot.id}@email-prioritizer")
    event.add("dtstamp", datetime.now(timezone.utc))
    event.add("summary", f"{slot.title} ({slot.code})" if slot.code else slot.title)
    event.add("dtstart", datetime.combine(first, slot.start_time, tzinfo=tz))
    event.add("dtend", datetime.combine(first, slot.end_time, tzinfo=tz))
    rule: dict = {"FREQ": "WEEKLY", "BYDAY": WEEKDAY_NAMES[slot.weekday][:2].upper()}
    if slot.term_end:
        rule["UNTIL"] = datetime.combine(slot.term_end, slot.end_time, tzinfo=tz).astimezone(timezone.utc)
    event.add("rrule", rule)
    if slot.room:
        event.add("location", slot.room)
    details = [part for part in (slot.instructor, slot.notes) if part]
    if details:
        event.add("description", "\n".join(details))
    return event
