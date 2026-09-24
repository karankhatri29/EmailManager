import json
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.core.security import decrypt
from app.db.models import Activity, MailAccount, SyncState
from app.providers import ProviderAuthError
from app.repositories import emails as repo
from app.services import sync_service
from tests.conftest import make_email, patch_provider, stored_email

SUMM = "app.services.sync_service.summarize_email"


def test_sync_stores_owned_prefixed_emails_classified_and_summarised(db, user, account):
    raw = [make_email("m1", "OTP", "your verification code is 1"), make_email("m2", body="Lunch was great")]
    with patch_provider(raw), patch(SUMM, return_value="S"):
        assert sync_service.sync_account(db, account, "Last 1 Day") == 2

    rows = {e.id: e for e in repo.list_in_window(db, user.id, 1)}
    assert set(rows) == {f"{account.id}:m1", f"{account.id}:m2"}
    urgent = rows[f"{account.id}:m1"]
    assert urgent.user_id == user.id and urgent.account_id == account.id
    assert urgent.category == "Urgent / Action Required" and urgent.summary == "S"
    assert rows[f"{account.id}:m2"].summary is None  # General mail is never summarised
    assert repo.get_synced_at(db, account.id, "Last 1 Day") is not None


def test_sync_creates_an_activity_for_urgent_mail_only(db, account):
    raw = [make_email("m1", "OTP", "your verification code is 1"), make_email("m2", body="Lunch was great")]
    with patch_provider(raw), patch(SUMM, return_value="S"):
        sync_service.sync_account(db, account, "Last 1 Day")

    activities = db.query(Activity).all()
    assert [a.email_id for a in activities] == [f"{account.id}:m1"]
    assert activities[0].user_id == account.user_id and activities[0].source == "email"


def test_sync_only_downloads_messages_not_stored_yet(db, account):
    repo.upsert_many(db, [stored_email(account, "old", summary="KEEP", category="Important")])
    raw = [make_email("old", "OTP", "verification code"), make_email("new", body="Lunch was great")]
    with patch_provider(raw) as provider, patch(SUMM, return_value="NEW") as summ:
        assert sync_service.sync_account(db, account, "Last 1 Day") == 1

    assert provider.fetched == ["new"]
    summ.assert_not_called()
    assert {e.id: e.summary for e in repo.list_in_window(db, account.user_id, 1)}[
        f"{account.id}:old"
    ] == "KEEP"


def test_two_mailboxes_can_hold_the_same_provider_message_id(db, user, account):
    from tests.conftest import make_account

    other = make_account(db, user, "second@gmail.com")
    with patch_provider([make_email("same", body="Lunch was great")]):
        sync_service.sync_account(db, account, "Last 1 Day")
        sync_service.sync_account(db, other, "Last 1 Day")
    assert len(repo.list_in_window(db, user.id, 1)) == 2


def test_refreshed_credentials_are_persisted_encrypted(db, account):
    with patch_provider([], refreshed='{"token": "fresh", "refresh_token": "r"}'):
        sync_service.sync_account(db, account, "Last 1 Day")
    stored = db.get(MailAccount, account.id).credentials
    assert "fresh" not in stored and json.loads(decrypt(stored))["token"] == "fresh"


def test_credentials_untouched_when_nothing_was_refreshed(db, account):
    before = db.get(MailAccount, account.id).credentials
    with patch_provider([]):
        sync_service.sync_account(db, account, "Last 1 Day")
    assert db.get(MailAccount, account.id).credentials == before


def test_login_failure_flags_the_account_for_reconnection_and_reraises(db, account):
    with (
        patch_provider(error=ProviderAuthError("Google rejected the saved login")),
        pytest.raises(ProviderAuthError),
    ):
        sync_service.sync_account(db, account, "Last 1 Day")

    account = db.get(MailAccount, account.id)
    assert account.status == "needs_reauth" and "rejected" in account.last_error
    assert db.query(SyncState).count() == 0  # nothing marked as synced


def test_other_errors_do_not_flag_the_account(db, account):
    with patch_provider(error=RuntimeError("network blip")), pytest.raises(RuntimeError):
        sync_service.sync_account(db, account, "Last 1 Day")
    assert db.get(MailAccount, account.id).status == "active"


def test_emails_are_stored_before_summaries_finish(db, account):
    """Emails must be visible while (slow) summaries are still being generated."""
    seen_during_summary = []

    def slow_summary(body):
        seen_during_summary.append(len(repo.list_in_window(db, account.user_id, 1)))
        return "S"

    with (
        patch_provider([make_email("m1", "OTP", "verification code")]),
        patch(SUMM, side_effect=slow_summary),
    ):
        sync_service.sync_account(db, account, "Last 1 Day")
    assert seen_during_summary == [1]


def test_summarize_pending_retries_missing_and_tolerates_failures(db, account):
    repo.upsert_many(
        db,
        [
            stored_email(account, "ok", body="ok", category="Important"),
            stored_email(account, "fails", body="fails", category="Urgent / Action Required"),
            stored_email(account, "general", body="general", category="General"),
        ],
    )
    results = {"ok": "S", "fails": None}  # the fake summariser is keyed on the email body
    with patch(SUMM, side_effect=lambda body: results[body]):
        assert sync_service.summarize_pending(db, account.id) == 1

    summaries = {e.id.split(":")[1]: e.summary for e in repo.list_in_window(db, account.user_id, 1)}
    assert summaries == {"ok": "S", "fails": None, "general": None}
    assert {e.id.split(":")[1] for e in repo.list_pending_summaries(db, account.id)} == {"fails"}


def test_summaries_run_concurrently(db, account):
    repo.upsert_many(db, [stored_email(account, str(i), category="Important") for i in range(3)])
    barrier = threading.Barrier(3, timeout=5)  # only passes if 3 calls are in flight together

    def summarise(body):
        barrier.wait()
        return "S"

    with patch(SUMM, side_effect=summarise), patch.object(sync_service, "get_settings") as settings:
        settings.return_value.summary_workers = 3
        assert sync_service.summarize_pending(db, account.id) == 3


def test_is_stale(db, account):
    assert sync_service.is_stale(db, account.id, "Last 1 Day", 300) is True

    repo.mark_synced(db, account.id, "Last 1 Day")
    assert sync_service.is_stale(db, account.id, "Last 1 Day", 300) is False
    assert sync_service.is_stale(db, account.id, "Last 1 Week", 300) is True  # per timeframe

    db.merge(
        SyncState(
            account_id=account.id,
            timeframe="Last 1 Day",
            synced_at=datetime.now(timezone.utc) - timedelta(seconds=600),
        )
    )
    db.commit()
    assert sync_service.is_stale(db, account.id, "Last 1 Day", 300) is True
