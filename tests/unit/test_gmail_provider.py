import base64
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

from app.providers import PROVIDERS, ProviderAuthError, get_provider, gmail
from app.providers.gmail import GmailProvider, fetch_message, list_message_ids
from tests.conftest import make_account

CREDS = json.dumps({"token": "t", "refresh_token": "r", "client_id": "c", "client_secret": "s"})


def _b64(s):
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def _service(detail=None, messages=None):
    svc = MagicMock()
    svc.users().messages().list().execute.return_value = {"messages": messages or []}
    svc.users().messages().get().execute.return_value = detail or {}
    return svc


def _http_error(status):
    return HttpError(MagicMock(status=status), b"{}")


# --- Gmail API helpers ---------------------------------------------------------------------------


def test_list_message_ids():
    svc = _service(messages=[{"id": "a"}, {"id": "b"}])
    assert list_message_ids(svc, "Last 1 Week") == ["a", "b"]
    assert svc.users().messages().list.call_args.kwargs["q"] == "newer_than:7d -in:sent -in:drafts"


def test_list_message_ids_empty():
    assert list_message_ids(_service(), "Last 1 Day") == []


def test_fetch_plain_text_multipart_with_real_date():
    detail = {
        "internalDate": "1700000000000",
        "threadId": "thread-9",
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Hi"},
                {"name": "From", "value": "a@b.com"},
                {"name": "List-Unsubscribe", "value": "<https://x.com/u?id=1>, <mailto:u@x.com>"},
                {"name": "List-Unsubscribe-Post", "value": "List-Unsubscribe=One-Click"},
            ],
            "parts": [{"mimeType": "text/plain", "body": {"data": _b64("hello world")}}],
        },
    }
    assert fetch_message(_service(detail), "1") == {
        "id": "1",
        "sender": "a@b.com",
        "subject": "Hi",
        "body": "hello world",
        "date": datetime.fromtimestamp(1700000000, tz=timezone.utc),
        "thread_id": "thread-9",
        "unsubscribe_url": "https://x.com/u?id=1",
        "unsubscribe_one_click": True,
    }


def test_nested_parts_and_html_fallback():
    nested = {
        "payload": {
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [{"mimeType": "text/plain", "body": {"data": _b64("deep text")}}],
                }
            ]
        }
    }
    assert fetch_message(_service(nested), "1")["body"] == "deep text"

    html_only = {"payload": {"parts": [{"mimeType": "text/html", "body": {"data": _b64("<p>x</p>")}}]}}
    assert fetch_message(_service(html_only), "1")["body"] == "<p>x</p>"


def test_single_part_body():
    assert fetch_message(_service({"payload": {"body": {"data": _b64("solo")}}}), "1")["body"] == "solo"


def test_falls_back_to_snippet_defaults_and_now():
    out = fetch_message(_service({"payload": {"headers": []}, "snippet": "snip"}), "1")
    assert out["subject"] == "No Subject" and out["sender"] == "Unknown" and out["body"] == "snip"
    assert (datetime.now(timezone.utc) - out["date"]).total_seconds() < 60


def test_body_is_truncated():
    detail = {"payload": {"body": {"data": _b64("x" * 5000)}}}
    assert len(fetch_message(_service(detail), "1")["body"]) == 4000


# --- GmailProvider -------------------------------------------------------------------------------


def test_provider_delegates_to_helpers():
    provider = GmailProvider(CREDS)
    with (
        patch.object(gmail, "build", return_value="SERVICE"),
        patch.object(gmail, "list_message_ids", return_value=["a"]) as lst,
        patch.object(gmail, "fetch_message", return_value={"id": "a"}) as fetch,
    ):
        assert provider.list_message_ids("Last 1 Day") == ["a"]
        assert provider.fetch_message("a") == {"id": "a"}
    assert lst.call_args.args == ("SERVICE", "Last 1 Day", None) and fetch.call_args.args == ("SERVICE", "a")


def test_refresh_failure_becomes_provider_auth_error():
    provider = GmailProvider(CREDS)
    with (
        patch.object(gmail, "build"),
        patch.object(gmail, "list_message_ids", side_effect=RefreshError("invalid_grant")),
        pytest.raises(ProviderAuthError, match="invalid_grant"),
    ):
        provider.list_message_ids("Last 1 Day")


@pytest.mark.parametrize("status", [401, 403])
def test_http_auth_errors_become_provider_auth_error(status):
    provider = GmailProvider(CREDS)
    with (
        patch.object(gmail, "build"),
        patch.object(gmail, "fetch_message", side_effect=_http_error(status)),
        pytest.raises(ProviderAuthError),
    ):
        provider.fetch_message("a")


def test_other_http_errors_propagate():
    provider = GmailProvider(CREDS)
    with (
        patch.object(gmail, "build"),
        patch.object(gmail, "fetch_message", side_effect=_http_error(500)),
        pytest.raises(HttpError),
    ):
        provider.fetch_message("a")


def test_export_credentials_only_when_token_changed():
    provider = GmailProvider(CREDS)
    assert provider.export_credentials() is None

    provider._creds.token = "refreshed-token"
    exported = provider.export_credentials()
    assert json.loads(exported)["token"] == "refreshed-token"
    assert json.loads(exported)["refresh_token"] == "r"


def test_get_provider_decrypts_and_builds_the_right_class(db, user):
    account = make_account(db, user)
    assert isinstance(get_provider(account), GmailProvider)
    assert "google" in PROVIDERS


def test_get_provider_rejects_unknown_provider(db, user):
    account = make_account(db, user)
    account.provider = "carrier-pigeon"
    with pytest.raises(ValueError, match="Unsupported"):
        get_provider(account)


# --- pagination, batching, sent threads -------------------------------------------------------------


