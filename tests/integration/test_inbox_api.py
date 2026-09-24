from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.repositories import accounts as accounts_repo
from app.repositories import emails as repo
from tests.conftest import make_account, make_email, patch_provider, stored_email

SUMM = "app.services.sync_service.summarize_email"


def test_first_request_returns_immediately_then_the_background_sync_fills_data(auth_client, account, manager):
    now = datetime.now(timezone.utc)
    raw = [
        make_email("m1", "OTP", "your verification code is 1", date=now - timedelta(hours=3)),
        make_email("m2", body="Lunch was great", date=now - timedelta(hours=1)),
    ]
    with patch_provider(raw), patch(SUMM, return_value="S"):
        first = auth_client.get("/api/emails", params={"time_filter": "Last 1 Day"})
        assert first.status_code == 200 and first.json() == []  # not blocked on the mailbox
        manager.wait(10)
        data = auth_client.get("/api/emails", params={"time_filter": "Last 1 Day"}).json()

    assert [e["id"].split(":")[1] for e in data] == ["m2", "m1"]  # newest first
    assert data[1]["summary"] == "S" and data[1]["category"] == "Urgent / Action Required"
    assert data[0]["account_id"] == account.id
    assert {
        "id",
        "account_id",
        "sender",
        "subject",
        "body",
        "score",
        "category",
        "date",
        "summary",
        "task",
        "reason",
        "category_source",
        "is_done",
        "snoozed_until",
        "thread_id",
        "sender_address",
        "unsubscribe_url",
        "unsubscribe_one_click",
    } == set(data[0])


def test_no_mailboxes_means_no_emails_and_no_sync(auth_client):
    assert auth_client.get("/api/emails").json() == []
    assert auth_client.get("/api/sync/status").json()["syncing"] is False


def test_fresh_data_does_not_resync(auth_client, account, manager):
    with patch_provider([make_email("m1", body="Lunch was great")]) as provider:
        auth_client.get("/api/emails")
        manager.wait(10)
        auth_client.get("/api/emails")
        manager.wait(10)
    assert provider.list_calls == 1


def test_refresh_resyncs_but_downloads_and_summarises_nothing_twice(auth_client, account, manager):
    with (
        patch_provider([make_email("m1", "OTP", "verification code")]) as provider,
        patch(SUMM, return_value="S") as summ,
    ):
        auth_client.get("/api/emails")
        manager.wait(10)
        auth_client.get("/api/emails", params={"refresh": "true"})
        manager.wait(10)
    assert provider.list_calls == 2 and provider.fetched == ["m1"] and summ.call_count == 1


def test_every_connected_mailbox_is_synced_and_merged_into_one_inbox(auth_client, db, user, account, manager):
    second = make_account(db, user, "second@gmail.com")
    with patch_provider([make_email("m1", body="Lunch was great")]):
        auth_client.get("/api/emails")
        manager.wait(10)
        data = auth_client.get("/api/emails").json()
    assert {e["account_id"] for e in data} == {account.id, second.id}


def test_mailboxes_needing_reconnection_are_skipped(auth_client, db, account, manager):
    accounts_repo.mark_needs_reauth(db, account.id, "invalid_grant")
    with patch_provider([make_email("m1")]) as provider:
        auth_client.get("/api/emails", params={"refresh": "true"})
        manager.wait(10)
    assert provider.list_calls == 0


def test_each_timeframe_syncs_independently(auth_client, account, manager):
    with patch_provider([]) as provider:
        for tf in ("Last 1 Day", "Last 1 Week", "Last 1 Week"):
            auth_client.get("/api/emails", params={"time_filter": tf})
            manager.wait(10)
    assert provider.list_calls == 2


def test_window_filters_by_real_date(auth_client, db, account):
    repo.upsert_many(
        db,
        [
            stored_email(account, "today", date=datetime.now(timezone.utc)),
            stored_email(account, "last-week", date=datetime.now(timezone.utc) - timedelta(days=4)),
            stored_email(account, "last-month", date=datetime.now(timezone.utc) - timedelta(days=20)),
        ],
    )
    for tf in ("Last 1 Day", "Last 1 Week", "Last 1 Month"):
        repo.mark_synced(db, account.id, tf)

    def ids(tf):
        return [
            e["id"].split(":")[1] for e in auth_client.get("/api/emails", params={"time_filter": tf}).json()
        ]

    assert ids("Last 1 Day") == ["today"]
    assert ids("Last 1 Week") == ["today", "last-week"]
    assert ids("Last 1 Month") == ["today", "last-week", "last-month"]


