from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.db.models import FollowUp
from app.repositories import notifications as notifications_repo
from app.repositories import settings as settings_repo
from app.services.notifier import EmailNotConfigured, EmailSendError

SEND = "app.api.account_tools.send_email"
SMTP_ON = "app.api.account_tools.get_settings"


def _now(**delta):
    return datetime.now(timezone.utc) + timedelta(**delta)


# --- settings ------------------------------------------------------------------------------------


def test_settings_have_safe_defaults(auth_client):
    body = auth_client.get("/api/settings").json()
    assert body == {
        "timezone": "UTC",
        "briefing_enabled": False,
        "briefing_hour": 8,
        "urgent_alerts": True,
        "reminder_emails": False,
        "followup_days": 3,
        "email_configured": body["email_configured"],
    }


def test_settings_can_be_updated_partially(auth_client):
    r = auth_client.put(
        "/api/settings", json={"timezone": "Europe/Amsterdam", "briefing_enabled": True, "briefing_hour": 7}
    )
    body = r.json()
    assert (
        r.status_code == 200 and body["timezone"] == "Europe/Amsterdam" and body["briefing_enabled"] is True
    )
    assert body["briefing_hour"] == 7 and body["followup_days"] == 3  # untouched
    assert auth_client.get("/api/settings").json() == body


@pytest.mark.parametrize(
    "payload",
    [
        {"timezone": "Mars/Olympus"},
        {"timezone": ""},
        {"briefing_hour": 24},
        {"briefing_hour": -1},
        {"followup_days": 0},
        {"followup_days": 31},
        {"briefing_enabled": None},
        {"timezone": None},
    ],
)
def test_invalid_settings_are_rejected(auth_client, payload):
    assert auth_client.put("/api/settings", json=payload).status_code == 422


def test_changing_the_schedule_allows_todays_briefing_again(auth_client, db, user):
    row = settings_repo.get_or_create(db, user.id)
    settings_repo.update(db, row, briefing_last_sent=datetime.now(timezone.utc).date())
    auth_client.put("/api/settings", json={"briefing_hour": 5})
    db.expire_all()
    assert settings_repo.get_or_create(db, user.id).briefing_last_sent is None


def test_settings_are_per_user(auth_client, bob_client):
    auth_client.put("/api/settings", json={"briefing_enabled": True, "timezone": "Asia/Kolkata"})
    assert bob_client.get("/api/settings").json()["briefing_enabled"] is False


def test_email_configured_reflects_the_server(auth_client):
    with patch(SMTP_ON) as settings:
        settings.return_value.smtp_configured = True
        assert auth_client.get("/api/settings").json()["email_configured"] is True
        settings.return_value.smtp_configured = False
        assert auth_client.get("/api/settings").json()["email_configured"] is False


# --- briefing ------------------------------------------------------------------------------------


def test_briefing_endpoint_shape(auth_client):
    auth_client.post(
        "/api/activities", json={"title": "Pay rent", "start_at": _now().isoformat(), "all_day": False}
    )
    body = auth_client.get("/api/briefing").json()
    assert set(body) == {
        "date",
        "greeting",
        "overdue",
        "today",
        "upcoming",
        "top_emails",
        "promotions",
        "waiting",
        "classes",
        "counts",
    }
    assert [i["title"] for i in body["today"]] == ["Pay rent"]


def test_briefing_is_private(auth_client, bob_client):
    auth_client.post("/api/activities", json={"title": "Alice only", "start_at": _now().isoformat()})
    assert bob_client.get("/api/briefing").json()["today"] == []


def test_email_briefing_now_sends_to_the_login_address(auth_client):
    with patch(SEND) as send:
        r = auth_client.post("/api/briefing/send")
    assert r.status_code == 200 and r.json() == {"sent_to": "alice@example.com"}
    to, subject, text, html = send.call_args.args
    assert (
        to == "alice@example.com"
        and "briefing" in subject.lower()
        and "Good" in text
        and html.startswith("<div")
    )


def test_email_briefing_reports_missing_or_broken_smtp(auth_client):
    with patch(SEND, side_effect=EmailNotConfigured):
        assert auth_client.post("/api/briefing/send").status_code == 503
    with patch(SEND, side_effect=EmailSendError("535 auth failed")):
        r = auth_client.post("/api/briefing/send")
    assert r.status_code == 502 and "535" not in r.text  # server details are not leaked


# --- notifications -------------------------------------------------------------------------------


def test_notifications_list_with_unread_count_newest_first(auth_client, db, user):
    for title in ("first", "second", "third"):
        notifications_repo.create(db, user.id, "urgent", title)
    body = auth_client.get("/api/notifications").json()
    assert body["unread"] == 3 and [n["title"] for n in body["items"]] == ["third", "second", "first"]
    assert body["items"][0]["created_at"].endswith("Z") and body["items"][0]["read_at"] is None
    assert len(auth_client.get("/api/notifications", params={"limit": 2}).json()["items"]) == 2
    assert auth_client.get("/api/notifications", params={"limit": 500}).status_code == 422


def test_marking_notifications_read(auth_client, db, user):
    ids = [notifications_repo.create(db, user.id, "urgent", t).id for t in ("a", "b", "c")]

    assert auth_client.post("/api/notifications/read", json={"ids": ids[:1]}).status_code == 204
    body = auth_client.get("/api/notifications").json()
    assert body["unread"] == 2
    assert [
        n["title"]
        for n in auth_client.get("/api/notifications", params={"unread_only": True}).json()["items"]
    ] == ["c", "b"]

    auth_client.post("/api/notifications/read", json={})  # no ids: everything
    assert auth_client.get("/api/notifications").json()["unread"] == 0
    assert auth_client.get("/api/notifications", params={"unread_only": True}).json()["items"] == []


