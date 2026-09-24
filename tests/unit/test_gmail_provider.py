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
    assert svc.users().messages().list.call_args.kwargs["q"] == "newer_than:7d"


def test_list_message_ids_empty():
    assert list_message_ids(_service(), "Last 1 Day") == []


def test_fetch_plain_text_multipart_with_real_date():
    detail = {
        "internalDate": "1700000000000",
        "payload": {
            "headers": [{"name": "Subject", "value": "Hi"}, {"name": "From", "value": "a@b.com"}],
            "parts": [{"mimeType": "text/plain", "body": {"data": _b64("hello world")}}],
        },
    }
    assert fetch_message(_service(detail), "1") == {
        "id": "1",
        "sender": "a@b.com",
        "subject": "Hi",
        "body": "hello world",
        "date": datetime.fromtimestamp(1700000000, tz=timezone.utc),
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
    assert lst.call_args.args == ("SERVICE", "Last 1 Day") and fetch.call_args.args == ("SERVICE", "a")


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