def test_users_never_see_each_others_emails(auth_client, bob_client, db, account, bob_account):
    repo.upsert_many(db, [stored_email(account, "alices"), stored_email(bob_account, "bobs")])
    for acct in (account, bob_account):
        repo.mark_synced(db, acct.id, "Last 1 Day")

    assert [e["id"] for e in auth_client.get("/api/emails").json()] == [f"{account.id}:alices"]
    assert [e["id"] for e in bob_client.get("/api/emails").json()] == [f"{bob_account.id}:bobs"]


def test_invalid_timeframe_rejected(auth_client):
    assert auth_client.get("/api/emails", params={"time_filter": "Last 10 Years"}).status_code == 422
    assert auth_client.post("/api/sync", params={"time_filter": "nope"}).status_code == 422


def test_sync_failure_is_reported_in_status_and_stored_emails_still_served(auth_client, db, account, manager):
    repo.upsert_many(db, [stored_email(account, "kept")])
    with patch_provider(error=RuntimeError("boom")):
        r = auth_client.get("/api/emails")
        manager.wait(10)

    assert r.status_code == 200 and [e["id"].split(":")[1] for e in r.json()] == ["kept"]
    status = auth_client.get("/api/sync/status").json()
    assert status["syncing"] is False and "boom" in status["error"]


def test_login_failure_shows_up_as_a_mailbox_needing_reconnection(auth_client, account, manager):
    from app.providers import ProviderAuthError

    with patch_provider(error=ProviderAuthError("Google rejected the saved login")):
        auth_client.get("/api/emails")
        manager.wait(10)
    listed = auth_client.get("/api/accounts").json()[0]
    assert listed["status"] == "needs_reauth" and "rejected" in listed["last_error"]


def test_sync_status_idle(auth_client):
    assert auth_client.get("/api/sync/status").json() == {
        "syncing": False,
        "timeframe": None,
        "started_at": None,
        "finished_at": None,
        "new_emails": None,
        "error": None,
    }


def test_sync_status_only_covers_your_own_mailboxes(auth_client, bob_client, account, bob_account, manager):
    with patch_provider([make_email("m1", body="Lunch was great")]):
        bob_client.post("/api/sync")
        manager.wait(10)
    assert bob_client.get("/api/sync/status").json()["new_emails"] == 1
    assert auth_client.get("/api/sync/status").json()["finished_at"] is None


def test_post_sync_starts_background_sync(auth_client, account, manager):
    with patch_provider([make_email("m1", body="Lunch was great")]):
        r = auth_client.post("/api/sync", params={"time_filter": "Last 1 Week"})
        assert r.status_code == 202 and r.json()["timeframe"] == "Last 1 Week"
        manager.wait(10)

    status = auth_client.get("/api/sync/status").json()
    assert status["syncing"] is False and status["new_emails"] == 1 and status["error"] is None


def test_scheduler_orders_the_users_tasks(auth_client, db, account, bob_account):
    repo.upsert_many(
        db,
        [
            stored_email(
                account, "plain", subject="Task", body="no dates here", category="Important", score=3.3
            ),
            stored_email(
                account,
                "asap",
                subject="Task2",
                body="please finish this asap",
                category="Urgent / Action Required",
                score=4.0,
            ),  # fmt: skip
            stored_email(account, "promo", category="Promotional"),
            stored_email(bob_account, "bobs", category="Urgent / Action Required", score=4.9),
        ],
    )
    tasks = auth_client.get("/api/scheduler").json()
    assert [t["id"].split(":")[1] for t in tasks] == ["asap", "plain"]  # Bob's mail is not included
    assert set(tasks[0]) == {"id", "sender", "task", "deadline", "sort_tier", "base_score"}


def test_scheduler_empty(auth_client):
    assert auth_client.get("/api/scheduler").json() == []
