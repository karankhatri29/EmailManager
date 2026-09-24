from unittest.mock import patch

from tests.conftest import make_email, patch_provider

D = "2026-10-15T00:00:00Z"


def _create(client, **fields):
    r = client.post("/api/activities", json={"title": "Task", **fields})
    assert r.status_code == 201, r.text
    return r.json()


def test_create_manual_activity(auth_client):
    a = _create(
        auth_client,
        title="Dentist",
        notes="bring forms",
        start_at="2026-10-15T09:00:00Z",
        end_at="2026-10-15T10:00:00Z",
    )
    assert a["title"] == "Dentist" and a["notes"] == "bring forms"
    assert a["source"] == "manual" and a["status"] == "todo" and a["email_id"] is None
    assert a["start_at"].startswith("2026-10-15T09:00:00") and a["all_day"] is False


def test_all_day_requires_a_date(auth_client):
    assert _create(auth_client, start_at=D, all_day=True)["all_day"] is True
    assert _create(auth_client, all_day=True)["all_day"] is False  # unscheduled can't be all-day


def test_naive_datetimes_are_treated_as_utc(auth_client):
    assert _create(auth_client, start_at="2026-10-15T09:00:00")["start_at"] == "2026-10-15T09:00:00Z"


def test_create_validation(auth_client):
    bad = [
        {"title": ""},
        {"title": "x" * 300},
        {},
        {"title": "x", "end_at": D},  # end without start
        {"title": "x", "start_at": "2026-10-15T10:00:00Z", "end_at": "2026-10-15T09:00:00Z"},
        {"title": "x", "start_at": "not-a-date"},
    ]
    for payload in bad:
        assert auth_client.post("/api/activities", json=payload).status_code == 422, payload


def test_list_returns_range_plus_backlog_ordered(auth_client):
    _create(auth_client, title="later", start_at="2026-10-20T00:00:00Z", all_day=True)
    _create(auth_client, title="sooner", start_at="2026-10-16T00:00:00Z", all_day=True)
    _create(auth_client, title="outside", start_at="2026-12-01T00:00:00Z", all_day=True)
    _create(auth_client, title="backlog")

    def titles(**params):
        return [a["title"] for a in auth_client.get("/api/activities", params=params).json()]

    window = {"start": "2026-10-01T00:00:00Z", "end": "2026-11-01T00:00:00Z"}
    assert titles(**window) == ["sooner", "later", "backlog"]
    assert titles(**window, include_unscheduled="false") == ["sooner", "later"]
    assert titles() == ["sooner", "later", "outside", "backlog"]


def test_patch_changes_only_the_given_fields(auth_client):
    a = _create(auth_client, title="Original", notes="keep me", start_at=D, all_day=True)
    r = auth_client.patch(f"/api/activities/{a['id']}", json={"title": "Renamed"})
    body = r.json()
    assert r.status_code == 200 and body["title"] == "Renamed"
    assert body["notes"] == "keep me" and body["start_at"] == a["start_at"] and body["all_day"] is True


def test_move_to_another_day(auth_client):
    a = _create(auth_client, start_at=D, all_day=True)
    r = auth_client.patch(f"/api/activities/{a['id']}", json={"start_at": "2026-10-22T00:00:00Z"})
    assert r.json()["start_at"].startswith("2026-10-22")


def test_mark_done_and_reopen(auth_client):
    a = _create(auth_client)
    assert auth_client.patch(f"/api/activities/{a['id']}", json={"status": "done"}).json()["status"] == "done"
    assert auth_client.patch(f"/api/activities/{a['id']}", json={"status": "todo"}).json()["status"] == "todo"
    assert auth_client.patch(f"/api/activities/{a['id']}", json={"status": "archived"}).status_code == 422


def test_unscheduling_clears_the_schedule(auth_client):
    a = _create(auth_client, start_at="2026-10-15T09:00:00Z", end_at="2026-10-15T10:00:00Z")
    body = auth_client.patch(f"/api/activities/{a['id']}", json={"start_at": None}).json()
    assert body["start_at"] is None and body["end_at"] is None and body["all_day"] is False


def test_scheduling_a_backlog_item(auth_client):
    a = _create(auth_client)
    body = auth_client.patch(f"/api/activities/{a['id']}", json={"start_at": D, "all_day": True}).json()
    assert body["start_at"].startswith("2026-10-15") and body["all_day"] is True


def test_patch_validation(auth_client):
    a = _create(auth_client, start_at="2026-10-15T09:00:00Z")
    url = f"/api/activities/{a['id']}"
    assert auth_client.patch(url, json={"title": None}).status_code == 422
    assert auth_client.patch(url, json={"title": ""}).status_code == 422
    assert auth_client.patch(url, json={"end_at": "2026-10-15T08:00:00Z"}).status_code == 422  # before start
    assert auth_client.patch(url, json={"all_day": None}).status_code == 422
    backlog = _create(auth_client)
    assert auth_client.patch(f"/api/activities/{backlog['id']}", json={"end_at": D}).status_code == 422


def test_delete(auth_client):
    a = _create(auth_client)
    assert auth_client.delete(f"/api/activities/{a['id']}").status_code == 204
    assert auth_client.get("/api/activities").json() == []
    assert auth_client.delete(f"/api/activities/{a['id']}").status_code == 404


def test_missing_activity_is_404(auth_client):
    assert auth_client.patch("/api/activities/9999", json={"title": "x"}).status_code == 404


def test_users_cannot_touch_each_others_activities(auth_client, bob_client):
    mine = _create(auth_client, title="alice private")
    theirs = _create(bob_client, title="bob private")

    assert [a["title"] for a in auth_client.get("/api/activities").json()] == ["alice private"]
    assert bob_client.patch(f"/api/activities/{mine['id']}", json={"title": "hacked"}).status_code == 404
    assert bob_client.delete(f"/api/activities/{mine['id']}").status_code == 404
    assert auth_client.patch(f"/api/activities/{theirs['id']}", json={"status": "done"}).status_code == 404
    assert [a["title"] for a in auth_client.get("/api/activities").json()] == ["alice private"]


def test_activities_appear_automatically_from_synced_email(auth_client, account, manager):
    raw = [
        make_email(
            "m1",
            "Assignment due",
            "Please submit the report by 15/10/2026. Deadline is firm.",
            sender="Prof <p@uni.edu>",
        ),
        make_email("m2", body="Lunch was great yesterday"),
    ]
    with patch_provider(raw), patch("app.services.sync_service.summarize_email", return_value="S"):
        auth_client.get("/api/emails")
        manager.wait(10)

    activities = auth_client.get("/api/activities").json()
    assert len(activities) == 1  # only the actionable email became an activity
    activity = activities[0]
    assert activity["source"] == "email" and activity["email_id"] == f"{account.id}:m1"
    assert "From: Prof" in activity["notes"]

    # ...and, being ordinary activities, they can be edited like any other.
    r = auth_client.patch(
        f"/api/activities/{activity['id']}", json={"title": "Write the report", "status": "done"}
    )
    assert r.json()["title"] == "Write the report" and r.json()["status"] == "done"
