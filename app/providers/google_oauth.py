"""Web OAuth flow for connecting a Gmail mailbox (redirect to Google and back)."""

import os

from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from ..core.config import GMAIL_SCOPES, get_settings
from ..core.redirects import GOOGLE_PATH, resolve

# Google may return a different scope ordering/superset than requested; don't fail on that.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")


def _build_flow(state: str | None = None, code_verifier: str | None = None) -> Flow:
    settings = get_settings()
    client_id, client_secret = settings.google_client()
    redirect_uri = resolve(settings.google_redirect_uri, GOOGLE_PATH)

    # oauthlib refuses plain http except for local development.
    if redirect_uri.startswith(("http://localhost", "http://127.0.0.1")):
        os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

    config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }
    flow = Flow.from_client_config(
        config,
        scopes=GMAIL_SCOPES,
        redirect_uri=redirect_uri,
        state=state,
        autogenerate_code_verifier=code_verifier is None,
    )
    if code_verifier is not None:
        flow.code_verifier = code_verifier
    return flow


def authorization_url() -> tuple[str, str, str]:
    """Returns (url to send the user to, state, PKCE code verifier). Keep state+verifier in the session."""
    flow = _build_flow()
    url, state = flow.authorization_url(
        access_type="offline", prompt="select_account consent"
    )  # pick which Gmail to add
    return url, state, flow.code_verifier


def finish(code: str, state: str, code_verifier: str) -> tuple[str, str]:
    """Exchanges the callback code. Returns (credentials JSON, the mailbox's email address)."""
    flow = _build_flow(state=state, code_verifier=code_verifier)
    flow.fetch_token(code=code)
    creds = flow.credentials
    if not creds.refresh_token:
        raise RuntimeError("Google did not return a refresh token; remove the app's access and retry.")

    profile = (
        build("gmail", "v1", credentials=creds, cache_discovery=False)
        .users()
        .getProfile(userId="me")
        .execute()
    )
    return creds.to_json(), profile["emailAddress"].lower()


def revoke(credentials_json: str) -> None:
    """Best-effort: tells Google to invalidate the tokens when a mailbox is disconnected."""
    import json

    import requests

    token = json.loads(credentials_json).get("refresh_token") or json.loads(credentials_json).get("token")
    if token:
        requests.post("https://oauth2.googleapis.com/revoke", params={"token": token}, timeout=5)
