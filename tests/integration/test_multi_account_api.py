"""Connecting Outlook next to Gmail, and viewing mail per mailbox."""

import json
from unittest.mock import patch

from app.core.security import decrypt
from app.db.models import MailAccount
from app.repositories import accounts as accounts_repo
from app.repositories import emails as emails_repo
from tests.conftest import make_account, make_email, patch_provider, stored_email

OAUTH = "app.api.accounts.microsoft_oauth"
TOMORROW = "Please send the report by tomorrow"


def _start_connect(client):
    with patch(
        f"{OAUTH}.authorization_url", return_value=("https://login.microsoftonline.com/x", "MSSTATE", "MSVER")
    ):
        return client.get("/api/accounts/connect/microsoft", follow_redirects=False)


def _callback(client, **params):
    params = {"code": "CODE", "state": "MSSTATE", **params}
    return client.get("/api/accounts/microsoft/callback", params=params, follow_redirects=False)


# --- connecting Outlook --------------------------------------------------------------------------


def test_connect_redirects_to_microsoft(auth_client):
    r = _start_connect(auth_client)
    assert r.status_code == 307 and r.headers["location"] == "https://login.microsoftonline.com/x"


def test_connect_requires_login(client):
    assert client.get("/api/accounts/connect/microsoft", follow_redirects=False).status_code == 401


def test_connect_reports_when_outlook_is_not_configured(auth_client):
    from types import SimpleNamespace

    with patch("app.api.accounts.get_settings", return_value=SimpleNamespace(microsoft_configured=False)):
        r = auth_client.get("/api/accounts/connect/microsoft", follow_redirects=False)
    assert r.headers["location"] == "/?connect_error=not_configured"


def test_callback_connects_an_outlook_mailbox_and_starts_a_sync(auth_client, db, manager):
    _start_connect(auth_client)
    creds = json.dumps({"access_token": "a", "refresh_token": "secret-refresh", "expires_at": 9e9})
    with (
        patch(f"{OAUTH}.finish", return_value=(creds, "me@outlook.com")) as finish,
        patch_provider([make_email("m1", body="Lunch was great")]),
    ):
        r = _callback(auth_client)
        manager.wait(10)

    assert r.headers["location"] == "/?connected=me@outlook.com"
    finish.assert_called_once_with("CODE", "MSSTATE", "MSVER")

    (listed,) = auth_client.get("/api/accounts").json()
    assert listed["provider"] == "microsoft" and listed["email_address"] == "me@outlook.com"
    assert listed["last_synced_at"] is not None

    stored = db.query(MailAccount).one()
    assert "secret-refresh" not in stored.credentials  # encrypted at rest
    assert json.loads(decrypt(stored.credentials))["refresh_token"] == "secret-refresh"


def test_callback_rejects_a_wrong_state_and_a_replay(auth_client, db):
    _start_connect(auth_client)
    with patch(f"{OAUTH}.finish") as finish:
        assert _callback(auth_client, state="FORGED").headers["location"] == "/?connect_error=invalid_state"
        assert (
            _callback(auth_client).headers["location"] == "/?connect_error=invalid_state"
        )  # state was consumed
    finish.assert_not_called()
    assert db.query(MailAccount).count() == 0


def test_a_google_flow_cannot_complete_a_microsoft_callback(auth_client):
    """The two providers keep separate session state, so one cannot be used to forge the other."""
    with patch(
        "app.api.accounts.google_oauth.authorization_url",
        return_value=("https://g.example", "GSTATE", "GVER"),
    ):
        auth_client.get("/api/accounts/connect/google", follow_redirects=False)
    with patch(f"{OAUTH}.finish") as finish:
        r = _callback(auth_client, state="GSTATE")
    assert r.headers["location"] == "/?connect_error=invalid_state"
    finish.assert_not_called()


def test_access_denied_and_exchange_failures_are_handled(auth_client, db):
    _start_connect(auth_client)
    assert (
        _callback(auth_client, error="access_denied").headers["location"] == "/?connect_error=access_denied"
    )

    _start_connect(auth_client)
    with patch(f"{OAUTH}.finish", side_effect=RuntimeError("boom")):
        assert _callback(auth_client).headers["location"] == "/?connect_error=failed"
    assert db.query(MailAccount).count() == 0


