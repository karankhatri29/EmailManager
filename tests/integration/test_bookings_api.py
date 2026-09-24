from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.api.bookings import get_scanner
from app.db.models import Email
from app.repositories import emails as emails_repo
from app.services import booking_scan
from tests.conftest import make_account, stored_email
from tests.unit.test_bookings import BUS, FLIGHT, HOTEL, MOVIE_HTML, TRAIN

RECENT = datetime.now(timezone.utc) - timedelta(days=3)


def _store(db, account, mail, message_id, **extra):
    row = stored_email(
        account,
        message_id,
        subject=mail["subject"],
        body=mail["body"],
        sender=mail["sender"],
        date=RECENT,
        category=mail["category"],
        **extra,
    )
    emails_repo.upsert_many(db, [row])
    return row["id"]


@pytest.fixture
def scanner(session_factory):
    """A fresh scanner wired to the test database (never the real one, and never the network)."""
    s = booking_scan.BookingScanner(max_workers=2)
    with patch.object(booking_scan, "SessionLocal", session_factory):
        yield s
        s.wait(10)
    s._pool.shutdown(wait=True)


@pytest.fixture(autouse=True)
def _no_network():
    """Listing starts a scan of unscanned mailboxes; by default that reads an empty fake mailbox, never Google."""
    with patch("app.services.booking_scan.get_provider", side_effect=lambda account: FakeSearchProvider([])):
        yield


@pytest.fixture
def api(auth_client, scanner):
    """Logged in as Alice, using the test scanner."""
    auth_client.app.dependency_overrides[get_scanner] = lambda: scanner
    return auth_client


@pytest.fixture
def bob_api(bob_client, scanner):
    bob_client.app.dependency_overrides[get_scanner] = lambda: scanner
    return bob_client


class FakeSearchProvider:
    """A mailbox that has a few ticket mails, older than the regular inbox window."""

    def __init__(self, mails, error=None):
        self.mails = {m["id"]: m for m in mails}
        self.error = error
        self.full_reads: list[str] = []

    def search_booking_ids(self, days, limit):
        if self.error:
            raise self.error
        return list(self.mails)

    def fetch_full_text(self, message_id):
        self.full_reads.append(message_id)
        return dict(self.mails[message_id])

    def export_credentials(self):
        return None


def _raw(mail, message_id, days_ago=60):
    return {
        "id": message_id,
        "subject": mail["subject"],
        "body": mail["body"],
        "sender": mail["sender"],
        "date": datetime.now(timezone.utc) - timedelta(days=days_ago),
    }


# --- listing -----------------------------------------------------------------------------------------------


def test_lists_bookings_found_in_stored_mail_soonest_first(api, db, user):
    account = make_account(db, user)
    for i, mail in enumerate((HOTEL, FLIGHT, MOVIE_HTML, TRAIN, BUS)):
        _store(db, account, mail, f"m{i}")
    data = api.get("/api/bookings").json()
    kinds = [b["kind"] for b in data["items"]]
    assert kinds == ["flight", "movie", "train", "bus", "hotel"]  # 12, 13, 15, 20 and 21 October
    starts = [b["start_at"] for b in data["items"]]
    assert starts == sorted(starts)
    flight = data["items"][0]
    assert flight["title"] == "Delhi (DEL) → Mumbai (BOM)" and flight["reference"] == "X7K2QP"
    assert flight["email_id"].endswith(":m1") and flight["status"] == "confirmed"
    assert {"label": "Seat", "value": "14A"} in flight["details"]


def test_filter_by_kind_and_validation(api, db, user):
    account = make_account(db, user)
    _store(db, account, FLIGHT, "m1")
    _store(db, account, MOVIE_HTML, "m2")
    assert [b["kind"] for b in api.get("/api/bookings", params={"kind": "movie"}).json()["items"]] == [
        "movie"
    ]
    assert api.get("/api/bookings", params={"kind": "spaceship"}).status_code == 422


def test_ordinary_mail_is_not_a_booking(api, db, user):
    account = make_account(db, user)
    emails_repo.upsert_many(
        db,
        [stored_email(account, "m1", subject="Lunch tomorrow?", body="Are we still on? Bring the laptop.")],
    )
    assert api.get("/api/bookings").json()["items"] == []


def test_users_only_see_their_own_bookings(api, bob_api, db, user, bob):
    _store(db, make_account(db, user), FLIGHT, "m1")
    _store(db, make_account(db, bob, "bob@gmail.com"), MOVIE_HTML, "m2")
    assert [b["kind"] for b in api.get("/api/bookings").json()["items"]] == ["flight"]
    assert [b["kind"] for b in bob_api.get("/api/bookings").json()["items"]] == ["movie"]


def test_requires_login(client):
    assert client.get("/api/bookings").status_code == 401
    assert client.post("/api/bookings/scan").status_code == 401
    assert client.get("/api/bookings/calendar", params={"email_id": "1:x"}).status_code == 401


# --- calendar file ---------------------------------------------------------------------------------------------


