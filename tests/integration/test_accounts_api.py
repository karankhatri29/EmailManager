import json
from unittest.mock import patch

from app.core.security import decrypt
from app.db.models import Email, MailAccount
from app.repositories import emails as emails_repo
from tests.conftest import make_email, patch_provider, stored_email

OAUTH = "app.api.accounts.google_oauth"


def _start_connect(client):
    """Runs the first half of the flow; returns the state stored in the session."""
    with patch(
        f"{OAUTH}.authorization_url",
        return_value=("https://accounts.google.com/consent?x=1", "STATE", "VERIFIER"),
    ):
        return client.get("/api/accounts/connect/google", follow_redirects=False)


def test_list_is_empty_at_first(auth_client):
    assert auth_client.get("/api/accounts").json() == []


def test_connect_redirects_to_google(auth_client):
    r = _start_connect(auth_client)
    assert r.status_code == 307 and r.headers["location"] == "https://accounts.google.com/consent?x=1"


def test_callback_connects_the_mailbox_and_starts_a_sync(auth_client, db, manager):
    _start_connect(auth_client)
    creds = json.dumps({"token": "t", "refresh_token": "rt"})
    with (
        patch(f"{OAUTH}.finish", return_value=(creds, "me@gmail.com")) as finish,
        patch_provider([make_email("m1", body="Lunch was great")]),
    ):
        r = auth_client.get(
            "/api/accounts/google/callback", params={"code": "CODE", "state": "STATE"}, follow_redirects=False
        )
        manager.wait(10)

    assert r.status_code == 307 and r.headers["location"] == "/?connected=me@gmail.com"
    finish.assert_called_once_with("CODE", "STATE", "VERIFIER")

    accounts = auth_client.get("/api/accounts").json()
    assert len(accounts) == 1
    assert accounts[0]["email_address"] == "me@gmail.com" and accounts[0]["provider"] == "google"
    assert accounts[0]["status"] == "active" and accounts[0]["last_synced_at"] is not None
    assert "credentials" not in accounts[0]

    stored = db.query(MailAccount).one()
    assert "rt" not in stored.credentials and json.loads(decrypt(stored.credentials))["refresh_token"] == "rt"
    assert db.query(Email).count() == 1  # the first sync ran in the background


def test_callback_rejects_a_wrong_state(auth_client, db):
    _start_connect(auth_client)
    with patch(f"{OAUTH}.finish") as finish:
        r = auth_client.get(
            "/api/accounts/google/callback", params={"code": "C", "state": "FORGED"}, follow_redirects=False
        )
    assert r.headers["location"] == "/?connect_error=invalid_state"
    finish.assert_not_called()
    assert db.query(MailAccount).count() == 0


def test_callback_state_cannot_be_replayed(auth_client):
    _start_connect(auth_client)
    with patch(f"{OAUTH}.finish", return_value=("{}", "me@gmail.com")):
        auth_client.get(
            "/api/accounts/google/callback", params={"code": "C", "state": "STATE"}, follow_redirects=False
        )
        again = auth_client.get(
            "/api/accounts/google/callback", params={"code": "C", "state": "STATE"}, follow_redirects=False
        )
    assert again.headers["location"] == "/?connect_error=invalid_state"


def test_callback_without_starting_the_flow_is_rejected(auth_client):
    r = auth_client.get(
        "/api/accounts/google/callback", params={"code": "C", "state": "STATE"}, follow_redirects=False
    )
    assert r.headers["location"] == "/?connect_error=invalid_state"


def test_user_denying_access_is_handled(auth_client):
    _start_connect(auth_client)
    r = auth_client.get(
        "/api/accounts/google/callback", params={"error": "access_denied"}, follow_redirects=False
    )
    assert r.headers["location"] == "/?connect_error=access_denied"


def test_callback_when_logged_out_is_rejected(client):
    r = client.get(
        "/api/accounts/google/callback", params={"code": "C", "state": "S"}, follow_redirects=False
    )
    assert r.headers["location"] == "/?connect_error=login_required"


def test_token_exchange_failure_is_handled(auth_client, db):
    _start_connect(auth_client)
    with patch(f"{OAUTH}.finish", side_effect=RuntimeError("invalid_grant")):
        r = auth_client.get(
            "/api/accounts/google/callback", params={"code": "C", "state": "STATE"}, follow_redirects=False
        )
    assert r.headers["location"] == "/?connect_error=failed"
    assert db.query(MailAccount).count() == 0


def test_reconnecting_the_same_mailbox_does_not_duplicate_it(auth_client, db, manager):
    for token in ("first", "second"):
        _start_connect(auth_client)
        with (
            patch(f"{OAUTH}.finish", return_value=(json.dumps({"refresh_token": token}), "me@gmail.com")),
            patch_provider([]),
        ):
            auth_client.get(
                "/api/accounts/google/callback",
                params={"code": "C", "state": "STATE"},
                follow_redirects=False,
            )
            manager.wait(10)
    assert len(auth_client.get("/api/accounts").json()) == 1
    assert json.loads(decrypt(db.query(MailAccount).one().credentials))["refresh_token"] == "second"


def test_a_user_can_connect_several_mailboxes(auth_client, manager):
    for address in ("one@gmail.com", "two@gmail.com"):
        _start_connect(auth_client)
        with patch(f"{OAUTH}.finish", return_value=("{}", address)), patch_provider([]):
            auth_client.get(
                "/api/accounts/google/callback",
                params={"code": "C", "state": "STATE"},
                follow_redirects=False,
            )
            manager.wait(10)
    assert [a["email_address"] for a in auth_client.get("/api/accounts").json()] == [
        "one@gmail.com",
        "two@gmail.com",
    ]


def test_accounts_needing_reconnection_are_reported(auth_client, account, db):
    from app.repositories import accounts as accounts_repo

    accounts_repo.mark_needs_reauth(db, account.id, "invalid_grant")
    listed = auth_client.get("/api/accounts").json()[0]
    assert listed["status"] == "needs_reauth" and listed["last_error"] == "invalid_grant"


def test_users_only_see_their_own_mailboxes(auth_client, bob_client, account, bob_account):
    assert [a["email_address"] for a in auth_client.get("/api/accounts").json()] == ["me@gmail.com"]
    assert [a["email_address"] for a in bob_client.get("/api/accounts").json()] == ["bob@gmail.com"]


def test_disconnect_deletes_the_mailbox_its_mail_and_derived_activities(auth_client, db, user, account):
    emails_repo.upsert_many(db, [stored_email(account, "m1", category="Urgent / Action Required")])
    auth_client.post("/api/activities", json={"title": "my own task"})

    with patch(f"{OAUTH}.revoke") as revoke:
        assert auth_client.delete(f"/api/accounts/{account.id}").status_code == 204
    revoke.assert_called_once()

    assert auth_client.get("/api/accounts").json() == []
    assert db.query(Email).count() == 0
    assert [a["title"] for a in auth_client.get("/api/activities").json()] == ["my own task"]


def test_disconnect_still_works_if_google_revocation_fails(auth_client, account):
    with patch(f"{OAUTH}.revoke", side_effect=RuntimeError("network")):
        assert auth_client.delete(f"/api/accounts/{account.id}").status_code == 204
    assert auth_client.get("/api/accounts").json() == []


def test_cannot_disconnect_someone_elses_mailbox(auth_client, bob_client, bob_account, db):
    assert auth_client.delete(f"/api/accounts/{bob_account.id}").status_code == 404
    assert bob_client.get("/api/accounts").json()[0]["email_address"] == "bob@gmail.com"
    assert auth_client.delete("/api/accounts/9999").status_code == 404
