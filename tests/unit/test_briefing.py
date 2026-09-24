from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from app.db.models import FollowUp
from app.repositories import activities as activities_repo
from app.repositories import emails as emails_repo
from app.repositories import settings as settings_repo
from app.services import briefing
from tests.conftest import stored_email

URGENT = "Urgent / Action Required"
NOW = datetime.now(timezone.utc)


def _settings(db, user, **fields):
    return settings_repo.update(db, settings_repo.get_or_create(db, user.id), **fields)


def _midnight(day):
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def _build(db, user, **fields):
    return briefing.build_briefing(db, user, _settings(db, user, **fields), NOW)


def _today():
    return NOW.date()  # the user's timezone is UTC in these tests


def _all_day(db, user, title, offset_days, **extra):
    return activities_repo.create(
        db,
        user.id,
        title=title,
        start_at=_midnight(_today() + timedelta(days=offset_days)),
        all_day=True,
        **extra,
    )


# --- calendar sections ---------------------------------------------------------------------------


def test_activities_are_sorted_into_overdue_today_and_upcoming(db, user):
    _all_day(db, user, "long ago", -5)
    _all_day(db, user, "yesterday", -1)
    _all_day(db, user, "today", 0)
    _all_day(db, user, "tomorrow", 1)
    _all_day(db, user, "in three days", 3)
    _all_day(db, user, "in four days", 4)  # beyond the briefing's horizon
    _all_day(db, user, "finished", 0, status="done")
    activities_repo.create(db, user.id, title="unscheduled")

    b = _build(db, user)
    titles = lambda key: [i["title"] for i in b[key]]  # noqa: E731
    assert titles("overdue") == ["long ago", "yesterday"] and titles("today") == ["today"]
    assert titles("upcoming") == ["tomorrow", "in three days"]
    assert (
        b["overdue"][0]["days_overdue"] == 5
        and b["today"][0]["all_day"] is True
        and b["today"][0]["time"] is None
    )
    assert b["date"] == _today().isoformat()


def test_very_old_overdue_items_are_left_out(db, user):
    _all_day(db, user, "ancient", -90)
    assert _build(db, user)["overdue"] == []


def test_timed_items_show_their_local_time(db, user):
    noon = datetime.combine(_today(), datetime.min.time(), tzinfo=timezone.utc) + timedelta(
        hours=15, minutes=30
    )
    activities_repo.create(db, user.id, title="call", start_at=noon)
    (item,) = _build(db, user)["today"]
    assert item["time"] == "15:30" and item["all_day"] is False


def test_days_follow_the_users_timezone_for_timed_items_but_not_for_all_day_dates(db, user):
    kolkata = ZoneInfo("Asia/Kolkata")
    late_utc = datetime(2026, 9, 24, 19, 0, tzinfo=timezone.utc)  # 00:30 on the 25th in India
    timed = activities_repo.create(db, user.id, title="timed", start_at=late_utc)
    all_day = activities_repo.create(
        db, user.id, title="date", start_at=_midnight(late_utc.date()), all_day=True
    )

    assert briefing.activity_day(timed, kolkata).isoformat() == "2026-09-25"
    assert briefing.activity_day(timed, ZoneInfo("UTC")).isoformat() == "2026-09-24"
    assert briefing.activity_day(all_day, kolkata).isoformat() == "2026-09-24"  # a date is a date everywhere


def test_the_briefing_uses_the_users_local_date_and_greeting(db, user):
    now = datetime(2026, 9, 24, 20, 0, tzinfo=timezone.utc)  # 01:30 on the 25th in India
    b = briefing.build_briefing(db, user, _settings(db, user, timezone="Asia/Kolkata"), now)
    assert b["date"] == "2026-09-25" and b["greeting"] == "Good morning"
    assert briefing.build_briefing(db, user, _settings(db, user, timezone="UTC"), now)["date"] == "2026-09-24"


def test_other_users_calendars_are_never_included(db, user, bob):
    _all_day(db, user, "mine", 0)
    activities_repo.create(db, bob.id, title="bobs", start_at=_midnight(_today()), all_day=True)
    assert [i["title"] for i in _build(db, user)["today"]] == ["mine"]


# --- mail ----------------------------------------------------------------------------------------


def _mail(account, mid, hours_ago=1, **extra):
    return stored_email(account, mid, date=NOW - timedelta(hours=hours_ago), **extra)


def test_top_emails_are_the_open_urgent_or_important_ones_best_first_and_capped(db, user, account):
    rows = [_mail(account, f"u{i}", category=URGENT, score=4.0 + i / 10) for i in range(4)]
    rows += [
        _mail(account, "imp", category="Important", score=3.3),
        _mail(account, "done", category=URGENT, score=4.9, is_done=True),
        _mail(account, "snoozed", category=URGENT, score=4.9, snoozed_until=NOW + timedelta(hours=5)),
        _mail(account, "old", category=URGENT, score=4.9, hours_ago=30),
        _mail(account, "general", category="General", score=2.0),
    ]
    emails_repo.upsert_many(db, rows)
    top = _build(db, user)["top_emails"]
    assert [e["id"].split(":")[1] for e in top] == ["u3", "u2", "u1", "u0", "imp"]
    assert top[0]["reason"] is None and {"sender", "subject", "category"} <= set(top[0])


