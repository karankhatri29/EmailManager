from datetime import datetime, timedelta, timezone

from app.services import email_processor
from app.services.nlp_engine import category_score
from app.services.rules import RuleSpec
from tests.conftest import make_email

URGENT = "Urgent / Action Required"


def test_urgent_email_gets_task_but_no_summary_yet():
    out = email_processor.process_emails([make_email(subject="OTP", body="your verification code is 1")])
    assert out[0]["category"] == URGENT
    assert out[0]["task"].startswith("OTP")
    assert out[0]["summary"] is None  # filled in later by sync_service.summarize_pending


def test_general_email_has_no_task():
    out = email_processor.process_emails([make_email(body="Lunch was great yesterday")])
    assert out[0]["category"] == "General" and out[0]["task"] is None


def test_output_has_exactly_the_stored_fields():
    out = email_processor.process_emails([make_email(body="Lunch was great")])
    assert set(out[0]) == {
        "id",
        "sender",
        "sender_address",
        "subject",
        "body",
        "date",
        "thread_id",
        "unsubscribe_url",
        "unsubscribe_one_click",
        "score",
        "category",
        "reason",
        "category_source",
        "summary",
        "task",
    }


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


def test_sender_address_thread_unsubscribe_and_reason_are_captured():
    email = make_email(
        sender="Shop <News@Shop.com>",
        subject="50% off sale",
        body="shop now",
        thread_id="t1",
        unsubscribe_url="https://shop.com/u",
        unsubscribe_one_click=True,
    )
    out = email_processor.process_emails([email])[0]
    assert out["sender_address"] == "news@shop.com" and out["thread_id"] == "t1"
    assert out["unsubscribe_url"] == "https://shop.com/u" and out["unsubscribe_one_click"] is True
    assert out["category"] == "Promotional" and "promotional" in out["reason"].lower()
    assert out["category_source"] == "auto"


def test_user_rules_override_the_classifier_and_say_so():
    rules = [RuleSpec("sender", "boss@work.com", URGENT)]
    email = make_email(sender="Boss <boss@work.com>", body="Lunch was great")
    out = email_processor.process_emails([email], rules)[0]
    assert out["category"] == URGENT and out["category_source"] == "rule"
    assert out["score"] == category_score(URGENT)
    assert "boss@work.com" in out["reason"] and out["task"] is not None


def test_rules_do_not_touch_other_senders():
    rules = [RuleSpec("sender", "boss@work.com", URGENT)]
    email = make_email(sender="Bob <bob@x.com>", body="Lunch was great")
    out = email_processor.process_emails([email], rules)[0]
    assert out["category"] == "General" and out["category_source"] == "auto"
