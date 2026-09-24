from datetime import datetime, timedelta, timezone

from app.services import email_processor
from tests.conftest import make_email


def test_urgent_email_gets_task_but_no_summary_yet():
    out = email_processor.process_emails([make_email(subject="OTP", body="your verification code is 1")])
    assert out[0]["category"] == "Urgent / Action Required"
    assert out[0]["task"].startswith("OTP")
    assert out[0]["summary"] is None  # filled in later by sync_service.summarize_pending


def test_general_email_has_no_task():
    out = email_processor.process_emails([make_email(body="Lunch was great yesterday")])
    assert out[0]["category"] == "General" and out[0]["task"] is None


def test_output_has_exactly_the_stored_fields():
    out = email_processor.process_emails([make_email(body="Lunch was great")])
    assert set(out[0]) == {"id", "sender", "subject", "body", "date", "score", "category", "summary", "task"}


def test_keeps_original_date_and_defaults_missing_one():
    original = datetime(2026, 1, 1, tzinfo=timezone.utc)
    out = email_processor.process_emails([make_email(body="Lunch was great", date=original)])
    assert out[0]["date"] == original

    e = make_email(body="Lunch was great")
    del e["date"]
    out = email_processor.process_emails([e])
    assert datetime.now(timezone.utc) - out[0]["date"] < timedelta(minutes=1)


def test_empty_input():
    assert email_processor.process_emails([]) == []