def test_promotions_are_counted_grouped_and_highlighted(db, user, account):
    shop, news = "Shop <deals@shop.com>", "News <hi@news.org>"
    emails_repo.upsert_many(
        db,
        [
            _mail(account, "p1", category="Promotional", sender=shop, subject="50% off laptops"),
            _mail(account, "p2", category="Promotional", sender=shop, subject="Free shipping"),
            _mail(account, "p3", category="Promotional", sender=news, subject="Weekly digest"),
            _mail(account, "old", category="Promotional", sender=news, hours_ago=40),
        ],
    )
    with patch.object(briefing.ai_summarizer, "generate_text", return_value="Laptops are 50% off.") as ai:
        promos = _build(db, user)["promotions"]

    assert promos["count"] == 3 and promos["highlights"] == "Laptops are 50% off."
    assert promos["top_senders"] == [
        {"sender": "deals@shop.com", "count": 2},
        {"sender": "hi@news.org", "count": 1},
    ]
    prompt = ai.call_args.args[0]
    assert "50% off laptops" in prompt and "Weekly digest" in prompt


def test_promotion_highlights_fall_back_to_subjects_when_the_ai_is_down(db, user, account):
    emails_repo.upsert_many(
        db,
        [
            _mail(account, "p1", category="Promotional", subject="Sale A"),
            _mail(account, "p2", category="Promotional", subject="Sale B"),
        ],
    )
    with patch.object(briefing.ai_summarizer, "generate_text", side_effect=RuntimeError("no key")):
        assert _build(db, user)["promotions"]["highlights"] == "Top subjects: Sale A; Sale B"


def test_no_promotions_means_no_ai_call(db, user, account):
    emails_repo.upsert_many(db, [_mail(account, "a", category="General")])
    with patch.object(briefing.ai_summarizer, "generate_text") as ai:
        promos = _build(db, user)["promotions"]
    ai.assert_not_called()
    assert promos == {"count": 0, "top_senders": [], "highlights": None}


def test_waiting_follow_ups_and_counts(db, user, account):
    db.add(
        FollowUp(
            user_id=user.id, account_id=account.id, thread_id="t", subject="Proposal", recipient="ann@corp.com",
            sent_at=NOW - timedelta(days=4), nudge_at=NOW,
        )
    )  # fmt: skip
    db.commit()
    emails_repo.upsert_many(
        db,
        [
            _mail(account, "u", category=URGENT),
            _mail(account, "g", category="General"),
            _mail(account, "p", category="Promotional"),
        ],
    )
    b = _build(db, user)
    assert b["waiting"] == [{"id": 1, "recipient": "ann@corp.com", "subject": "Proposal", "days": 4}]
    assert b["counts"] == {"emails": 3, "urgent": 1, "promotional": 1}


# --- rendering -----------------------------------------------------------------------------------


def test_an_empty_briefing_says_so(db, user):
    b = _build(db, user)
    assert briefing.is_empty(b)
    assert "Nothing needs your attention" in briefing.render_text(
        b
    ) and "Nothing needs your attention" in briefing.render_html(b)


def test_text_rendering_lists_every_section(db, user, account):
    _all_day(db, user, "Pay rent", -2)
    _all_day(db, user, "Submit report", 0)
    _all_day(db, user, "Dentist", 2)
    emails_repo.upsert_many(
        db,
        [
            _mail(account, "u", category=URGENT, subject="Server down"),
            _mail(account, "p", category="Promotional", subject="Sale"),
        ],
    )
    with patch.object(briefing.ai_summarizer, "generate_text", return_value="A good deal."):
        text = briefing.render_text(_build(db, user))
    for expected in (
        "OVERDUE",
        "Pay rent (2d overdue)",
        "DUE TODAY",
        "Submit report",
        "COMING UP",
        "Dentist",
        "NEEDS YOUR ATTENTION",
        "Server down",
        "PROMOTIONS (1 today",
        "A good deal.",
    ):
        assert expected in text
    assert not briefing.is_empty(_build(db, user))


def test_html_rendering_escapes_everything_from_mail_and_users(db, user, account):
    _all_day(db, user, "<script>alert(1)</script>", 0)
    emails_repo.upsert_many(
        db,
        [
            _mail(
                account, "u", category=URGENT, subject="<img src=x onerror=1>", sender="<b>Evil</b> <e@x.com>"
            )
        ],
    )
    page = briefing.render_html(_build(db, user))
    assert "<script>" not in page and "<img" not in page and "<b>Evil" not in page
    assert "&lt;script&gt;" in page and "&lt;img" in page


@pytest.mark.parametrize(
    "hour,expected",
    [
        (0, "Good morning"),
        (11, "Good morning"),
        (12, "Good afternoon"),
        (17, "Good afternoon"),
        (18, "Good evening"),
        (23, "Good evening"),
    ],
)
def test_greeting(hour, expected):
    assert briefing.greeting(hour) == expected


@pytest.mark.parametrize(
    "name,valid",
    [
        ("UTC", True),
        ("Europe/Amsterdam", True),
        ("Asia/Kolkata", True),
        ("Mars/Olympus", False),
        ("", False),
        ("../etc/passwd", False),
    ],
)
def test_timezone_validation(name, valid):
    assert briefing.valid_timezone(name) is valid
