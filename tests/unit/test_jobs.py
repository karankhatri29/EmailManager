import threading
import time
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from app.db.models import FollowUp, Notification
from app.repositories import activities as activities_repo
from app.repositories import notifications as notifications_repo
from app.repositories import settings as settings_repo
from app.services import background, jobs

SEND = "app.services.jobs.try_send_email"


def _enable(db, user, **fields):
    fields.setdefault("briefing_enabled", True)
    return settings_repo.update(db, settings_repo.get_or_create(db, user.id), **fields)


def _at(hour, minute=0, day=24):
    return datetime(2026, 9, day, hour, minute, tzinfo=timezone.utc)


# --- daily briefings -----------------------------------------------------------------------------


def test_briefing_waits_for_the_chosen_hour_then_sends_once_a_day(db, user):
    _enable(db, user, briefing_hour=8)
    with patch(SEND, return_value=True) as send:
        assert jobs.send_due_briefings(db, _at(7, 59)) == 0
        assert jobs.send_due_briefings(db, _at(8, 0)) == 1
        assert jobs.send_due_briefings(db, _at(8, 5)) == 0  # already sent today
        assert jobs.send_due_briefings(db, _at(23, 0)) == 0
        assert jobs.send_due_briefings(db, _at(8, 1, day=25)) == 1  # a new day
    assert send.call_count == 2
    assert settings_repo.get_or_create(db, user.id).briefing_last_sent == date(2026, 9, 25)


def test_a_briefing_sent_late_still_goes_out_once(db, user):
    """The server was down at 8:00: it sends as soon as it is back, but only once."""
    _enable(db, user, briefing_hour=8)
    with patch(SEND, return_value=True) as send:
        assert jobs.send_due_briefings(db, _at(14, 30)) == 1
        assert jobs.send_due_briefings(db, _at(14, 31)) == 0
    assert send.call_count == 1


def test_the_hour_is_the_users_local_hour(db, user):
    _enable(db, user, briefing_hour=8, timezone="Asia/Kolkata")  # UTC+5:30
    with patch(SEND, return_value=True):
        assert jobs.send_due_briefings(db, _at(2, 0)) == 0  # 07:30 in India
        assert jobs.send_due_briefings(db, _at(2, 45)) == 1  # 08:15 in India


def test_only_opted_in_users_get_a_briefing(db, user, bob):
    _enable(db, user)
    _enable(db, bob, briefing_enabled=False)
    with patch(SEND, return_value=True) as send:
        assert jobs.send_due_briefings(db, _at(9)) == 1
    assert send.call_args.args[0] == user.email


def test_the_briefing_is_delivered_in_the_app_and_by_email(db, user):
    _enable(db, user)
    with patch(SEND, return_value=True) as send:
        jobs.send_due_briefings(db, _at(9))

    (note,) = notifications_repo.list_for_user(db, user.id)
    assert note.kind == "briefing" and "briefing" in note.title.lower() and "Good morning" in note.body
    to, subject, text, html = send.call_args.args
    assert to == user.email and subject == note.title and "Good morning" in text and "<h2" in html


def test_without_smtp_the_in_app_copy_still_counts_as_delivered(db, user):
    _enable(db, user)
    with patch(SEND, return_value=False):  # the server cannot send email
        assert jobs.send_due_briefings(db, _at(9)) == 1
    assert len(notifications_repo.list_for_user(db, user.id)) == 1
    with patch(SEND, return_value=False):
        assert jobs.send_due_briefings(db, _at(10)) == 0


# --- reminders -----------------------------------------------------------------------------------


def _activity(db, user, remind_in_minutes, **extra):
    return activities_repo.create(
        db,
        user.id,
        title="Renew passport",
        remind_at=datetime.now(timezone.utc) + timedelta(minutes=remind_in_minutes),
        **extra,
    )


def test_due_reminders_become_notifications_exactly_once(db, user):
    activity = _activity(db, user, -5, notes="Bring photos")
    assert jobs.fire_due_reminders(db) == 1
    assert jobs.fire_due_reminders(db) == 0

    (note,) = notifications_repo.list_for_user(db, user.id)
    assert note.kind == "reminder" and note.title == "Reminder: Renew passport"
    assert note.body == "Bring photos" and note.ref == f"activity:{activity.id}"
    db.refresh(activity)
    assert activity.reminded_at is not None


