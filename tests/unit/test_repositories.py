from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.security import decrypt
from app.db.models import Activity, Email, SyncState
from app.repositories import accounts as accounts_repo
from app.repositories import activities as activities_repo
from app.repositories import emails as repo
from app.repositories import users as users_repo
from tests.conftest import make_account, stored_email


def _at(days_ago):
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


# --- users ---------------------------------------------------------------------------------------


def test_users_are_looked_up_case_insensitively(db):
    users_repo.create(db, "  Someone@Example.COM ", "password123")
    assert users_repo.get_by_email(db, "someone@example.com").email == "someone@example.com"
    assert users_repo.get_by_email(db, "SOMEONE@EXAMPLE.COM") is not None
    assert users_repo.get_by_email(db, "other@example.com") is None


def test_email_is_unique(db):
    users_repo.create(db, "a@example.com", "password123")
    with pytest.raises(IntegrityError):
        users_repo.create(db, "a@example.com", "password123")


def test_calendar_token_rotation(db):
    user = users_repo.create(db, "a@example.com", "password123")
    old = user.calendar_token
    new = users_repo.rotate_calendar_token(db, user)
    assert new != old
    assert users_repo.get_by_calendar_token(db, old) is None
    assert users_repo.get_by_calendar_token(db, new).id == user.id


# --- mail accounts -------------------------------------------------------------------------------


def test_account_credentials_are_stored_encrypted(db, user):
    account = make_account(db, user)
    assert "refresh_token" not in account.credentials
    assert "refresh_token" in decrypt(account.credentials)


def test_reconnecting_updates_the_existing_account_and_reactivates_it(db, user):
    account = make_account(db, user)
    accounts_repo.mark_needs_reauth(db, account.id, "invalid_grant")
    assert db.get(type(account), account.id).status == "needs_reauth"

    again = accounts_repo.upsert(db, user.id, "google", "me@gmail.com", '{"refresh_token": "new"}')
    assert again.id == account.id
    assert again.status == "active" and again.last_error is None
    assert "new" in decrypt(again.credentials)
    assert len(accounts_repo.list_for_user(db, user.id)) == 1


def test_accounts_are_scoped_to_their_owner(db, user, bob):
    mine, theirs = make_account(db, user), make_account(db, bob, "bob@gmail.com")
    assert [a.id for a in accounts_repo.list_for_user(db, user.id)] == [mine.id]
    assert accounts_repo.get_for_user(db, user.id, theirs.id) is None
    assert accounts_repo.get_for_user(db, bob.id, theirs.id).id == theirs.id


def test_active_listing_excludes_accounts_needing_reauth(db, user):
    ok, broken = make_account(db, user), make_account(db, user, "old@gmail.com")
    accounts_repo.mark_needs_reauth(db, broken.id, "x")
    assert [a.id for a in accounts_repo.list_active_for_user(db, user.id)] == [ok.id]
    assert accounts_repo.list_all_active_ids(db) == [ok.id]


def test_update_credentials_re_encrypts(db, user):
    account = make_account(db, user)
    accounts_repo.update_credentials(db, account.id, '{"token": "fresh"}')
    assert decrypt(db.get(type(account), account.id).credentials) == '{"token": "fresh"}'


def test_last_synced_map(db, user):
    a, b = make_account(db, user), make_account(db, user, "b@gmail.com")
    repo.mark_synced(db, a.id, "Last 1 Day")
    assert set(accounts_repo.last_synced_map(db, [a.id, b.id])) == {a.id}
    assert accounts_repo.last_synced_map(db, []) == {}


def test_deleting_an_account_removes_its_data_but_not_manual_activities_or_other_accounts(db, user):
    doomed, kept = make_account(db, user), make_account(db, user, "keep@gmail.com")
    repo.upsert_many(db, [stored_email(doomed, "m1"), stored_email(kept, "m1")])
    repo.mark_synced(db, doomed.id, "Last 1 Day")
    repo.mark_synced(db, kept.id, "Last 1 Day")
    activities_repo.create(db, user.id, title="from doomed", email_id=f"{doomed.id}:m1", source="email")
    activities_repo.create(db, user.id, title="from kept", email_id=f"{kept.id}:m1", source="email")
    activities_repo.create(db, user.id, title="mine", source="manual")

    accounts_repo.delete(db, doomed)

    assert {e.id for e in db.query(Email)} == {f"{kept.id}:m1"}
    assert {s.account_id for s in db.query(SyncState)} == {kept.id}
    assert {a.title for a in db.query(Activity)} == {"from kept", "mine"}
    assert [a.id for a in accounts_repo.list_for_user(db, user.id)] == [kept.id]


# --- emails --------------------------------------------------------------------------------------


def test_email_ids_are_prefixed_with_the_account():
    assert repo.make_email_id(7, "abc") == "7:abc"


def test_same_provider_message_id_can_exist_in_two_mailboxes(db, user, bob):
    a, b = make_account(db, user), make_account(db, bob, "bob@gmail.com")
    repo.upsert_many(db, [stored_email(a, "same"), stored_email(b, "same")])
    assert db.query(Email).count() == 2


