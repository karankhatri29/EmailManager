from datetime import datetime, timezone

from app.db.models import Activity
from app.repositories import emails as emails_repo
from app.services import activities_service
from tests.conftest import stored_email

SENT = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def _email(account, message_id="m1", **extra):
    fields = {
        "subject": "Assignment due",
        "body": "Please submit the report by 15/10/2026.",
        "sender": "Prof <prof@uni.edu>",
        "date": SENT,
        "category": "Urgent / Action Required",
    }
    return stored_email(account, message_id, **{**fields, **extra})


def test_activity_from_email_with_a_deadline(account):
    activity = activities_service.activity_from_email(_email(account))

    assert activity["user_id"] == account.user_id and activity["email_id"] == f"{account.id}:m1"
    assert activity["start_at"] == datetime(2026, 10, 15, tzinfo=timezone.utc)
    assert activity["all_day"] is True and activity["source"] == "email" and activity["status"] == "todo"
    assert activity["title"] and len(activity["title"]) <= 255
    assert "From: Prof <prof@uni.edu>" in activity["notes"] and "Deadline detected" in activity["notes"]


def test_activity_without_a_deadline_is_unscheduled(account):
    email = _email(account, body="Kindly review the notes.")
    activity = activities_service.activity_from_email(email)
    assert activity["start_at"] is None and activity["all_day"] is False
    assert "Deadline detected" not in activity["notes"]


def test_creates_activities_only_for_urgent_and_important_emails(db, account):
    emails = [
        _email(account, "urgent"),
        _email(account, "important", category="Important"),
        _email(account, "general", category="General"),
        _email(account, "promo", category="Promotional"),
    ]
    emails_repo.upsert_many(db, emails)

    assert activities_service.create_activities_for_emails(db, emails) == 2
    assert {a.email_id.split(":")[1] for a in db.query(Activity)} == {"urgent", "important"}


def test_running_twice_does_not_duplicate_activities(db, account):
    emails = [_email(account)]
    emails_repo.upsert_many(db, emails)
    assert activities_service.create_activities_for_emails(db, emails) == 1
    assert activities_service.create_activities_for_emails(db, emails) == 0
    assert db.query(Activity).count() == 1


def test_no_candidates(db):
    assert activities_service.create_activities_for_emails(db, []) == 0
