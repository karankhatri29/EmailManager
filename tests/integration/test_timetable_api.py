from datetime import date, timedelta

import pytest

PHYSICS = {"title": "Physics", "weekdays": [0, 2], "start_time": "09:00", "end_time": "10:30"}


def _add(client, **fields):
    r = client.post("/api/timetable", json={**PHYSICS, **fields})
    assert r.status_code == 201, r.text
    return r.json()


def _slots(client):
    return client.get("/api/timetable").json()


# --- adding classes ------------------------------------------------------------------------------


def test_one_class_on_several_weekdays_creates_one_slot_per_day(auth_client):
    created = _add(
        auth_client,
        code="PHY101",
        room="Lab 3",
        instructor="Dr Rao",
        term_start="2026-08-01",
        term_end="2026-12-15",
        notes="Bring the manual",
    )
    assert [(s["weekday"], s["start_time"], s["end_time"]) for s in created] == [
        (0, "09:00", "10:30"),
        (2, "09:00", "10:30"),
    ]
    first = created[0]
    assert first["title"] == "Physics" and first["code"] == "PHY101" and first["room"] == "Lab 3"
    assert (
        first["instructor"] == "Dr Rao"
        and first["term_start"] == "2026-08-01"
        and first["term_end"] == "2026-12-15"
    )
    assert (
        first["notes"] == "Bring the manual" and first["color"].startswith("#") and len(first["color"]) == 7
    )
    assert created[0]["color"] == created[1]["color"]  # one colour per course
    assert len(_slots(auth_client)) == 2


def test_the_same_course_always_gets_the_same_colour_unless_chosen(auth_client):
    a = _add(auth_client, title="Chemistry", weekdays=[1])[0]["color"]
    auth_client.delete("/api/timetable", params={"course": "Chemistry"})
    assert _add(auth_client, title="chemistry ", weekdays=[1])[0]["color"] == a
    assert _add(auth_client, title="Maths", weekdays=[3], color="#AABBCC")[0]["color"] == "#AABBCC"


def test_values_are_cleaned_up(auth_client):
    (slot,) = _add(auth_client, title="  Biology  ", weekdays=[4, 4], code="  ", room=" B2 ", instructor="")
    assert (
        slot["title"] == "Biology"
        and slot["code"] is None
        and slot["room"] == "B2"
        and slot["instructor"] is None
    )
    assert len(_slots(auth_client)) == 1  # a repeated weekday is one meeting


@pytest.mark.parametrize(
    "changes",
    [
        {"title": ""},
        {"title": "   "},
        {"title": "x" * 121},
        {"weekdays": []},
        {"weekdays": [7]},
        {"weekdays": [-1]},
        {"weekdays": [0, 1, 2, 3, 4, 5, 6, 0]},
        {"start_time": "10:30", "end_time": "09:00"},
        {"start_time": "09:00", "end_time": "09:00"},
        {"start_time": "25:00"},
        {"color": "red"},
        {"color": "#12345"},
        {"term_start": "2026-12-01", "term_end": "2026-08-01"},
        {"notes": "x" * 2001},
    ],
)
def test_invalid_classes_are_rejected(auth_client, changes):
    assert auth_client.post("/api/timetable", json={**PHYSICS, **changes}).status_code == 422


def test_missing_required_fields_are_rejected(auth_client):
    for missing in ("title", "weekdays", "start_time", "end_time"):
        payload = {k: v for k, v in PHYSICS.items() if k != missing}
        assert auth_client.post("/api/timetable", json=payload).status_code == 422


# --- overlaps ------------------------------------------------------------------------------------


def test_overlapping_classes_are_refused_with_a_clear_message(auth_client):
    _add(auth_client)
    r = auth_client.post(
        "/api/timetable", json={"title": "Maths", "weekdays": [2], "start_time": "10:00", "end_time": "11:00"}
    )
    assert (
        r.status_code == 409
        and r.json()["detail"] == "That time overlaps with Physics on Wednesday 09:00-10:30."
    )
    assert len(_slots(auth_client)) == 2  # nothing was added


def test_a_clash_on_any_weekday_rejects_the_whole_class(auth_client):
    _add(auth_client, title="Maths", weekdays=[2], start_time="10:00", end_time="11:00")
    r = auth_client.post(
        "/api/timetable", json={**PHYSICS, "weekdays": [0, 1, 2], "start_time": "10:30", "end_time": "11:30"}
    )
    assert r.status_code == 409
    assert [s["title"] for s in _slots(auth_client)] == ["Maths"]  # not even the Monday/Tuesday slots


@pytest.mark.parametrize(
    "start,end,clashes",
    [
        ("08:00", "09:00", False),  # ends exactly when Physics starts
        ("10:30", "11:30", False),  # starts exactly when Physics ends
        ("08:30", "09:01", True),
        ("10:29", "12:00", True),
        ("09:30", "10:00", True),  # inside
        ("08:00", "12:00", True),  # around
        ("09:00", "10:30", True),  # identical
    ],
)
def test_back_to_back_is_fine_overlap_is_not(auth_client, start, end, clashes):
    _add(auth_client, weekdays=[0])
    r = auth_client.post(
        "/api/timetable", json={"title": "Other", "weekdays": [0], "start_time": start, "end_time": end}
    )
    assert r.status_code == (409 if clashes else 201)