def test_notifications_are_private(auth_client, bob_client, db, user, bob):
    mine = notifications_repo.create(db, user.id, "urgent", "mine")
    notifications_repo.create(db, bob.id, "urgent", "bobs")
    assert [n["title"] for n in bob_client.get("/api/notifications").json()["items"]] == ["bobs"]

    bob_client.post("/api/notifications/read", json={"ids": [mine.id]})  # someone else's id: ignored
    assert auth_client.get("/api/notifications").json()["unread"] == 1
    bob_client.post("/api/notifications/read", json={})
    assert auth_client.get("/api/notifications").json()["unread"] == 1  # Alice's are untouched


# --- follow-ups ----------------------------------------------------------------------------------


def _followup(db, user, account, thread="t1", status="waiting", days_ago=4):
    row = FollowUp(
        user_id=user.id, account_id=account.id, thread_id=thread, subject=f"Subject {thread}", recipient="ann@corp.com",
        sent_at=_now(days=-days_ago), nudge_at=_now(days=-1), status=status,
    )  # fmt: skip
    db.add(row)
    db.commit()
    return row


def test_followups_list_longest_wait_first_with_a_status_filter(auth_client, db, user, account):
    _followup(db, user, account, "newer", days_ago=2)
    _followup(db, user, account, "older", days_ago=9)
    _followup(db, user, account, "answered", status="replied")

    listed = auth_client.get("/api/followups").json()
    assert [f["thread_id"] for f in listed] == ["older", "newer"] and listed[0]["waiting_days"] == 9
    assert listed[0]["sent_at"].endswith("Z") and listed[0]["recipient"] == "ann@corp.com"
    assert [
        f["thread_id"] for f in auth_client.get("/api/followups", params={"status": "replied"}).json()
    ] == ["answered"]
    assert len(auth_client.get("/api/followups", params={"status": "all"}).json()) == 3
    assert auth_client.get("/api/followups", params={"status": "bogus"}).status_code == 422


def test_dismissing_a_followup(auth_client, db, user, account):
    row = _followup(db, user, account)
    assert auth_client.post(f"/api/followups/{row.id}/dismiss").json()["status"] == "dismissed"
    assert auth_client.get("/api/followups").json() == []


def test_snoozing_a_followup_reschedules_its_nudge(auth_client, db, user, account):
    row = _followup(db, user, account)
    row.nudged_at = _now(days=-1)
    db.commit()
    body = auth_client.post(f"/api/followups/{row.id}/snooze", json={"days": 2}).json()
    assert body["status"] == "waiting" and body["nudge_at"] > _now(days=1, hours=23).isoformat()
    db.refresh(row)
    assert row.nudged_at is None  # it can nudge again
    assert auth_client.post(f"/api/followups/{row.id}/snooze", json={"days": 0}).status_code == 422
    assert auth_client.post(f"/api/followups/{row.id}/snooze", json={"days": 99}).status_code == 422


def test_scheduling_a_followup_puts_it_in_tomorrows_calendar(auth_client, db, user, account):
    row = _followup(db, user, account)
    r = auth_client.post(f"/api/followups/{row.id}/schedule")
    activity = r.json()
    assert (
        r.status_code == 201 and activity["title"] == "Follow up: Subject t1" and activity["all_day"] is True
    )
    tomorrow = (_now(days=1)).date().isoformat()
    assert activity["start_at"].startswith(tomorrow) and "ann@corp.com" in activity["notes"]
    assert any(a["id"] == activity["id"] for a in auth_client.get("/api/activities").json())


def test_followups_are_private(auth_client, bob_client, db, user, account):
    row = _followup(db, user, account)
    assert bob_client.get("/api/followups").json() == []
    for action, body in (("dismiss", None), ("snooze", {"days": 2}), ("schedule", None)):
        assert bob_client.post(f"/api/followups/{row.id}/{action}", json=body).status_code == 404
    assert auth_client.get("/api/followups").json()[0]["status"] == "waiting"


# --- activity reminders --------------------------------------------------------------------------


def test_activities_can_carry_a_reminder(auth_client):
    when = _now(hours=2).replace(microsecond=0)
    r = auth_client.post(
        "/api/activities",
        json={"title": "Call", "start_at": _now(hours=3).isoformat(), "remind_at": when.isoformat()},
    )
    body = r.json()
    assert (
        r.status_code == 201
        and body["remind_at"].startswith(when.strftime("%Y-%m-%dT%H:%M"))
        and body["reminded_at"] is None
    )


def test_editing_a_reminder_rearms_it_and_null_clears_it(auth_client, db):
    activity = auth_client.post(
        "/api/activities", json={"title": "Call", "remind_at": _now(minutes=-5).isoformat()}
    ).json()
    from app.services.jobs import fire_due_reminders

    assert fire_due_reminders(db) == 1
    assert auth_client.get("/api/activities").json()[0]["reminded_at"] is not None

    later = _now(hours=1).isoformat()
    body = auth_client.patch(f"/api/activities/{activity['id']}", json={"remind_at": later}).json()
    assert body["reminded_at"] is None  # a new time fires again
    body = auth_client.patch(f"/api/activities/{activity['id']}", json={"remind_at": None}).json()
    assert body["remind_at"] is None
    assert (
        auth_client.patch(f"/api/activities/{activity['id']}", json={"title": "Renamed"}).json()["remind_at"]
        is None
    )
