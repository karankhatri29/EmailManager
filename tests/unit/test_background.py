import threading
import time
from unittest.mock import patch

from app.repositories import accounts as accounts_repo
from app.services import background
from tests.conftest import make_account


def test_idle_status_for_unknown_or_no_accounts(manager):
    for ids in ([], [999]):
        status = manager.status(ids)
        assert status["syncing"] is False and status["error"] is None and status["started_at"] is None


def test_trigger_runs_sync_for_the_account_and_records_the_result(manager, account):
    with patch.object(background, "sync_account", return_value=4) as sync:
        assert manager.trigger(account.id, "Last 1 Week") is True
        manager.wait(5)

    assert sync.call_args.args[1].id == account.id and sync.call_args.args[2] == "Last 1 Week"
    status = manager.status([account.id])
    assert status["syncing"] is False and status["new_emails"] == 4 and status["error"] is None
    assert status["timeframe"] == "Last 1 Week" and status["finished_at"] is not None


def test_only_one_sync_per_account_at_a_time(manager, account):
    release = threading.Event()

    def blocking(db, acct, timeframe):
        release.wait(5)
        return 0

    with patch.object(background, "sync_account", side_effect=blocking) as sync:
        assert manager.trigger(account.id, "Last 1 Day") is True
        assert manager.is_syncing(account.id) is True
        assert manager.trigger(account.id, "Last 1 Week") is False  # rejected while running
        release.set()
        manager.wait(5)

    assert sync.call_count == 1
    assert manager.is_syncing(account.id) is False
    assert manager.trigger(account.id, "Last 1 Week") is True  # free again
    manager.wait(5)


def test_different_accounts_sync_in_parallel(manager, db, user, account):
    other = make_account(db, user, "second@gmail.com")
    barrier = threading.Barrier(2, timeout=5)  # only passes if both syncs are in flight together

    def sync(db_, acct, timeframe):
        barrier.wait()
        return 1

    with patch.object(background, "sync_account", side_effect=sync):
        assert manager.trigger(account.id, "Last 1 Day") and manager.trigger(other.id, "Last 1 Day")
        manager.wait(10)

    assert manager.status([account.id, other.id])["new_emails"] == 2


def test_failure_is_captured_per_account_not_raised(manager, db, user, account):
    other = make_account(db, user, "second@gmail.com")

    def sync(db_, acct, timeframe):
        if acct.id == account.id:
            raise RuntimeError("gmail down")
        return 3

    with patch.object(background, "sync_account", side_effect=sync):
        manager.trigger(account.id, "Last 1 Day")
        manager.trigger(other.id, "Last 1 Day")
        manager.wait(10)

    broken, fine = manager.status([account.id]), manager.status([other.id])
    assert (
        "RuntimeError" in broken["error"] and "gmail down" in broken["error"] and broken["new_emails"] is None
    )
    assert fine["error"] is None and fine["new_emails"] == 3
    combined = manager.status([account.id, other.id])
    assert (
        combined["error"] == broken["error"] and combined["new_emails"] == 3 and combined["syncing"] is False
    )


def test_error_clears_on_the_next_successful_run(manager, account):
    with patch.object(background, "sync_account", side_effect=RuntimeError("x")):
        manager.trigger(account.id, "Last 1 Day")
        manager.wait(5)
    with patch.object(background, "sync_account", return_value=0):
        manager.trigger(account.id, "Last 1 Day")
        manager.wait(5)
    assert manager.status([account.id])["error"] is None


def test_accounts_needing_reconnection_are_not_synced(manager, db, account):
    accounts_repo.mark_needs_reauth(db, account.id, "invalid_grant")
    with patch.object(background, "sync_account") as sync:
        manager.trigger(account.id, "Last 1 Day")
        manager.wait(5)
    sync.assert_not_called()
    assert manager.status([account.id])["syncing"] is False


def test_sync_all_accounts_triggers_every_active_account(manager, db, user, account, bob_account):
    broken = make_account(db, user, "broken@gmail.com")
    accounts_repo.mark_needs_reauth(db, broken.id, "x")
    with (
        patch.object(background, "sync_manager", manager),
        patch.object(background, "sync_account", return_value=0) as sync,
    ):
        assert background.sync_all_accounts("Last 1 Day") == 2
        manager.wait(5)
    assert {c.args[1].id for c in sync.call_args_list} == {account.id, bob_account.id}


def test_periodic_sync_runs_repeatedly_and_stops(session_factory):
    with (
        patch.object(background, "SessionLocal", session_factory),
        patch.object(background, "sync_all_accounts") as sync_all,
    ):
        stop = background.start_periodic_sync(0.05, "Last 1 Day")
        time.sleep(0.4)
        stop.set()
        time.sleep(0.2)
        after_stop = sync_all.call_count
        time.sleep(0.2)

    assert after_stop >= 3 and sync_all.call_count == after_stop
    sync_all.assert_called_with("Last 1 Day")


def test_periodic_sync_survives_a_failing_round(session_factory):
    with patch.object(
        background, "sync_all_accounts", side_effect=[RuntimeError("boom"), 0, 0, 0, 0, 0, 0, 0]
    ) as sync_all:
        stop = background.start_periodic_sync(0.05)
        time.sleep(0.3)
        stop.set()
    assert sync_all.call_count >= 2