def test_future_finished_and_unreminded_activities_do_not_fire(db, user):
    _activity(db, user, +60)
    _activity(db, user, -5, status="done")
    activities_repo.create(db, user.id, title="no reminder set")
    assert jobs.fire_due_reminders(db) == 0
    assert notifications_repo.list_for_user(db, user.id) == []


def test_reminder_emails_only_go_to_users_who_asked(db, user):
    _activity(db, user, -5)
    with patch(SEND, return_value=True) as send:
        jobs.fire_due_reminders(db)
    send.assert_not_called()

    settings_repo.update(db, settings_repo.get_or_create(db, user.id), reminder_emails=True)
    _activity(db, user, -1)
    with patch(SEND, return_value=True) as send:
        jobs.fire_due_reminders(db)
    assert send.call_args.args[0] == user.email and "Renew passport" in send.call_args.args[1]


def test_changing_a_reminder_time_makes_it_fire_again(db, user):
    activity = _activity(db, user, -5)
    jobs.fire_due_reminders(db)
    activities_repo.update(
        db, activity, remind_at=datetime.now(timezone.utc) - timedelta(minutes=1), reminded_at=None
    )
    assert jobs.fire_due_reminders(db) == 1


# --- follow-up nudges ----------------------------------------------------------------------------


def _followup(db, user, account, thread, nudge_in_hours, status="waiting"):
    row = FollowUp(
        user_id=user.id, account_id=account.id, thread_id=thread, subject=f"Subject {thread}", recipient="ann@corp.com",
        sent_at=datetime.now(timezone.utc) - timedelta(days=4), nudge_at=datetime.now(timezone.utc) + timedelta(hours=nudge_in_hours),
        status=status,
    )  # fmt: skip
    db.add(row)
    db.commit()
    return row


def test_a_waiting_follow_up_is_nudged_once_when_due(db, user, account):
    row = _followup(db, user, account, "due", -1)
    _followup(db, user, account, "later", +24)
    _followup(db, user, account, "answered", -1, status="replied")
    _followup(db, user, account, "dismissed", -1, status="dismissed")

    assert jobs.nudge_followups(db) == 1
    assert jobs.nudge_followups(db) == 0

    (note,) = notifications_repo.list_for_user(db, user.id)
    assert note.kind == "followup" and note.title == "No reply yet: Subject due"
    assert "ann@corp.com" in note.body and "4 days ago" in note.body and note.ref == f"followup:{row.id}"


# --- running everything --------------------------------------------------------------------------


def test_run_periodic_jobs_runs_each_job_and_reports(session_factory, db, user, account):
    _enable(db, user, briefing_hour=0)
    _activity(db, user, -5)
    _followup(db, user, account, "t", -1)
    with patch.object(jobs, "SessionLocal", session_factory), patch(SEND, return_value=True):
        assert jobs.run_periodic_jobs() == {"briefings": 1, "reminders": 1, "followups": 1, "pruned": 0}


def test_one_failing_job_does_not_stop_the_others(session_factory, db, user, account):
    _activity(db, user, -5)
    with (
        patch.object(jobs, "SessionLocal", session_factory),
        patch.object(jobs, "send_due_briefings", side_effect=RuntimeError("boom")),
    ):
        results = jobs.run_periodic_jobs()
    assert results["briefings"] == 0 and results["reminders"] == 1


def test_old_notifications_are_pruned(db, user):
    old = notifications_repo.create(db, user.id, "urgent", "old")
    old.created_at = datetime.now(timezone.utc) - timedelta(days=notifications_repo.KEEP_DAYS + 1)
    notifications_repo.create(db, user.id, "urgent", "new")
    db.commit()
    assert notifications_repo.prune(db) == 1
    assert [n.title for n in db.query(Notification)] == ["new"]


def test_the_jobs_loop_runs_repeatedly_and_stops(session_factory):
    calls = []
    with patch.object(background.jobs, "run_periodic_jobs", side_effect=lambda: calls.append(1)):
        stop = background.start_periodic_jobs(0.05)
        time.sleep(0.35)
        stop.set()
        time.sleep(0.15)
        after_stop = len(calls)
        time.sleep(0.15)
    assert after_stop >= 3 and len(calls) == after_stop
    assert isinstance(stop, threading.Event)