def test_upsert_inserts_then_updates(db, account):
    repo.upsert_many(db, [stored_email(account, "a", category="General")])
    repo.upsert_many(db, [stored_email(account, "a", category="Important", summary="S")])
    rows = repo.list_in_window(db, account.user_id, 1)
    assert len(rows) == 1 and rows[0].category == "Important" and rows[0].summary == "S"


def test_get_existing_ids(db, account):
    repo.upsert_many(db, [stored_email(account, "a"), stored_email(account, "b")])
    assert repo.get_existing_ids(db, [f"{account.id}:a", "9:zzz"]) == {f"{account.id}:a"}
    assert repo.get_existing_ids(db, []) == set()


def test_list_in_window_filters_by_date_orders_desc_and_isolates_users(db, user, bob, account, bob_account):
    repo.upsert_many(
        db,
        [
            stored_email(account, "old", date=_at(10)),
            stored_email(account, "mid", date=_at(3)),
            stored_email(account, "new", date=_at(0)),
            stored_email(bob_account, "bobs", date=_at(0)),
        ],
    )
    ids = lambda days: [e.id.split(":")[1] for e in repo.list_in_window(db, user.id, days)]  # noqa: E731
    assert ids(1) == ["new"] and ids(7) == ["new", "mid"] and ids(30) == ["new", "mid", "old"]
    assert [e.id.split(":")[1] for e in repo.list_in_window(db, bob.id, 30)] == ["bobs"]


def test_pending_summaries_are_per_account_and_only_urgent_or_important(db, user, account):
    other = make_account(db, user, "other@gmail.com")
    repo.upsert_many(
        db,
        [
            stored_email(account, "urgent", category="Urgent / Action Required"),
            stored_email(account, "important", category="Important"),
            stored_email(account, "done", category="Important", summary="S"),
            stored_email(account, "general", category="General"),
            stored_email(account, "promo", category="Promotional"),
            stored_email(other, "elsewhere", category="Important"),
        ],
    )
    assert {e.id.split(":")[1] for e in repo.list_pending_summaries(db, account.id)} == {
        "urgent",
        "important",
    }


def test_set_summary(db, account):
    repo.upsert_many(db, [stored_email(account, "a", category="Important")])
    repo.set_summary(db, f"{account.id}:a", "SUM")
    assert repo.list_in_window(db, account.user_id, 1)[0].summary == "SUM"
    assert repo.list_pending_summaries(db, account.id) == []


def test_sync_state_is_per_account_and_timeframe(db, user, account):
    other = make_account(db, user, "other@gmail.com")
    assert repo.get_synced_at(db, account.id, "Last 1 Day") is None
    repo.mark_synced(db, account.id, "Last 1 Day")
    synced_at = repo.get_synced_at(db, account.id, "Last 1 Day")
    assert synced_at.tzinfo is not None and (datetime.now(timezone.utc) - synced_at).total_seconds() < 5
    assert repo.get_synced_at(db, account.id, "Last 1 Week") is None
    assert repo.get_synced_at(db, other.id, "Last 1 Day") is None


# --- activities ----------------------------------------------------------------------------------


def test_activity_listing_range_backlog_order_and_isolation(db, user, bob):
    now = datetime(2026, 10, 15, tzinfo=timezone.utc)
    mk = activities_repo.create
    mk(db, user.id, title="late", start_at=now + timedelta(days=20))
    mk(db, user.id, title="in-2", start_at=now + timedelta(days=2))
    mk(db, user.id, title="in-1", start_at=now + timedelta(days=1))
    mk(db, user.id, title="backlog")
    mk(db, bob.id, title="bobs", start_at=now + timedelta(days=1))

    titles = lambda **kw: [a.title for a in activities_repo.list_for_user(db, user.id, **kw)]  # noqa: E731
    assert titles(start=now, end=now + timedelta(days=5)) == ["in-1", "in-2", "backlog"]
    assert titles(start=now, end=now + timedelta(days=5), include_unscheduled=False) == ["in-1", "in-2"]
    assert titles() == ["in-1", "in-2", "late", "backlog"]
    assert activities_repo.get_for_user(db, bob.id, activities_repo.list_for_user(db, user.id)[0].id) is None


def test_scheduled_open_excludes_done_and_unscheduled(db, user):
    when = datetime(2026, 10, 15, tzinfo=timezone.utc)
    activities_repo.create(db, user.id, title="open", start_at=when)
    activities_repo.create(db, user.id, title="done", start_at=when, status="done")
    activities_repo.create(db, user.id, title="backlog")
    assert [a.title for a in activities_repo.list_scheduled_open(db, user.id)] == ["open"]


def test_only_one_activity_per_email(db, account):
    repo.upsert_many(db, [stored_email(account, "a")])
    activities_repo.create(db, account.user_id, title="one", email_id=f"{account.id}:a")
    with pytest.raises(IntegrityError):
        activities_repo.create(db, account.user_id, title="two", email_id=f"{account.id}:a")
