import json
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

from app.providers import PROVIDERS, ProviderAuthError, get_provider, microsoft_oauth
from app.providers.microsoft import MicrosoftProvider, _format_sender, _parse_date, _short_id
from tests.conftest import make_account

MS = "app.providers.microsoft.requests"
MS_OAUTH = "app.providers.microsoft_oauth.requests"


def _creds(expires_in=3600, access="access-1"):
    return json.dumps(
        {"access_token": access, "refresh_token": "refresh-1", "expires_at": time.time() + expires_in}
    )


def _response(status=200, body=None):
    r = MagicMock(status_code=status, content=b"x")
    r.json.return_value = body if body is not None else {}
    r.raise_for_status.side_effect = None if status < 400 else RuntimeError(f"HTTP {status}")
    return r


# --- helpers -------------------------------------------------------------------------------------


def test_registered_as_a_provider():
    assert PROVIDERS["microsoft"] is MicrosoftProvider


def test_get_provider_builds_it_from_an_account(db, user):
    from app.repositories import accounts as accounts_repo

    account = accounts_repo.upsert(db, user.id, "microsoft", "me@outlook.com", _creds())
    assert isinstance(get_provider(account), MicrosoftProvider)


def test_long_graph_ids_are_shortened_to_a_stable_hash():
    graph_id = "AAMkAGI2" + "x" * 140
    assert len(_short_id(graph_id)) == 40 and _short_id(graph_id) == _short_id(graph_id)
    assert _short_id(graph_id) != _short_id(graph_id + "y")


def test_sender_formatting():
    assert (
        _format_sender({"from": {"emailAddress": {"name": "Ann", "address": "ann@x.com"}}})
        == "Ann <ann@x.com>"
    )
    assert _format_sender({"from": {"emailAddress": {"name": "a@x.com", "address": "a@x.com"}}}) == "a@x.com"
    assert _format_sender({"from": {"emailAddress": {"address": "a@x.com"}}}) == "a@x.com"
    assert _format_sender({}) == "Unknown"


def test_dates_are_parsed_as_aware_utc_with_a_fallback():
    assert _parse_date("2026-01-02T03:04:05Z") == datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    assert _parse_date("2026-01-02T03:04:05").tzinfo is not None
    assert _parse_date("garbage").tzinfo is not None and _parse_date(None).tzinfo is not None


# --- listing and fetching ------------------------------------------------------------------------


def test_list_message_ids_queries_the_inbox_and_hashes_ids():
    provider = MicrosoftProvider(_creds())
    with patch(MS) as requests:
        requests.get.return_value = _response(body={"value": [{"id": "G1"}, {"id": "G2"}]})
        ids = provider.list_message_ids("Last 1 Week")

    assert ids == [_short_id("G1"), _short_id("G2")]
    call = requests.get.call_args
    assert call.args[0].endswith("/me/mailFolders/inbox/messages")
    assert call.kwargs["headers"]["Authorization"] == "Bearer access-1"
    assert call.kwargs["params"]["$filter"].startswith("receivedDateTime ge ")


def test_fetch_message_maps_the_graph_message():
    provider = MicrosoftProvider(_creds())
    message = {
        "subject": "Quarterly report",
        "from": {"emailAddress": {"name": "Ann", "address": "ann@x.com"}},
        "body": {"content": "  Please review by Friday.  "},
        "receivedDateTime": "2026-01-02T03:04:05Z",
    }
    with patch(MS) as requests:
        requests.get.side_effect = [_response(body={"value": [{"id": "G1"}]}), _response(body=message)]
        (short,) = provider.list_message_ids("Last 1 Day")
        result = provider.fetch_message(short)

    assert result == {
        "id": short,
        "sender": "Ann <ann@x.com>",
        "subject": "Quarterly report",
        "body": "Please review by Friday.",
        "date": datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
    }
    fetch_call = requests.get.call_args
    assert fetch_call.args[0].endswith("/me/messages/G1")
    assert fetch_call.kwargs["headers"]["Prefer"] == 'outlook.body-content-type="text"'


def test_fetch_falls_back_to_the_preview_and_default_subject():
    provider = MicrosoftProvider(_creds())
    with patch(MS) as requests:
        requests.get.side_effect = [
            _response(body={"value": [{"id": "G1"}]}),
            _response(body={"body": {"content": ""}, "bodyPreview": "preview text"}),
        ]
        (short,) = provider.list_message_ids("Last 1 Day")
        result = provider.fetch_message(short)
    assert result["body"] == "preview text" and result["subject"] == "No Subject"