def test_add_to_calendar_downloads_an_ics(api, db, user):
    account = make_account(db, user)
    email_id = _store(db, account, MOVIE_HTML, "m1")
    r = api.get("/api/bookings/calendar", params={"email_id": email_id})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar")
    assert "movie-booking.ics" in r.headers["content-disposition"]
    assert "SUMMARY:Movie: Kalki 2898 AD (Telugu)" in r.text and "DTSTART:20261013T193000" in r.text


def test_calendar_errors(api, db, user, bob):
    account = make_account(db, user)
    theirs = _store(db, make_account(db, bob, "bob@gmail.com"), FLIGHT, "b1")
    plain = stored_email(account, "m9", subject="hello", body="just chatting")
    emails_repo.upsert_many(db, [plain])
    undated = _store(
        db,
        account,
        dict(
            FLIGHT,
            subject="Booking confirmed",
            body="Booking ID: ZZ9911AA\nYour flight boarding pass is attached.",
        ),
        "m8",
    )
    assert api.get("/api/bookings/calendar", params={"email_id": theirs}).status_code == 404  # someone else's
    assert (
        api.get("/api/bookings/calendar", params={"email_id": plain["id"]}).status_code == 404
    )  # not a booking
    assert (
        api.get("/api/bookings/calendar", params={"email_id": undated}).status_code == 404
    )  # no date to add
    assert api.get("/api/bookings/calendar", params={"email_id": "999:nope"}).status_code == 404


# --- scanning the mailbox ----------------------------------------------------------------------------------------


def test_listing_starts_a_scan_of_mailboxes_that_were_never_scanned(api, db, user, scanner):
    account = make_account(db, user)
    fake = FakeSearchProvider([_raw(FLIGHT, "g1"), _raw(TRAIN, "g2")])
    with patch("app.services.booking_scan.get_provider", return_value=fake):
        first = api.get("/api/bookings").json()
        assert first["items"] == []  # not blocked on the mailbox
        scanner.wait(10)
        later = api.get("/api/bookings").json()

    assert sorted(b["kind"] for b in later["items"]) == ["flight", "train"]
    assert later["scanning"] is False and later["last_scan"] is not None
    assert sorted(fake.full_reads) == ["g1", "g2"]
    stored = db.query(Email).filter(Email.account_id == account.id).all()
    assert len(stored) == 2 and all(
        e.summary == "" for e in stored
    )  # no AI summaries queued for old ticket mails
    assert {e.id for e in stored} == {f"{account.id}:g1", f"{account.id}:g2"}


def test_a_scanned_mailbox_is_not_scanned_again_soon(api, db, user, scanner):
    make_account(db, user)
    fake = FakeSearchProvider([_raw(FLIGHT, "g1")])
    with patch("app.services.booking_scan.get_provider", return_value=fake) as get_provider:
        api.get("/api/bookings")
        scanner.wait(10)
        api.get("/api/bookings")
        scanner.wait(10)
    assert get_provider.call_count == 1
    with patch("app.services.booking_scan.get_provider", return_value=FakeSearchProvider([])) as get_provider:
        api.get("/api/bookings", params={"refresh": "true"})
        scanner.wait(10)
    assert get_provider.call_count == 1  # refresh=true forces it


def test_manual_scan_endpoint_and_status(api, db, user, scanner):
    make_account(db, user)
    with patch(
        "app.services.booking_scan.get_provider", return_value=FakeSearchProvider([_raw(HOTEL, "h1")])
    ):
        r = api.post("/api/bookings/scan")
        assert r.status_code == 202
        scanner.wait(10)
        status = api.get("/api/bookings/status").json()
    assert status["scanning"] is False and status["last_scan"] is not None and status["error"] is None
    assert [b["kind"] for b in api.get("/api/bookings").json()["items"]] == ["hotel"]


def test_mail_already_stored_in_full_is_not_fetched_again(api, db, user, scanner):
    account = make_account(db, user)
    _store(db, account, FLIGHT, "g1")  # short body: stored complete by the regular sync
    fake = FakeSearchProvider([_raw(FLIGHT, "g1")])
    with patch("app.services.booking_scan.get_provider", return_value=fake):
        api.post("/api/bookings/scan")
        scanner.wait(10)
    assert fake.full_reads == []


def test_a_body_cut_off_by_the_regular_sync_is_completed(api, db, user, scanner):
    account = make_account(db, user)
    cut_off = "x" * 4000  # the regular sync keeps 4000 characters
    emails_repo.upsert_many(
        db,
        [
            stored_email(
                account,
                "g1",
                subject=FLIGHT["subject"],
                sender=FLIGHT["sender"],
                body=cut_off,
                category="Important",
            )
        ],
    )
    fake = FakeSearchProvider([_raw(FLIGHT, "g1")])
    with patch("app.services.booking_scan.get_provider", return_value=fake):
        api.post("/api/bookings/scan")
        scanner.wait(10)
    assert fake.full_reads == ["g1"]
    assert db.get(Email, f"{account.id}:g1").body == FLIGHT["body"]
    assert api.get("/api/bookings").json()["items"][0]["reference"] == "X7K2QP"


