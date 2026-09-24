import pytest

from app.core.security import login_limiter
from tests.conftest import ALICE, PASSWORD, register


def test_register_logs_you_in_and_returns_the_user(client):
    r = register(client, "New.User@Example.com")
    assert r.json()["email"] == "new.user@example.com" and "password" not in r.text
    assert "session" in r.cookies or client.cookies.get("session")
    assert client.get("/api/auth/me").json()["email"] == "new.user@example.com"


def test_duplicate_email_is_rejected_case_insensitively(client, make_client):
    register(client, ALICE)
    r = make_client().post("/api/auth/register", json={"email": ALICE.upper(), "password": PASSWORD})
    assert r.status_code == 409


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "not-an-email", "password": PASSWORD},
        {"email": ALICE, "password": "short"},
        {"email": ALICE, "password": "x" * 200},
        {"email": ALICE},
        {},
    ],
)
def test_register_validation(client, payload):
    assert client.post("/api/auth/register", json=payload).status_code == 422


def test_login_logout_cycle(client, make_client):
    register(client, ALICE)
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401

    fresh = make_client()
    assert fresh.post("/api/auth/login", json={"email": ALICE, "password": PASSWORD}).status_code == 200
    assert fresh.get("/api/auth/me").json()["email"] == ALICE
    assert fresh.post("/api/auth/logout").status_code == 204
    assert fresh.get("/api/auth/me").status_code == 401


def test_login_is_case_insensitive_on_email(client, make_client):
    register(client, ALICE)
    assert (
        make_client().post("/api/auth/login", json={"email": ALICE.upper(), "password": PASSWORD}).status_code
        == 200
    )


def test_wrong_password_and_unknown_user_look_identical(client, make_client):
    register(client, ALICE)
    other = make_client()
    wrong = other.post("/api/auth/login", json={"email": ALICE, "password": "wrong-password"})
    unknown = other.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": "wrong-password"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "Invalid email or password"}


def test_repeated_failures_lock_the_login_but_only_for_that_email(client, make_client):
    register(client, ALICE)
    other = make_client()
    for _ in range(login_limiter.max_failures):
        assert (
            other.post("/api/auth/login", json={"email": ALICE, "password": "nope-nope"}).status_code == 401
        )

    # Even the right password is refused while locked out...
    assert other.post("/api/auth/login", json={"email": ALICE, "password": PASSWORD}).status_code == 429
    # ...but other accounts are unaffected.
    assert (
        other.post("/api/auth/login", json={"email": "someone@else.com", "password": "nope-nope"}).status_code
        == 401
    )


def test_successful_login_resets_the_failure_count(client, make_client):
    register(client, ALICE)
    other = make_client()
    for _ in range(login_limiter.max_failures - 1):
        other.post("/api/auth/login", json={"email": ALICE, "password": "nope-nope"})
    assert other.post("/api/auth/login", json={"email": ALICE, "password": PASSWORD}).status_code == 200
    assert not login_limiter.is_blocked(ALICE)


PROTECTED = [
    ("get", "/api/auth/me"),
    ("get", "/api/accounts"),
    ("get", "/api/accounts/connect/google"),
    ("delete", "/api/accounts/1"),
    ("get", "/api/emails"),
    ("post", "/api/sync"),
    ("get", "/api/sync/status"),
    ("get", "/api/scheduler"),
    ("get", "/api/activities"),
    ("post", "/api/activities"),
    ("patch", "/api/activities/1"),
    ("delete", "/api/activities/1"),
    ("get", "/api/calendar/feed"),
    ("post", "/api/calendar/feed/rotate"),
]


@pytest.mark.parametrize("method,path", PROTECTED)
def test_every_private_endpoint_requires_login(client, method, path):
    kwargs = {"json": {"title": "x"}} if method in ("post", "patch") else {}
    assert getattr(client, method)(path, **kwargs).status_code == 401


def test_a_session_for_a_deleted_user_is_rejected(auth_client, db, user):
    db.delete(user)
    db.commit()
    assert auth_client.get("/api/auth/me").status_code == 401


def test_a_tampered_session_cookie_is_rejected(auth_client):
    auth_client.cookies.set("session", "eyJ1aWQiOiAxfQ==.forged.signature", domain="testserver.local")
    auth_client.cookies.clear()
    auth_client.cookies.set("session", "eyJ1aWQiOiAxfQ==.forged.signature")
    assert auth_client.get("/api/auth/me").status_code == 401


def test_session_cookie_is_httponly_and_samesite(client):
    r = register(client, ALICE)
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


def test_invite_only_sign_up_lets_only_listed_addresses_register(client):
    from unittest.mock import patch

    from app.core.config import get_settings

    restricted = get_settings().model_copy(update={"allowed_emails": " Friend@Example.com , me@x.com"})
    with patch("app.api.auth.get_settings", return_value=restricted):
        refused = client.post("/api/auth/register", json={"email": "stranger@x.com", "password": PASSWORD})
        assert refused.status_code == 403 and "invite only" in refused.json()["detail"]
        assert register(client, "FRIEND@example.com").status_code == 201  # case does not matter
