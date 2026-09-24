"""End to end: a real invitation email goes through reading, classifying and analysing."""

from datetime import datetime, timezone
from pathlib import Path

from app.services import email_processor

DRIVE = (Path(__file__).parent.parent / "data" / "placement_drive.html").read_text(encoding="utf-8")
SENT = datetime(2026, 9, 24, 9, 0, tzinfo=timezone.utc)


def _process():
    raw = {
        "id": "1",
        "sender": "neoPAT <noreply@neopat.example>",
        "subject": "Congratulations! You're Eligible for LTM Placement Drive",
        "body": DRIVE,
        "date": SENT,
    }
    return email_processor.process_emails([raw])[0]


def test_html_invitation_is_readable_text():
    body = _process()["body"]
    assert "<" not in body and "Drive Number: pat-PL-2026-1348" in body


def test_invitation_asks_for_action_and_names_it():
    out = _process()
    assert out["category"] in ("Important", "Urgent / Action Required")
    assert out["task"] == "Confirm your participation"


def test_an_empty_date_field_does_not_invent_a_deadline():
    out = _process()
    # neither the email's own date nor the year inside "pat-PL-2026-1348" may become a date
    assert out["due_date"] is None and out["due_kind"] is None and out["due_text"] is None
