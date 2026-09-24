from datetime import datetime, timedelta, timezone

import pytest

from app.repositories import emails as emails_repo
from tests.conftest import stored_email


def _day(offset):
    d = (datetime.now(timezone.utc) + timedelta(days=offset)).date()
    return f"{d.isoformat()}T00:00:00Z"


def _todo(client, title="Task", **fields):
    r = client.post("/api/activities", json={"title": title, **fields})
    assert r.status_code == 201, r.text
    return r.json()


def _titles(client, **params):
    return [t["title"] for t in client.get("/api/todos", params=params).json()]


# --- priority and course on tasks ----------------------------------------------------------------


def test_new_todos_default_to_normal_priority_and_no_course(auth_client):
    todo = _todo(auth_client)
    assert todo["priority"] == 2 and todo["course"] is None


def test_priority_and_course_can_be_set_and_changed(auth_client):
    todo = _todo(auth_client, "Lab report", priority=3, course="Physics", start_at=_day(2), all_day=True)
    assert todo["priority"] == 3 and todo["course"] == "Physics"

    changed = auth_client.patch(
        f"/api/activities/{todo['id']}", json={"priority": 1, "course": "Chemistry"}
    ).json()
    assert changed["priority"] == 1 and changed["course"] == "Chemistry"
    assert auth_client.patch(f"/api/activities/{todo['id']}", json={"course": None}).json()["course"] is None
    assert (
        auth_client.patch(f"/api/activities/{todo['id']}", json={"title": "Renamed"}).json()["priority"] == 1
    )


@pytest.mark.parametrize(
    "payload", [{"priority": 0}, {"priority": 4}, {"priority": "high"}, {"course": "x" * 121}]
)
def test_invalid_priority_or_course_is_rejected(auth_client, payload):
    assert auth_client.post("/api/activities", json={"title": "Task", **payload}).status_code == 422
    todo = _todo(auth_client)
    assert auth_client.patch(f"/api/activities/{todo['id']}", json=payload).status_code == 422


def test_priority_cannot_be_cleared(auth_client):
    todo = _todo(auth_client)
    assert auth_client.patch(f"/api/activities/{todo['id']}", json={"priority": None}).status_code == 422


# --- the list ------------------------------------------------------------------------------------


def test_open_todos_are_ordered_by_due_date_then_priority_with_undated_last(auth_client):
    _todo(auth_client, "undated high", priority=3)
    _todo(auth_client, "later", start_at=_day(9), all_day=True)
    _todo(auth_client, "soon low", start_at=_day(1), all_day=True, priority=1)
    _todo(auth_client, "soon high", start_at=_day(1), all_day=True, priority=3)
    _todo(auth_client, "overdue", start_at=_day(-3), all_day=True)
    _todo(auth_client, "undated normal")
    assert _titles(auth_client) == [
        "overdue",
        "soon high",
        "soon low",
        "later",
        "undated high",
        "undated normal",
    ]


def test_finished_todos_are_listed_separately_newest_first(auth_client):
    a, b, _open = _todo(auth_client, "a"), _todo(auth_client, "b"), _todo(auth_client, "still open")
    auth_client.patch(f"/api/activities/{a['id']}", json={"status": "done"})
    auth_client.patch(f"/api/activities/{b['id']}", json={"status": "done"})

    assert _titles(auth_client) == ["still open"]
    assert _titles(auth_client, status="done") == ["b", "a"]
    assert set(_titles(auth_client, status="all")) == {"a", "b", "still open"}
    auth_client.patch(f"/api/activities/{a['id']}", json={"status": "todo"})  # reopened
    assert set(_titles(auth_client)) == {"a", "still open"}


def test_filter_by_course_ignores_case(auth_client):
    _todo(auth_client, "physics 1", course="Physics")
    _todo(auth_client, "physics 2", course="physics")
    _todo(auth_client, "maths", course="Maths")
    _todo(auth_client, "no course")
    assert set(_titles(auth_client, course="PHYSICS")) == {"physics 1", "physics 2"}
    assert _titles(auth_client, course="History") == []


def test_todos_include_tasks_created_from_email(auth_client, db, account):
    emails_repo.upsert_many(
        db,
        [
            stored_email(
                account,
                "m1",
                category="Urgent / Action Required",
                subject="Submit form",
                body="please submit the form",
            )
        ],
    )
    from app.services.activities_service import create_activities_for_emails

    create_activities_for_emails(
        db,
        [
            stored_email(
                account,
                "m1",
                category="Urgent / Action Required",
                subject="Submit form",
                body="please submit the form",
            )
        ],
    )
    todos = auth_client.get("/api/todos").json()
    assert len(todos) == 1 and todos[0]["source"] == "email" and todos[0]["email_id"] == f"{account.id}:m1"


def test_the_list_can_be_limited_and_validates_its_parameters(auth_client):
    for i in range(5):
        _todo(auth_client, f"t{i}")
    assert len(auth_client.get("/api/todos", params={"limit": 2}).json()) == 2
    for bad in ({"limit": 0}, {"limit": 5000}, {"status": "archived"}):
        assert auth_client.get("/api/todos", params=bad).status_code == 422


def test_todos_are_private(auth_client, bob_client):
    _todo(auth_client, "alices", course="Physics")
    _todo(bob_client, "bobs")
    assert _titles(auth_client) == ["alices"] and _titles(bob_client) == ["bobs"]
    assert _titles(bob_client, course="Physics") == []
