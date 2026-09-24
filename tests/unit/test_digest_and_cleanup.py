from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.db.models import Notification
from app.repositories import emails as emails_repo
from app.repositories import notifications as notifications_repo
from app.repositories import rules as rules_repo
from app.repositories import settings as settings_repo
from app.services import ai_summarizer, digest, jobs, newsletter_cleanup, threads
from tests.conftest import stored_email

NOW = datetime(2026, 9, 24, 19, 30, tzinfo=timezone.utc)
SEND = "app.services.jobs.try_send_email"
AI = "app.services.digest.ai_summarizer.generate_text"
PROMO = "Promotional"


def _promo(account, mid, subject, sender="Shop <deals@shop.com>", hours_ago=2, **extra):
    return stored_email(
        account,
        mid,
        subject=subject,
        sender=sender,
        category=PROMO,
        date=NOW - timedelta(hours=hours_ago),
        **extra,
    )


def _settings(db, user, **fields):
    return settings_repo.update(db, settings_repo.get_or_create(db, user.id), **fields)


# --- thread summaries: three bullets --------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("- a\n- b\n- c\n- d", "- a\n- b\n- c"),
        ("1. one\n2. two\n3. three", "- one\n- two\n- three"),
        ("### Summary\n* x\n* y", "- x\n- y"),
        (
            "They agreed on Friday. Ana objected to the cost. Ben will send a quote. More later.",
            "- They agreed on Friday.\n- Ana objected to the cost.\n- Ben will send a quote.",
        ),
        ("", ""),
    ],
)
def test_the_thread_summary_is_at_most_three_bullets(raw, expected):
    assert ai_summarizer.three_bullets(raw) == expected


def test_a_fifty_reply_chain_is_condensed_in_parts_and_keeps_the_latest_messages(db, account):
    rows = [
        stored_email(
            account,
            f"t{i}",
            body=f"Reply number {i}. " + "We discussed the budget. " * 60,
            thread_id="T",
            date=NOW + timedelta(minutes=i),
        )
        for i in range(50)
    ]
    emails_repo.upsert_many(db, rows)
    latest = emails_repo.get_for_user(db, account.user_id, f"{account.id}:t49")
    seen = {}

    def fake_summary(text):
        seen["final"] = text
        return "<b>ok</b>"

    with (
        patch.object(
            threads.ai_summarizer, "condense_thread_part", side_effect=lambda t: "NOTES"
        ) as condense,
        patch.object(threads.ai_summarizer, "summarize_thread", side_effect=fake_summary),
    ):
        threads.summarize(db, account.user_id, latest)
    assert 1 <= condense.call_count <= threads.MAX_PARTS  # older stretches are condensed, not dropped
    assert "NOTES" in seen["final"] and "Reply number 49" in seen["final"]
    assert len(seen["final"]) < threads.MAX_TRANSCRIPT_CHARS + 2000


def test_quoted_replies_do_not_bloat_the_transcript(db, account):
    quoted = "Agreed.\n\nOn Mon, 21 Sep 2026, Sam <s@x.com> wrote:\n> " + "old text " * 200
    emails_repo.upsert_many(db, [stored_email(account, "q1", body=quoted, thread_id="Q")])
    row = emails_repo.get_for_user(db, account.user_id, f"{account.id}:q1")
    assert threads._transcript([row]).count("old text") == 0


# --- evening promotions digest -------------------------------------------------------------------


def test_deal_scoring_prefers_concrete_offers():
    assert digest.deal_score("40% off running shoes") > digest.deal_score("Our summer catalogue is here")
    assert digest.deal_score("Save $20 today") > 0
    assert digest.deal_score("Weekly newsletter") == 0


def test_the_digest_counts_todays_promotions_and_names_the_best_deals(db, user, account):
    emails_repo.upsert_many(
        db,
        [
            _promo(account, "1", "40% off running shoes", "Nike <n@nike.com>"),
            _promo(account, "2", "New arrivals are here", "Zara <z@zara.com>"),
            _promo(account, "3", "Save $50 on headphones", "Sony <s@sony.com>"),
            _promo(account, "4", "An old sale", "Old <o@old.com>", hours_ago=30),  # not today
            stored_email(account, "5", category="General"),
        ],
    )
    with patch(AI, side_effect=RuntimeError("down")):  # no AI: subjects are scored instead
        d = digest.build_digest(db, user, settings_repo.get_or_create(db, user.id), NOW)
    assert d["count"] == 3
    assert [x["sender"] for x in d["deals"]] == ["Nike", "Sony"]
    assert d["headline"].startswith("You received 3 promotional emails today; the best deals were")
    assert "Nike" in d["headline"] and "Sony" in d["headline"]


def test_the_ai_only_chooses_among_the_real_subjects(db, user, account):
    emails_repo.upsert_many(
        db,
        [
            _promo(account, "1", "Alpha", "A <a@a.com>"),
            _promo(account, "2", "Beta", "B <b@b.com>", hours_ago=3),
        ],
    )
    with patch(AI, return_value="2, 9, 1"):  # 9 does not exist and is ignored
        d = digest.build_digest(db, user, settings_repo.get_or_create(db, user.id), NOW)
    assert [x["subject"] for x in d["deals"]] == ["Beta", "Alpha"]


