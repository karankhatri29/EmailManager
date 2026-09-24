from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

from app.providers import google_oauth


def test_authorization_url_requests_offline_access_with_pkce():
    url, state, verifier = google_oauth.authorization_url()
    query = parse_qs(urlparse(url).query)

    assert url.startswith("https://accounts.google.com/")
    assert query["client_id"] == ["test-client-id"]
    assert query["access_type"] == ["offline"] and query["prompt"] == ["consent"]
    assert query["scope"] == ["https://www.googleapis.com/auth/gmail.readonly"]
    assert query["redirect_uri"] == ["http://localhost:8000/api/accounts/google/callback"]
    assert query["state"] == [state] and verifier
    assert query["code_challenge_method"] == ["S256"] and "code_challenge" in query


def test_each_authorization_gets_a_fresh_state_and_verifier():
    _, s1, v1 = google_oauth.authorization_url()
    _, s2, v2 = google_oauth.authorization_url()
    assert s1 != s2 and v1 != v2


def _fake_flow(refresh_token="rt"):
    creds = SimpleNamespace(refresh_token=refresh_token, to_json=lambda: '{"refresh_token": "rt"}')
    return SimpleNamespace(fetch_token=MagicMock(), credentials=creds)


def test_finish_returns_credentials_and_mailbox_address():
    flow = _fake_flow()
    with (
        patch.object(google_oauth, "_build_flow", return_value=flow) as build_flow,
        patch.object(google_oauth, "build") as build_service,
    ):
        build_service().users().getProfile().execute.return_value = {"emailAddress": "Me@Gmail.com"}
        credentials_json, address = google_oauth.finish("the-code", "the-state", "the-verifier")

    assert address == "me@gmail.com" and "rt" in credentials_json
    flow.fetch_token.assert_called_once_with(code="the-code")
    build_flow.assert_called_once_with(state="the-state", code_verifier="the-verifier")


def test_finish_requires_a_refresh_token():
    with (
        patch.object(google_oauth, "_build_flow", return_value=_fake_flow(refresh_token=None)),
        pytest.raises(RuntimeError, match="refresh token"),
    ):
        google_oauth.finish("c", "s", "v")


def test_revoke_posts_the_refresh_token():
    with patch("requests.post") as post:
        google_oauth.revoke('{"refresh_token": "rt", "token": "at"}')
    assert post.call_args.kwargs["params"] == {"token": "rt"}


def test_revoke_without_tokens_does_nothing():
    with patch("requests.post") as post:
        google_oauth.revoke("{}")
    post.assert_not_called()