def _paged_service(pages):
    """A service whose messages().list() returns the given pages in turn."""
    svc = MagicMock()
    svc.users().messages().list().execute.side_effect = pages
    svc.users().messages().list.reset_mock()
    return svc


def test_list_message_ids_follows_pagination():
    svc = _paged_service(
        [
            {"messages": [{"id": "a"}, {"id": "b"}], "nextPageToken": "p2"},
            {"messages": [{"id": "c"}], "nextPageToken": "p3"},
            {"messages": [{"id": "d"}]},
        ]
    )
    assert list_message_ids(svc, "Last 1 Month") == ["a", "b", "c", "d"]
    tokens = [c.kwargs.get("pageToken") for c in svc.users().messages().list.call_args_list]
    assert tokens == [None, "p2", "p3"]


def test_list_message_ids_stops_at_the_limit():
    svc = _paged_service(
        [
            {"messages": [{"id": str(i)} for i in range(5)], "nextPageToken": "more"},
            {"messages": [{"id": "x"}]},
        ]
    )
    list_message_ids(svc, "Last 1 Month", limit=3)
    calls = svc.users().messages().list.call_args_list
    assert calls[0].kwargs["maxResults"] == 3  # never asks for more than the limit
    assert len(calls) == 1  # and does not fetch a second page once it has enough


class _FakeBatch:
    """Runs each queued request's callback like googleapiclient's BatchHttpRequest does."""

    def __init__(self, responses):
        self.responses = responses
        self.queue = []

    def add(self, request, callback, request_id):
        self.queue.append((callback, request_id))

    def execute(self):
        for callback, request_id in self.queue:
            outcome = self.responses[request_id]
            if isinstance(outcome, Exception):
                callback(request_id, None, outcome)
            else:
                callback(request_id, outcome, None)


def _detail(subject):
    return {"payload": {"headers": [{"name": "Subject", "value": subject}], "body": {"data": _b64("body")}}}


def test_fetch_messages_batches_requests_and_keeps_order():
    svc = MagicMock()
    batches = []

    def new_batch():
        batches.append(_FakeBatch({"a": _detail("A"), "b": _detail("B"), "c": _detail("C")}))
        return batches[-1]

    svc.new_batch_http_request.side_effect = new_batch
    out = gmail.fetch_messages(svc, ["c", "a", "b"], batch_size=2)

    assert [m["id"] for m in out] == ["c", "a", "b"] and [m["subject"] for m in out] == ["C", "A", "B"]
    assert len(batches) == 2  # 3 messages, batches of 2


def test_fetch_messages_retries_failed_items_individually():
    svc = MagicMock()
    svc.new_batch_http_request.side_effect = lambda: _FakeBatch({"a": _detail("A"), "b": _http_error(429)})
    svc.users().messages().get().execute.return_value = _detail("B-retried")
    out = gmail.fetch_messages(svc, ["a", "b"])
    assert [m["subject"] for m in out] == ["A", "B-retried"]


def test_fetch_messages_raises_authorisation_errors():
    svc = MagicMock()
    svc.new_batch_http_request.side_effect = lambda: _FakeBatch({"a": _http_error(401)})
    with pytest.raises(HttpError):
        gmail.fetch_messages(svc, ["a"])


def test_provider_fetch_messages_maps_auth_errors():
    provider = GmailProvider(CREDS)
    with (
        patch.object(gmail, "build"),
        patch.object(gmail, "fetch_messages", side_effect=_http_error(403)),
        pytest.raises(ProviderAuthError),
    ):
        provider.fetch_messages(["a"])


def _sent_message(mid, ts, labels, to="them@x.com", subject="Proposal"):
    return {
        "id": mid,
        "internalDate": str(ts * 1000),
        "labelIds": labels,
        "payload": {"headers": [{"name": "To", "value": to}, {"name": "Subject", "value": subject}]},
    }


def _threads_service(threads):
    svc = MagicMock()
    svc.users().messages().list().execute.return_value = {
        "messages": [{"id": f"m-{t}", "threadId": t} for t in threads]
        + [{"id": "dup", "threadId": next(iter(threads))}]
    }
    svc.users().threads().get.side_effect = lambda **kw: MagicMock(
        execute=lambda: {"messages": threads[kw["id"]]}
    )
    return svc


def test_sent_threads_report_who_has_the_last_word():
    svc = _threads_service(
        {
            "waiting": [_sent_message("1", 100, ["SENT"])],
            "answered": [_sent_message("2", 100, ["SENT"]), _sent_message("3", 200, ["INBOX"])],
            "you-again": [
                _sent_message("4", 100, ["INBOX"]),
                _sent_message("5", 300, ["SENT"], subject="Re: hi"),
            ],
        }
    )
    by_id = {t["thread_id"]: t for t in gmail.list_sent_threads(svc, 14)}

    assert by_id["waiting"]["awaiting"] is True and by_id["waiting"]["recipient"] == "them@x.com"
    assert by_id["answered"]["awaiting"] is False
    assert by_id["you-again"]["awaiting"] is True and by_id["you-again"]["subject"] == "Re: hi"
    assert by_id["waiting"]["sent_at"] == datetime.fromtimestamp(100, tz=timezone.utc)
    assert len(by_id) == 3  # the duplicate thread id is only looked at once


def test_sent_threads_ignore_drafts_and_read_metadata_only():
    svc = _threads_service(
        {"t": [_sent_message("1", 100, ["SENT"]), _sent_message("2", 200, ["DRAFT", "SENT"])]}
    )
    result = gmail.list_sent_threads(svc, 14)
    assert result[0]["awaiting"] is False  # the newest message is a draft, not something you sent
    assert svc.users().threads().get.call_args.kwargs["format"] == "metadata"
