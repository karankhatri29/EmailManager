from datetime import datetime, timezone
from unittest.mock import patch

from icalendar import Calendar

from app.repositories import settings as settings_repo
from app.services import briefing

CLASS = {"title": "Physics", "weekdays": [0], "start_time": "09:00", "end_time": "10:30"}


def _feed(client):
    url = client.get("/api/calendar/feed").json()["url"].removeprefix("http://testserver")
    return client.get(url)


def _events(response):
    return [c for c in Calendar.from_ical(response.content).walk() if c.name == "VEVENT"]


def test_classes_appear_in_the_calendar_feed_as_recurring_events(auth_client):
    auth_client.put("/api/settings", json={"timezone": "Asia/Kolkata"})
    auth_client.post(
        "/api/timetable",
        json={**CLASS, "room": "Lab 3", "term_start": "2026-08-03", "term_end": "2026-12-14"},
    )
    auth_client.post(
        "/api/activities", json={"title": "Report due", "start_at": "2026-10-15T00:00:00Z", "all_day": True}
    )

    events = _events(_feed(auth_client))
    by_summary = {str(e["summary"]): e for e in events}
    assert set(by_summary) == {"Physics", "Report due"}
    physics = by_summary["Physics"]
    assert physics["dtstart"].dt.tzinfo is not None and "Kolkata" in str(physics["dtstart"].dt.tzinfo)
    assert (
        physics["dtstart"].dt.hour == 9
        and physics["rrule"]["BYDAY"] == ["MO"]
        and str(physics["location"]) == "Lab 3"
    )


def test_the_feed_only_contains_the_owners_classes(auth_client, bob_client):
    auth_client.post("/api/timetable", json=CLASS)
    bob_client.post("/api/timetable", json={**CLASS, "title": "Bobs class"})
    assert [str(e["summary"]) for e in _events(_feed(auth_client))] == ["Physics"]
    assert [str(e["summary"]) for e in _events(_feed(bob_client))] == ["Bobs class"]


def test_the_feed_works_with_only_classes(auth_client):
    auth_client.post("/api/timetable", json=CLASS)
    assert len(_events(_feed(auth_client))) == 1


# --- briefing ------------------------------------------------------------------------------------


def test_todays_classes_are_in_the_briefing_with_time_and_room(db, user, auth_client):
    today = datetime.now(timezone.utc).date()
    auth_client.post("/api/timetable", json={**CLASS, "weekdays": [today.weekday()], "room": "Hall A"})
    auth_client.post(
        "/api/timetable",
        json={"title": "Maths", "weekdays": [today.weekday()], "start_time": "11:00", "end_time": "12:00"},
    )
    other_day = (today.weekday() + 1) % 7
    auth_client.post(
        "/api/timetable",
        json={"title": "Tomorrow only", "weekdays": [other_day], "start_time": "09:00", "end_time": "10:00"},
    )

    b = auth_client.get("/api/briefing").json()
    assert b["classes"] == [
        {"title": "Physics", "time": "09:00-10:30", "room": "Hall A"},
        {"title": "Maths", "time": "11:00-12:00", "room": None},
    ]


def test_classes_alone_make_the_briefing_non_empty_and_are_rendered(db, user, auth_client):
    today = datetime.now(timezone.utc).date()
    auth_client.post("/api/timetable", json={**CLASS, "weekdays": [today.weekday()], "room": "Hall A"})
    settings = settings_repo.get_or_create(db, user.id)
    with patch.object(briefing.ai_summarizer, "generate_text", side_effect=RuntimeError):
        b = briefing.build_briefing(db, user, settings)
    assert not briefing.is_empty(b)
    text, html = briefing.render_text(b), briefing.render_html(b)
    assert "CLASSES TODAY" in text and "09:00-10:30  Physics (Hall A)" in text
    assert "Classes today" in html and "Physics" in html and "Hall A" in html


def test_a_class_outside_its_term_is_not_in_the_briefing(user, auth_client):
    today = datetime.now(timezone.utc).date()
    auth_client.post(
        "/api/timetable",
        json={**CLASS, "weekdays": [today.weekday()], "term_start": "2020-01-01", "term_end": "2020-05-01"},
    )
    assert auth_client.get("/api/briefing").json()["classes"] == []


def test_class_details_from_the_user_are_escaped_in_the_email(db, user, auth_client):
    today = datetime.now(timezone.utc).date()
    auth_client.post(
        "/api/timetable",
        json={
            "title": "<script>alert(1)</script>",
            "weekdays": [today.weekday()],
            "start_time": "09:00",
            "end_time": "10:00",
            "room": "<b>Room</b>",
        },
    )
    settings = settings_repo.get_or_create(db, user.id)
    html = briefing.render_html(briefing.build_briefing(db, user, settings))
    assert "<script>" not in html and "<b>Room" not in html and "&lt;script&gt;" in html
