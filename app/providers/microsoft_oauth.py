"""Web OAuth flow for connecting an Outlook / Microsoft 365 mailbox (authorization code + PKCE)."""

import base64
import hashlib
import json
import secrets
import time
from urllib.parse import urlencode

import requests

from ..core.config import MICROSOFT_SCOPES, get_settings
from ..core.redirects import MICROSOFT_PATH, resolve

GRAPH_ME = "https://graph.microsoft.com/v1.0/me"
TIMEOUT = 15


def token_endpoint(name: str) -> str:
    return f"https://login.microsoftonline.com/{get_settings().microsoft_tenant}/oauth2/v2.0/{name}"


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def authorization_url() -> tuple[str, str, str]:
    """Returns (url to send the user to, state, PKCE code verifier). Keep state+verifier in the session."""
    settings = get_settings()
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)
    query = urlencode(
        {
            "client_id": settings.microsoft_client_id,
            "response_type": "code",
            "redirect_uri": resolve(settings.microsoft_redirect_uri, MICROSOFT_PATH),
            "response_mode": "query",
            "scope": " ".join(MICROSOFT_SCOPES),
            "state": state,
            "code_challenge": _challenge(verifier),
            "code_challenge_method": "S256",
            "prompt": "select_account",  # lets the user pick which of several Microsoft accounts to add
        }
    )
    return f"{token_endpoint('authorize')}?{query}", state, verifier


def credentials_from_token(token: dict, previous_refresh_token: str | None = None) -> str:
    """The JSON we store: tokens only (the client secret always comes from settings)."""
    return json.dumps(
        {
            "access_token": token["access_token"],
            "refresh_token": token.get("refresh_token") or previous_refresh_token,
            "expires_at": time.time() + int(token.get("expires_in", 3600)),
        }
    )


def finish(code: str, state: str, code_verifier: str) -> tuple[str, str]:
    """Exchanges the callback code. Returns (credentials JSON, the mailbox's email address)."""
    settings = get_settings()
    response = requests.post(
        token_endpoint("token"),
        data={
            "grant_type": "authorization_code",
            "client_id": settings.microsoft_client_id,
            "client_secret": settings.microsoft_client_secret,
            "code": code,
            "redirect_uri": resolve(settings.microsoft_redirect_uri, MICROSOFT_PATH),
            "code_verifier": code_verifier,
            "scope": " ".join(MICROSOFT_SCOPES),
        },
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    token = response.json()
    if not token.get("refresh_token"):
        raise RuntimeError("Microsoft did not return a refresh token; check the offline_access permission.")

    profile = requests.get(
        GRAPH_ME,
        params={"$select": "mail,userPrincipalName"},
        headers={"Authorization": f"Bearer {token['access_token']}"},
        timeout=TIMEOUT,
    )
    profile.raise_for_status()
    data = profile.json()
    address = data.get("mail") or data.get("userPrincipalName")
    if not address:
        raise RuntimeError("Microsoft did not report a mailbox address for this account.")
    return credentials_from_token(token), address.lower()
