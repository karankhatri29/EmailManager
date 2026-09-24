from datetime import datetime, timedelta, timezone

from app.db.models import FollowUp
from app.repositories import followups as repo
from app.repositories import settings as settings_repo
from app.services import followups as service
from tests.conftest import FakeProvider

NOW = datetime.now(timezone.utc)


def _thread(
    thread_id="t1", awaiting=True, days_ago=4, recipient="Client <client@corp.com>", subject="Proposal"
):
    return {
        "thread_id": thread_id,
        "subject": subject,
        "recipient": recipient,
        "sent_at": NOW - timedelta(days=days_ago),
        "awaiting": awaiting,
    }


def _sync(db, account, *threads):
    return service.sync_followups(db, account, FakeProvider(sent_threads=threads), NOW)


def test_conversations_awaiting_a_reply_are_tracked(db, account):
    assert _sync(db, account, _thread()) == 1
    (row,) = db.query(FollowUp).all()
    assert (
        row.status == "waiting" and row.subject == "Proposal" and row.recipient == "Client <client@corp.com>"
    )
    assert row.user_id == account.user_id and row.account_id == account.id and row.nudged_at is None


def test_the_nudge_time_uses_the_users_own_setting(db, account):
    settings_repo.update(db, settings_repo.get_or_create(db, account.user_id), followup_days=5)
    _sync(db, account, _thread(days_ago=4))
    row = db.query(FollowUp).one()
    assert abs((row.nudge_at - row.sent_at).total_seconds() - 5 * 86400) < 1


def test_syncing_again_does_not_duplicate(db, account):
    assert _sync(db, account, _thread()) == 1
    assert _sync(db, account, _thread()) == 0
    assert db.query(FollowUp).count() == 1


def test_answered_conversations_are_not_tracked_and_tracked_ones_become_replied(db, account):
    assert _sync(db, account, _thread("answered", awaiting=False)) == 0
    assert db.query(FollowUp).count() == 0

    _sync(db, account, _thread("t1"))
    _sync(db, account, _thread("t1", awaiting=False))  # they replied since the last sync
    assert db.query(FollowUp).one().status == "replied"


def test_a_reply_then_another_unanswered_message_starts_a_new_wait(db, account):
    _sync(db, account, _thread("t1", days_ago=6))
    _sync(db, account, _thread("t1", awaiting=False))
    row = db.query(FollowUp).one()
    row.nudged_at = NOW
    db.commit()

    _sync(db, account, _thread("t1", days_ago=1))  # you wrote again and it is unanswered
    db.expire_all()
    row = db.query(FollowUp).one()
    assert row.status == "waiting" and row.nudged_at is None
    assert abs((NOW - row.sent_at.replace(tzinfo=timezone.utc)).days - 1) <= 1


def test_writing_again_before_a_reply_restarts_the_wait(db, account):
    _sync(db, account, _thread("t1", days_ago=6))
    first_nudge = db.query(FollowUp).one().nudge_at
    _sync(db, account, _thread("t1", days_ago=1))
    db.expire_all()
    assert db.query(FollowUp).one().nudge_at > first_nudge


def test_dismissed_follow_ups_stay_dismissed(db, account):
    _sync(db, account, _thread("t1"))
    row = db.query(FollowUp).one()
    row.status = repo.DISMISSED
    db.commit()
    _sync(db, account, _thread("t1"))
    db.expire_all()
    assert db.query(FollowUp).one().status == "dismissed"


def test_automated_and_self_addressed_recipients_are_ignored(db, account):
    threads = [
        _thread("noreply", recipient="noreply@shop.com"),
        _thread("donotreply", recipient="Do Not Reply <do-not-reply@bank.com>"),
        _thread("notify", recipient="notifications@github.com"),
        _thread("self", recipient=account.email_address),
        _thread("empty", recipient=""),
        _thread("real", recipient="Ann <ann@corp.com>, noreply@x.com"),  # one human among the recipients
    ]
    assert _sync(db, account, *threads) == 1
    assert db.query(FollowUp).one().thread_id == "real"


def test_conversations_nobody_answered_for_a_month_are_dropped(db, account):
    assert _sync(db, account, _thread("stale", days_ago=45)) == 0


def test_providers_that_cannot_list_sent_mail_are_skipped(db, account):
    class NoSentMail:  # e.g. a provider without list_sent_threads
        pass

    assert service.sync_followups(db, account, NoSentMail(), NOW) == 0


def test_waiting_days_and_due_listing(db, account):
    _sync(db, account, _thread("old", days_ago=5), _thread("fresh", days_ago=0))
    by_thread = {f.thread_id: f for f in db.query(FollowUp)}
    assert (
        service.waiting_days(by_thread["old"], NOW) == 5
        and service.waiting_days(by_thread["fresh"], NOW) == 0
    )

    due = repo.list_due_for_nudge(db, NOW)
    assert [f.thread_id for f in due] == ["old"]  # 3-day default: the 5-day-old one is due, today's is not
    assert [f.thread_id for f in repo.list_for_user(db, account.user_id)] == [
        "old",
        "fresh",
    ]  # longest wait first
