import os

from cryptography.fernet import Fernet

# Must be set before the app is imported: no periodic sync thread, no real database file, known keys.
os.environ.update(
    INPROCESS_SYNC="false",
    DATABASE_URL="sqlite://",
    SECRET_KEY="test-secret-key",
    ENCRYPTION_KEY=Fernet.generate_key().decode(),
    GOOGLE_CLIENT_ID="test-client-id",
    GOOGLE_CLIENT_SECRET="test-client-secret",
    MICROSOFT_CLIENT_ID="test-ms-client-id",
    MICROSOFT_CLIENT_SECRET="test-ms-client-secret",
    PUBLIC_BASE_URL="http://testserver",
)

import json  # noqa: E402
from contextlib import contextmanager  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from unittest.mock import patch  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.api.deps import get_sync_manager  # noqa: E402
from app.core.security import login_limiter  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import enable_sqlite_foreign_keys, get_db  # noqa: E402
from app.main import create_app  # noqa: E402
from app.repositories import accounts as accounts_repo  # noqa: E402
from app.repositories import users as users_repo  # noqa: E402
from app.services import background  # noqa: E402
from app.services.senders import sender_address as parse_sender_address  # noqa: E402

ALICE = "alice@example.com"
BOB = "bob@example.com"
PASSWORD = "correct-horse-battery"


# --- database / app ------------------------------------------------------------------------------


@pytest.fixture
def session_factory():
    """Fresh in-memory SQLite database per test (with foreign keys enforced, like Postgres)."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    enable_sqlite_foreign_keys(engine)
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def db(session_factory):
    with session_factory() as session:
        yield session


@pytest.fixture
def manager(session_factory):
    """A fresh SyncManager wired to the test database."""
    mgr = background.SyncManager(max_workers=3)
    with patch.object(background, "SessionLocal", session_factory):
        yield mgr
        mgr.wait(10)
    mgr._pool.shutdown(wait=True)


@pytest.fixture
def make_client(session_factory, manager):
    """Factory for independent clients (separate cookie jars), e.g. two different users."""

    def factory() -> TestClient:
        app = create_app()

        def override_get_db():
            with session_factory() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_sync_manager] = lambda: manager
        return TestClient(app)

    return factory


@pytest.fixture(autouse=True)
def _reset_login_limiter():
    login_limiter._failures.clear()


@pytest.fixture
def client(make_client):
    """Not logged in."""
    return make_client()


def register(client, email=ALICE, password=PASSWORD):
    response = client.post("/api/auth/register", json={"email": email, "password": password})
    assert response.status_code == 201, response.text
    return response


@pytest.fixture
def auth_client(client):
    """Logged in as ALICE."""
    register(client, ALICE)
    return client


@pytest.fixture
def bob_client(make_client):
    """Logged in as BOB (a different user)."""
    c = make_client()
    register(c, BOB)
    return c


@pytest.fixture
def user(auth_client, db):
    return users_repo.get_by_email(db, ALICE)


@pytest.fixture
def bob(bob_client, db):
    return users_repo.get_by_email(db, BOB)


def make_account(db, user, address="me@gmail.com"):
    credentials = json.dumps({"token": "t", "refresh_token": "r", "client_id": "c", "client_secret": "s"})
    return accounts_repo.upsert(db, user.id, "google", address, credentials)


@pytest.fixture
def account(db, user):
    return make_account(db, user)


@pytest.fixture
def bob_account(db, bob):
    return make_account(db, bob, "bob@gmail.com")


# --- data helpers --------------------------------------------------------------------------------


def make_email(id="m1", subject="Hello", body="Just saying hi", sender="Bob <bob@x.com>", **extra):
    """A raw message as a provider returns it."""
    email = {"id": id, "subject": subject, "body": body, "sender": sender, "date": datetime.now(timezone.utc)}
    email.update(extra)
    return email


def stored_email(account, message_id="m1", **extra):
    """An email dict as stored in the database (already processed and owned)."""
    email = {
        **make_email(message_id),
        "sender_address": parse_sender_address(extra.get("sender", "Bob <bob@x.com>")),
        "id": f"{account.id}:{message_id}",
        "user_id": account.user_id,
        "account_id": account.id,
        "score": 2.2,
        "category": "General",
        "summary": None,
        "task": None,
    }
    email.update(extra)
    return email


class FakeProvider:
    """Stands in for a mailbox provider."""

    def __init__(self, raw_emails=(), refreshed=None, error=None):
        self.by_id = {e["id"]: e for e in raw_emails}
        self.refreshed = refreshed
        self.error = error
        self.fetched: list[str] = []
        self.list_calls = 0

    def list_message_ids(self, timeframe):
        self.list_calls += 1
        if self.error:
            raise self.error
        return list(self.by_id)

    def fetch_message(self, message_id):
        self.fetched.append(message_id)
        return dict(self.by_id[message_id])

    def export_credentials(self):
        return self.refreshed


@contextmanager
def patch_provider(raw_emails=(), **kwargs):
    """Every mailbox sync uses a FakeProvider serving `raw_emails`. Yields the provider."""
    fake = FakeProvider(raw_emails, **kwargs)
    with patch("app.services.sync_service.get_provider", return_value=fake):
        yield fake
