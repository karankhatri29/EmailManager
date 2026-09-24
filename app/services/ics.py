"""iCalendar (.ics) feed of a user's scheduled activities, subscribable from any calendar app."""

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

from icalendar import Calendar, Event

from ..db.models import Activity


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def build_calendar(activities: Iterable[Activity]) -> bytes:
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

    return cal.to_ical()