def test_a_day_without_promotions(db, user):
    d = digest.build_digest(db, user, settings_repo.get_or_create(db, user.id), NOW)
    assert d["count"] == 0 and d["headline"] == "No promotional emails today."


def test_digest_job_sends_once_after_the_chosen_hour(db, user, account):
    _settings(db, user, digest_enabled=True, digest_hour=19)
    emails_repo.upsert_many(db, [_promo(account, "1", "50% off everything")])
    with patch(SEND, return_value=True) as send, patch(AI, side_effect=RuntimeError):
        assert jobs.send_due_digests(db, NOW.replace(hour=18)) == 0
        assert jobs.send_due_digests(db, NOW) == 1
        assert jobs.send_due_digests(db, NOW + timedelta(hours=1)) == 0
    assert send.call_count == 1
    note = db.query(Notification).filter_by(user_id=user.id, kind="digest").one()
    assert "You received 1 promotional email today" in note.body
    assert settings_repo.get_or_create(db, user.id).digest_last_sent == date(2026, 9, 24)


def test_digest_job_stays_quiet_on_a_day_with_no_promotions(db, user):
    _settings(db, user, digest_enabled=True, digest_hour=19)
    with patch(SEND, return_value=True) as send:
        assert jobs.send_due_digests(db, NOW) == 0
    assert send.call_count == 0 and notifications_repo.unread_count(db, user.id) == 0


# --- unopened newsletters: suggest, unsubscribe, mute, archive ------------------------------------


def _newsletter(account, prefix, unread, sender="News <n@letter.com>", count=4, span_days=100):
    return [
        stored_email(
            account,
            f"{prefix}{i}",
            sender=sender,
            subject=f"Issue {i}",
            category=PROMO,
            is_unread=unread,
            unsubscribe_url="https://letter.com/u?id=1",
            unsubscribe_one_click=True,
            date=datetime.now(timezone.utc) - timedelta(days=i * span_days // (count - 1)),
        )
        for i in range(count)
    ]


def test_only_senders_that_were_never_opened_are_suggested(db, user, account):
    opened = _newsletter(account, "b", True, sender="Read <r@read.com>")
    opened[0]["is_unread"] = False  # one of its messages was opened
    emails_repo.upsert_many(
        db,
        _newsletter(account, "a", True)  # never opened
        + opened
        + _newsletter(account, "c", None, sender="Unknown <u@unknown.com>")  # read state unknown
        + _newsletter(account, "d", True, sender="Fresh <f@fresh.com>", span_days=10),  # too recent to say
    )
    found = newsletter_cleanup.suggestions(db, user.id, months=3)
    assert [g["sender_address"] for g in found] == ["n@letter.com"]


def test_cleanup_unsubscribes_mutes_and_archives(db, user, account):
    emails_repo.upsert_many(db, _newsletter(account, "a", True))
    groups = newsletter_cleanup.suggestions(db, user.id, 3)
    with patch("app.services.unsubscribe.one_click_unsubscribe", return_value=True) as post:
        results = newsletter_cleanup.clean_up(db, user.id, groups)
    post.assert_called_once_with("https://letter.com/u?id=1")
    assert results[0]["unsubscribed"] is True and results[0]["archived"] == 4
    assert rules_repo.find(db, user.id, "sender", "n@letter.com").category == PROMO
    assert newsletter_cleanup.suggestions(db, user.id, 3) == []  # muted senders are not suggested again


def test_a_sender_without_one_click_is_muted_and_the_link_is_handed_back(db, user, account):
    rows = _newsletter(account, "a", True)
    for r in rows:
        r["unsubscribe_one_click"] = False
    emails_repo.upsert_many(db, rows)
    with patch("app.services.unsubscribe.one_click_unsubscribe") as post:
        result = newsletter_cleanup.clean_up(db, user.id, newsletter_cleanup.suggestions(db, user.id, 3))[0]
    post.assert_not_called()  # only one-click links are ever used automatically
    assert result["unsubscribed"] is False and result["link"] == "https://letter.com/u?id=1"


def test_auto_cleanup_job_runs_once_a_day_and_notifies(db, user, account):
    _settings(db, user, auto_cleanup=True, cleanup_months=3)
    emails_repo.upsert_many(db, _newsletter(account, "a", True))
    with patch("app.services.unsubscribe.one_click_unsubscribe", return_value=True):
        assert jobs.run_auto_cleanups(db, datetime.now(timezone.utc)) == 1
        assert jobs.run_auto_cleanups(db, datetime.now(timezone.utc)) == 0
    note = db.query(Notification).filter_by(user_id=user.id, kind="cleanup").one()
    assert "unsubscribed" in note.body


def test_auto_cleanup_is_off_by_default(db, user, account):
    emails_repo.upsert_many(db, _newsletter(account, "a", True))
    assert jobs.run_auto_cleanups(db, datetime.now(timezone.utc)) == 0