def test_callback_when_logged_out_is_rejected(client):
    assert _callback(client).headers["location"] == "/?connect_error=login_required"


def test_gmail_and_outlook_can_be_connected_side_by_side(auth_client, db, user):
    make_account(db, user, "me@gmail.com")
    _start_connect(auth_client)
    with patch(f"{OAUTH}.finish", return_value=("{}", "me@outlook.com")), patch_provider([]):
        _callback(auth_client)
    providers = {a["email_address"]: a["provider"] for a in auth_client.get("/api/accounts").json()}
    assert providers == {"me@gmail.com": "google", "me@outlook.com": "microsoft"}


def test_the_same_address_can_exist_as_both_a_gmail_and_an_outlook_mailbox(db, user):
    accounts_repo.upsert(db, user.id, "google", "same@x.com", "{}")
    accounts_repo.upsert(db, user.id, "microsoft", "same@x.com", "{}")
    assert len(accounts_repo.list_for_user(db, user.id)) == 2


def test_disconnecting_outlook_deletes_its_mail_without_calling_google(auth_client, db, user):
    outlook = accounts_repo.upsert(db, user.id, "microsoft", "me@outlook.com", "{}")
    emails_repo.upsert_many(db, [stored_email(outlook, "m1")])
    with patch("app.api.accounts.google_oauth.revoke") as revoke:
        assert auth_client.delete(f"/api/accounts/{outlook.id}").status_code == 204
    revoke.assert_not_called()
    assert auth_client.get("/api/emails").json() == []


# --- per-mailbox views ---------------------------------------------------------------------------


def _two_mailboxes(db, user):
    gmail = make_account(db, user, "me@gmail.com")
    outlook = accounts_repo.upsert(db, user.id, "microsoft", "me@outlook.com", "{}")
    emails_repo.upsert_many(
        db,
        [
            stored_email(
                gmail,
                "g1",
                subject="from gmail",
                body=TOMORROW,
                task="Send report",
                category="Urgent / Action Required",
            ),
            stored_email(
                outlook,
                "o1",
                subject="from outlook",
                body=TOMORROW,
                task="Send slides",
                category="Urgent / Action Required",
            ),
        ],
    )
    return gmail, outlook


def test_emails_default_to_all_mailboxes_and_can_be_filtered_to_one(auth_client, db, user):
    gmail, outlook = _two_mailboxes(db, user)

    everything = auth_client.get("/api/emails").json()
    assert {e["subject"] for e in everything} == {"from gmail", "from outlook"}

    only_outlook = auth_client.get("/api/emails", params={"account_id": outlook.id}).json()
    assert [e["subject"] for e in only_outlook] == ["from outlook"]
    assert only_outlook[0]["account_id"] == outlook.id

    only_gmail = auth_client.get("/api/emails", params={"account_id": gmail.id}).json()
    assert [e["subject"] for e in only_gmail] == ["from gmail"]


def test_the_task_schedule_can_be_filtered_to_one_mailbox(auth_client, db, user):
    gmail, outlook = _two_mailboxes(db, user)
    everything = {t["id"] for t in auth_client.get("/api/scheduler").json()}
    assert everything == {f"{gmail.id}:g1", f"{outlook.id}:o1"}

    only_outlook = auth_client.get("/api/scheduler", params={"account_id": outlook.id}).json()
    assert [t["id"] for t in only_outlook] == [f"{outlook.id}:o1"]


def test_filtering_by_someone_elses_or_a_missing_mailbox_is_a_404(auth_client, bob_client, bob_account):
    assert auth_client.get("/api/emails", params={"account_id": bob_account.id}).status_code == 404
    assert auth_client.get("/api/scheduler", params={"account_id": bob_account.id}).status_code == 404
    assert auth_client.get("/api/emails", params={"account_id": 9999}).status_code == 404


def test_a_provider_that_needs_reconnecting_does_not_hide_the_others(auth_client, db, user):
    gmail, outlook = _two_mailboxes(db, user)
    accounts_repo.mark_needs_reauth(db, outlook.id, "invalid_grant")
    statuses = {a["email_address"]: a["status"] for a in auth_client.get("/api/accounts").json()}
    assert statuses == {"me@gmail.com": "active", "me@outlook.com": "needs_reauth"}
    assert len(auth_client.get("/api/emails").json()) == 2  # stored mail stays visible