def test_the_same_time_on_a_different_weekday_is_fine(auth_client):
    _add(auth_client, weekdays=[0])
    assert (
        auth_client.post("/api/timetable", json={**PHYSICS, "title": "Maths", "weekdays": [1]}).status_code
        == 201
    )


def test_classes_in_different_terms_may_share_a_slot(auth_client):
    _add(auth_client, weekdays=[0], term_start="2026-01-05", term_end="2026-05-01")
    other_term = {"title": "Chemistry", "weekdays": [0], "start_time": "09:00", "end_time": "10:30"}
    assert (
        auth_client.post(
            "/api/timetable", json={**other_term, "term_start": "2026-08-01", "term_end": "2026-12-15"}
        ).status_code
        == 201
    )
    assert (
        auth_client.post(
            "/api/timetable",
            json={**other_term, "title": "Bio", "term_start": "2026-04-01", "term_end": "2026-09-01"},
        ).status_code
        == 409
    )
    assert (
        auth_client.post("/api/timetable", json={**other_term, "title": "Open"}).status_code == 409
    )  # no term = always


# --- editing -------------------------------------------------------------------------------------


def test_edit_one_meeting(auth_client):
    monday, wednesday = _add(auth_client)
    r = auth_client.patch(
        f"/api/timetable/{monday['id']}", json={"room": "Hall A", "start_time": "11:00", "end_time": "12:00"}
    )
    body = r.json()
    assert (
        r.status_code == 200
        and body["room"] == "Hall A"
        and body["start_time"] == "11:00"
        and body["weekday"] == 0
    )
    assert [s["start_time"] for s in _slots(auth_client)] == ["11:00", "09:00"]  # Wednesday untouched


def test_moving_a_class_onto_another_is_refused_but_onto_itself_is_fine(auth_client):
    monday, _ = _add(auth_client)
    other = _add(auth_client, title="Maths", weekdays=[1], start_time="09:00", end_time="10:00")[0]
    assert auth_client.patch(f"/api/timetable/{other['id']}", json={"weekday": 0}).status_code == 409
    assert (
        auth_client.patch(f"/api/timetable/{monday['id']}", json={"end_time": "10:00"}).status_code == 200
    )  # own old slot


def test_edit_the_whole_course_at_once(auth_client):
    monday, wednesday = _add(auth_client, room="Lab 3")
    r = auth_client.patch(
        f"/api/timetable/{monday['id']}",
        json={
            "title": "Physics II",
            "color": "#111111",
            "instructor": "Dr Iyer",
            "room": "Lab 9",
            "apply_to_course": True,
        },
    )
    assert r.status_code == 200
    by_id = {s["id"]: s for s in _slots(auth_client)}
    assert {s["title"] for s in by_id.values()} == {"Physics II"} and {
        s["color"] for s in by_id.values()
    } == {"#111111"}
    assert {s["instructor"] for s in by_id.values()} == {"Dr Iyer"}
    assert (
        by_id[monday["id"]]["room"] == "Lab 9" and by_id[wednesday["id"]]["room"] == "Lab 3"
    )  # the room is per meeting


def test_without_apply_to_course_only_that_meeting_changes(auth_client):
    monday, wednesday = _add(auth_client)
    auth_client.patch(f"/api/timetable/{monday['id']}", json={"instructor": "Dr Iyer"})
    assert {s["id"]: s["instructor"] for s in _slots(auth_client)} == {
        monday["id"]: "Dr Iyer",
        wednesday["id"]: None,
    }


def test_a_term_change_for_the_whole_course_is_checked_against_other_classes(auth_client):
    _add(auth_client, weekdays=[0], term_start="2026-01-05", term_end="2026-05-01")
    other = _add(
        auth_client,
        title="Maths",
        weekdays=[0],
        start_time="09:00",
        end_time="10:30",
        term_start="2026-08-01",
        term_end="2026-12-15",
    )[0]
    r = auth_client.patch(
        f"/api/timetable/{other['id']}", json={"term_start": "2026-03-01", "apply_to_course": True}
    )
    assert r.status_code == 409


def test_edit_validation(auth_client):
    slot = _add(auth_client)[0]
    url = f"/api/timetable/{slot['id']}"
    for bad in (
        {"title": None},
        {"title": ""},
        {"weekday": 7},
        {"start_time": None},
        {"end_time": "08:00"},
        {"start_time": "11:00"},
        {"color": "blue"},
        {"term_start": "2027-01-01", "term_end": "2026-01-01"},
    ):
        assert auth_client.patch(url, json=bad).status_code == 422, bad
    assert auth_client.patch("/api/timetable/9999", json={"room": "x"}).status_code == 404


# --- deleting ------------------------------------------------------------------------------------