def test_fetching_an_id_that_was_never_listed_is_an_error():
    with pytest.raises(ValueError):
        MicrosoftProvider(_creds()).fetch_message("deadbeef")


# --- authentication ------------------------------------------------------------------------------


def test_unexpired_token_is_used_without_refreshing_and_reports_no_changes():
    provider = MicrosoftProvider(_creds())
    with patch(MS) as requests:
        requests.get.return_value = _response(body={"value": []})
        provider.list_message_ids("Last 1 Day")
    requests.post.assert_not_called()
    assert provider.export_credentials() is None


def test_expired_token_is_refreshed_and_the_new_credentials_exported():
    provider = MicrosoftProvider(_creds(expires_in=-10))
    with patch(MS) as requests:
        requests.post.return_value = _response(
            body={"access_token": "access-2", "refresh_token": "refresh-2", "expires_in": 3600}
        )
        requests.get.return_value = _response(body={"value": []})
        provider.list_message_ids("Last 1 Day")

    assert requests.post.call_args.kwargs["data"]["grant_type"] == "refresh_token"
    assert requests.get.call_args.kwargs["headers"]["Authorization"] == "Bearer access-2"
    exported = json.loads(provider.export_credentials())
    assert exported["access_token"] == "access-2" and exported["refresh_token"] == "refresh-2"


def test_refresh_keeps_the_old_refresh_token_if_none_is_returned():
    provider = MicrosoftProvider(_creds(expires_in=-10))
    with patch(MS) as requests:
        requests.post.return_value = _response(body={"access_token": "access-2", "expires_in": 3600})
        requests.get.return_value = _response(body={"value": []})
        provider.list_message_ids("Last 1 Day")
    assert json.loads(provider.export_credentials())["refresh_token"] == "refresh-1"


def test_a_rejected_refresh_token_means_the_mailbox_needs_reconnecting():
    provider = MicrosoftProvider(_creds(expires_in=-10))
    with patch(MS) as requests:
        requests.post.return_value = _response(
            400, {"error": "invalid_grant", "error_description": "expired"}
        )
        with pytest.raises(ProviderAuthError, match="expired"):
            provider.list_message_ids("Last 1 Day")


@pytest.mark.parametrize("status", [401, 403])
def test_graph_denying_access_means_the_mailbox_needs_reconnecting(status):
    provider = MicrosoftProvider(_creds())
    with patch(MS) as requests:
        requests.get.return_value = _response(status)
        with pytest.raises(ProviderAuthError):
            provider.list_message_ids("Last 1 Day")


def test_other_graph_errors_are_not_treated_as_auth_failures():
    provider = MicrosoftProvider(_creds())
    with patch(MS) as requests:
        requests.get.return_value = _response(500)
        with pytest.raises(RuntimeError, match="500"):
            provider.list_message_ids("Last 1 Day")


# --- OAuth flow ----------------------------------------------------------------------------------


def test_authorization_url_uses_pkce_and_offers_account_selection():
    url, state, verifier = microsoft_oauth.authorization_url()
    parsed = urlparse(url)
    query = {k: v[0] for k, v in parse_qs(parsed.query).items()}

    assert parsed.netloc == "login.microsoftonline.com" and parsed.path.endswith("/oauth2/v2.0/authorize")
    assert query["state"] == state and query["code_challenge_method"] == "S256"
    assert query["code_challenge"] == microsoft_oauth._challenge(verifier) != verifier
    assert query["prompt"] == "select_account"
    assert {"offline_access", "Mail.Read"} <= set(query["scope"].split())


def test_finish_exchanges_the_code_and_reads_the_address():
    with patch(MS_OAUTH) as requests:
        requests.post.return_value = _response(
            body={"access_token": "a", "refresh_token": "r", "expires_in": 3600}
        )
        requests.get.return_value = _response(body={"mail": None, "userPrincipalName": "Me@Outlook.com"})
        credentials, address = microsoft_oauth.finish("CODE", "STATE", "VERIFIER")

    assert address == "me@outlook.com"
    assert json.loads(credentials)["refresh_token"] == "r"
    sent = requests.post.call_args.kwargs["data"]
    assert sent["code"] == "CODE" and sent["code_verifier"] == "VERIFIER"
    assert "client_secret" in sent and "client_secret" not in credentials  # never stored


def test_finish_requires_a_refresh_token():
    with patch(MS_OAUTH) as requests:
        requests.post.return_value = _response(body={"access_token": "a", "expires_in": 3600})
        with pytest.raises(RuntimeError, match="refresh token"):
            microsoft_oauth.finish("C", "S", "V")


def test_account_helper_still_creates_google_accounts(db, user):
    assert make_account(db, user).provider == "google"
