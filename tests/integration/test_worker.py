from unittest.mock import patch

from app.repositories import accounts as accounts_repo
from app.worker import tasks
from app.worker.celery_app import celery_app
from tests.conftest import make_account


def test_beat_schedules_sync_inbox():
    entry = celery_app.conf.beat_schedule["sync-inbox"]
    assert entry["task"] == "app.worker.tasks.sync_inbox"
    assert entry["schedule"] > 0


def test_sync_inbox_syncs_every_active_mailbox(session_factory, db, user, account, bob_account):
    broken = make_account(db, user, "broken@gmail.com")
    accounts_repo.mark_needs_reauth(db, broken.id, "x")
    with (
        patch.object(tasks, "SessionLocal", session_factory),
        patch.object(tasks, "sync_account", return_value=2) as sync,
    ):
        assert tasks.sync_inbox("Last 1 Week") == 4
    assert {c.args[1].id for c in sync.call_args_list} == {account.id, bob_account.id}
    assert {c.args[2] for c in sync.call_args_list} == {"Last 1 Week"}


def test_one_failing_mailbox_does_not_stop_the_others(session_factory, db, account, bob_account):
    def flaky(db_, acct, timeframe):
        if acct.id == account.id:
            raise RuntimeError("boom")
        return 5

    with (
        patch.object(tasks, "SessionLocal", session_factory),
        patch.object(tasks, "sync_account", side_effect=flaky),
    ):
        assert tasks.sync_inbox() == 5


def test_sync_inbox_with_no_mailboxes(session_factory):
    with patch.object(tasks, "SessionLocal", session_factory):
        assert tasks.sync_inbox() == 0