def test_delete_one_meeting_or_the_whole_course(auth_client):
    monday, _ = _add(auth_client)
    _add(auth_client, title="Maths", weekdays=[1])
    assert auth_client.delete(f"/api/timetable/{monday['id']}").status_code == 204
    assert [(s["title"], s["weekday"]) for s in _slots(auth_client)] == [("Maths", 1), ("Physics", 2)]

    assert auth_client.delete("/api/timetable", params={"course": "physics"}).status_code == 204  # any case
    assert [s["title"] for s in _slots(auth_client)] == ["Maths"]
    assert auth_client.delete("/api/timetable", params={"course": "Physics"}).status_code == 404
    assert auth_client.delete(f"/api/timetable/{monday['id']}").status_code == 404


# --- reading -------------------------------------------------------------------------------------


def test_the_timetable_is_ordered_by_day_then_time(auth_client):
    _add(auth_client, title="Late", weekdays=[0], start_time="14:00", end_time="15:00")
    _add(auth_client, title="Early", weekdays=[0], start_time="08:00", end_time="09:00")
    _add(auth_client, title="Tuesday", weekdays=[1], start_time="07:00", end_time="08:00")
    assert [s["title"] for s in _slots(auth_client)] == ["Early", "Late", "Tuesday"]


def _next_weekday(target: int, after: date) -> date:
    return after + timedelta(days=(target - after.weekday()) % 7)


def test_classes_on_a_given_day_respect_the_weekday_and_the_term(auth_client):
    monday = _next_weekday(0, date(2026, 9, 1))  # a Monday in the middle of the term below
    _add(auth_client, title="In term", weekdays=[0], term_start="2026-08-01", term_end="2026-12-15")
    _add(
        auth_client,
        title="Ended",
        weekdays=[0],
        start_time="12:00",
        end_time="13:00",
        term_start="2026-01-01",
        term_end="2026-05-01",
    )
    _add(auth_client, title="Always", weekdays=[0], start_time="14:00", end_time="15:00")
    _add(auth_client, title="Tuesday", weekdays=[1], start_time="09:00", end_time="10:00")

    def titles(day):
        response = auth_client.get("/api/timetable/day", params={"date": day.isoformat()})
        return [s["title"] for s in response.json()]

    assert titles(monday) == ["In term", "Always"]
    assert titles(monday + timedelta(days=1)) == ["Tuesday"]
    assert titles(date(2027, 3, 1)) == ["Always"]  # a Monday after the terms ended: only the term-less class
    assert titles(monday + timedelta(days=2)) == []
    assert auth_client.get("/api/timetable/day", params={"date": "not-a-date"}).status_code == 422
    assert isinstance(auth_client.get("/api/timetable/day").json(), list)  # defaults to today


def test_courses_combine_the_timetable_and_todo_courses(auth_client):
    _add(auth_client, title="Physics", weekdays=[0])
    _add(auth_client, title="maths", weekdays=[1], start_time="09:00", end_time="10:00")
    auth_client.post("/api/activities", json={"title": "Essay", "course": "History"})
    auth_client.post("/api/activities", json={"title": "Lab", "course": "PHYSICS"})
    assert auth_client.get("/api/courses").json() == ["History", "maths", "Physics"]


# --- privacy and limits --------------------------------------------------------------------------


def test_timetables_are_private(auth_client, bob_client):
    slot = _add(auth_client)[0]
    assert bob_client.get("/api/timetable").json() == []
    assert _add(bob_client, weekdays=[0])  # Bob can use the same time slot: separate timetables
    assert bob_client.patch(f"/api/timetable/{slot['id']}", json={"room": "x"}).status_code == 404
    assert bob_client.delete(f"/api/timetable/{slot['id']}").status_code == 404
    assert (
        bob_client.delete("/api/timetable", params={"course": "Physics"}).status_code == 204
    )  # only his own
    assert len(_slots(auth_client)) == 2


def test_the_timetable_size_is_limited(auth_client, monkeypatch):
    monkeypatch.setattr("app.repositories.timetable.MAX_SLOTS_PER_USER", 3)
    _add(auth_client, weekdays=[0, 1])
    assert (
        auth_client.post(
            "/api/timetable",
            json={
                **PHYSICS,
                "title": "Maths",
                "weekdays": [2, 3],
                "start_time": "11:00",
                "end_time": "12:00",
            },
        ).status_code
        == 422
    )
    assert (
        auth_client.post(
            "/api/timetable",
            json={**PHYSICS, "title": "Maths", "weekdays": [2], "start_time": "11:00", "end_time": "12:00"},
        ).status_code
        == 201
    )


def test_every_timetable_endpoint_requires_login(client):
    for method, path in (
        ("get", "/api/timetable"),
        ("post", "/api/timetable"),
        ("get", "/api/timetable/day"),
        ("get", "/api/courses"),
        ("patch", "/api/timetable/1"),
        ("delete", "/api/timetable/1"),
        ("delete", "/api/timetable?course=x"),
        ("get", "/api/todos"),
    ):
        kwargs = {"json": {}} if method in ("post", "patch") else {}
        assert getattr(client, method)(path, **kwargs).status_code == 401, path