def test_a_revoked_login_flags_the_mailbox_and_reports_the_error(api, db, user, scanner):
    from app.providers import ProviderAuthError

    account = make_account(db, user)
    fake = FakeSearchProvider([], error=ProviderAuthError("Google rejected the saved login"))
    with patch("app.services.booking_scan.get_provider", return_value=fake):
        api.post("/api/bookings/scan")
        scanner.wait(10)
    db.expire_all()
    assert db.get(type(account), account.id).status == "needs_reauth"
    assert "rejected" in (api.get("/api/bookings/status").json()["error"] or "")


def test_a_mailbox_that_needs_reconnecting_is_skipped(api, db, user, scanner):
    from app.repositories import accounts as accounts_repo

    account = make_account(db, user)
    accounts_repo.mark_needs_reauth(db, account.id, "invalid_grant")
    with patch("app.services.booking_scan.get_provider") as get_provider:
        api.get("/api/bookings")
        scanner.wait(10)
    get_provider.assert_not_called()


# --- the Gmail / Microsoft search and read implementations -----------------------------------------------------------


def test_gmail_query_covers_the_big_ticket_sellers():
    from app.providers.booking_search import gmail_query

    q = gmail_query(180)
    assert (
        q.startswith("newer_than:180d")
        and "bookmyshow.com" in q
        and "irctc.co.in" in q
        and "goindigo.in" in q
    )


def test_gmail_full_text_prefers_plain_text_and_converts_html_only_mails():
    import base64

    from app.providers.booking_search import _gmail_full

    def b64(s):
        return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")

    html_only = {
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Tickets"},
                {"name": "From", "value": "BookMyShow <a@bookmyshow.com>"},
            ],
            "mimeType": "multipart/alternative",
            "parts": [
                {
                    "mimeType": "text/html",
                    "body": {
                        "data": b64(
                            "<table><tr><td>Seats</td><td>H12</td></tr></table>" + "<!-- pad -->" * 900
                        )
                    },
                }
            ],
        },
        "internalDate": "1700000000000",
    }
    provider = MagicMock()
    provider.service.users().messages().get().execute.return_value = html_only
    result = _gmail_full(provider, "abc", 20000)
    assert (
        result["body"] == "Seats H12"
        and result["subject"] == "Tickets"
        and result["sender"].startswith("BookMyShow")
    )

    with_plain = {
        "payload": {
            "headers": [],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": b64("PNR: ABC123")}},
                {"mimeType": "text/html", "body": {"data": b64("<p>PNR: ABC123</p>")}},
            ],
        }
    }
    provider.service.users().messages().get().execute.return_value = with_plain
    assert _gmail_full(provider, "abc", 20000)["body"] == "PNR: ABC123"


def test_gmail_search_pages_and_stops_at_the_limit():
    from app.providers.booking_search import _gmail_search

    provider = MagicMock()
    provider.service.users().messages().list().execute.side_effect = [
        {"messages": [{"id": "a"}, {"id": "b"}], "nextPageToken": "t"},
        {"messages": [{"id": "c"}]},
    ]
    assert _gmail_search(provider, 180, 10) == ["a", "b", "c"]


def test_microsoft_search_filters_by_age_and_reads_full_text():
    import json
    import time

    from app.providers.booking_search import fetch_full_text, search_booking_ids
    from app.providers.microsoft import MicrosoftProvider

    provider = MicrosoftProvider(
        json.dumps({"access_token": "t", "refresh_token": "r", "expires_at": time.time() + 3600})
    )
    recent = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    old = (datetime.now(timezone.utc) - timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def response(body):
        r = MagicMock(status_code=200)
        r.json.return_value = body
        return r

    with patch("app.providers.microsoft.requests") as requests:
        requests.get.side_effect = [
            response(
                {
                    "value": [
                        {"id": "G-NEW", "receivedDateTime": recent},
                        {"id": "G-OLD", "receivedDateTime": old},
                    ]
                }
            ),
            response(
                {
                    "subject": "Your tickets",
                    "from": {"emailAddress": {"name": "PVR", "address": "a@pvrcinemas.com"}},
                    "body": {"content": "<p>Seats: A1</p>"},
                    "receivedDateTime": recent,
                }
            ),
        ]
        ids = search_booking_ids(provider, 180, 50)
        assert len(ids) == 1
        full = fetch_full_text(provider, ids[0])
    assert full["body"] == "Seats: A1" and full["sender"] == "PVR <a@pvrcinemas.com>"
    assert requests.get.call_args_list[0].kwargs["headers"]["ConsistencyLevel"] == "eventual"


def test_the_reader_can_fetch_a_ticket_mail_older_than_the_inbox_window(api, db, user, bob):
    account = make_account(db, user)
    email_id = _store(db, account, FLIGHT, "m1")
    theirs = _store(db, make_account(db, bob, "bob@gmail.com"), FLIGHT, "b1")
    r = api.get("/api/bookings/email", params={"email_id": email_id})
    assert r.status_code == 200 and r.json()["subject"] == FLIGHT["subject"] and "X7K2QP" in r.json()["body"]
    assert api.get("/api/bookings/email", params={"email_id": theirs}).status_code == 404  # someone else's
    assert api.get("/api/bookings/email", params={"email_id": "999:nope"}).status_code == 404
