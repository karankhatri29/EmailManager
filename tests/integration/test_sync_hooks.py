from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.db.models import FollowUp
from app.repositories import notifications as notifications_repo
from app.repositories import settings as settings_repo
from app.services import sync_service
from tests.conftest import make_email, patch_provider

SUMM = "app.services.sync_service.summarize_email"


def _sent(thread_id="t1", awaiting=True, recipient="Ann <ann@corp.com>"):
    return {
        "thread_id": thread_id,
        "subject": "Proposal",
        "recipient": recipient,
        "sent_at": datetime.now(timezone.utc) - timedelta(days=2),
        "awaiting": awaiting,
    }


def test_new_urgent_mail_raises_one_alert_per_sync_not_one_per_email(db, user, account):
    raw = [
        make_email("m1", "OTP", "your verification code is 1"),
        make_email("m2", "Security", "password reset security alert"),
    ]
    with patch_provider(raw), patch(SUMM, return_value="S"):
        sync_service.sync_account(db, account, "Last 1 Day")
    (alert,) = notifications_repo.list_for_user(db, user.id)
    assert alert.kind == "urgent" and alert.title == "2 new urgent emails"


def test_a_sync_with_nothing_urgent_is_silent(db, user, account):
    with patch_provider([make_email("m1", body="Lunch was great")]):
        sync_service.sync_account(db, account, "Last 1 Day")
    assert notifications_repo.list_for_user(db, user.id) == []


def test_alerts_can_be_turned_off(db, user, account):
    settings_repo.update(db, settings_repo.get_or_create(db, user.id), urgent_alerts=False)
    with (
        patch_provider([make_email("m1", "OTP", "your verification code is 1")]),
        patch(SUMM, return_value="S"),
    ):
        sync_service.sync_account(db, account, "Last 1 Day")
    assert notifications_repo.list_for_user(db, user.id) == []


def test_a_first_import_of_old_mail_does_not_flood_the_user(db, user, account):
    old = datetime.now(timezone.utc) - timedelta(days=20)
    raw = [make_email(f"m{i}", "OTP", "your verification code is 1", date=old) for i in range(10)]
    with patch_provider(raw), patch(SUMM, return_value="S"):
        sync_service.sync_account(db, account, "Last 1 Month")
    assert notifications_repo.list_for_user(db, user.id) == []


def test_sync_tracks_conversations_awaiting_a_reply(db, account):
    with patch_provider(
        [],
        sent_threads=[
            _sent("waiting"),
            _sent("answered", awaiting=False),
            _sent("bot", recipient="noreply@x.com"),
        ],
    ):
        sync_service.sync_account(db, account, "Last 1 Day")
    assert [f.thread_id for f in db.query(FollowUp)] == ["waiting"]


def test_follow_up_tracking_failing_never_fails_the_sync(db, user, account):
    raw = [make_email("m1", body="Lunch was great")]
    with patch_provider(raw, sent_error=RuntimeError("Gmail threads API down")):
        assert sync_service.sync_account(db, account, "Last 1 Day") == 1  # the mail itself synced fine
    assert db.query(FollowUp).count() == 0
