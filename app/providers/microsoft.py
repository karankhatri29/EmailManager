"""MailProvider for Outlook / Microsoft 365 mailboxes, using the Microsoft Graph REST API."""

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone

import requests

from ..core.config import MICROSOFT_SCOPES, TIMEFRAME_MESSAGE_LIMITS, TIMEFRAMES, get_settings
from ..services.textify import normalize_body
from .base import ProviderAuthError
from .common import parse_list_unsubscribe
from .microsoft_oauth import credentials_from_token, token_endpoint

GRAPH = "https://graph.microsoft.com/v1.0"
PAGE_SIZE = 100
MAX_BODY_CHARS = 4000
TIMEOUT = 15
EXPIRY_MARGIN_SECONDS = 60


def _short_id(graph_id: str) -> str:
    """Graph message ids are ~150 characters, longer than the emails.id column allows once prefixed
    with the account, so the stored id is a stable hash of it."""
    return hashlib.sha1(graph_id.encode()).hexdigest()


def _parse_date(value: str | None) -> datetime:
    if value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _format_sender(message: dict) -> str:
    address = (message.get("from") or {}).get("emailAddress") or {}
    name, mail = address.get("name"), address.get("address")
    if name and mail and name != mail:
        return f"{name} <{mail}>"
    return mail or name or "Unknown"


class MicrosoftProvider:
    """Built from the stored (decrypted) OAuth credentials. Refreshes the access token when it expires."""

    def __init__(self, credentials_json: str) -> None:
        self._original = credentials_json
        self._creds = json.loads(credentials_json)
        self._real_ids: dict[str, str] = {}  # stored (hashed) id -> Graph id, filled by list_message_ids

    # --- auth ---

    def _refresh(self) -> None:
        settings = get_settings()
        response = requests.post(
            token_endpoint("token"),
            data={
                "grant_type": "refresh_token",
                "refresh_token": self._creds.get("refresh_token"),
                "client_id": settings.microsoft_client_id,
                "client_secret": settings.microsoft_client_secret,
                "scope": " ".join(MICROSOFT_SCOPES),
            },
            timeout=TIMEOUT,
        )
        if response.status_code in (400, 401):
            try:
                detail = response.json().get("error_description", "")
            except ValueError:
                detail = ""
            raise ProviderAuthError(f"Microsoft rejected the saved login: {detail or response.status_code}")
        response.raise_for_status()
        self._creds = json.loads(credentials_from_token(response.json(), self._creds.get("refresh_token")))

    def _access_token(self) -> str:
        if time.time() >= float(self._creds.get("expires_at", 0)) - EXPIRY_MARGIN_SECONDS:
            self._refresh()
        return self._creds["access_token"]

    def _get(self, path: str, params: dict | None = None, headers: dict | None = None) -> dict:
        response = requests.get(
            path if path.startswith("https://") else f"{GRAPH}{path}",
            params=params,
            headers={"Authorization": f"Bearer {self._access_token()}", **(headers or {})},
            timeout=TIMEOUT,
        )
        if response.status_code in (401, 403):
            raise ProviderAuthError(f"Outlook access denied (HTTP {response.status_code})")
        response.raise_for_status()
        return response.json()

    # --- MailProvider ---

    def list_message_ids(self, timeframe: str, limit: int | None = None) -> list[str]:
        days = TIMEFRAMES.get(timeframe, ("", 1))[1]
        limit = limit or TIMEFRAME_MESSAGE_LIMITS.get(timeframe, PAGE_SIZE)
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

        ids: list[str] = []
        url: str | None = "/me/mailFolders/inbox/messages"
        params: dict | None = {
            "$filter": f"receivedDateTime ge {since}",
            "$orderby": "receivedDateTime desc",
            "$select": "id",
            "$top": min(PAGE_SIZE, limit),
        }
        while url and len(ids) < limit:
            data = self._get(url, params=params)
            for message in data.get("value", []):
                short = _short_id(message["id"])
                self._real_ids[short] = message["id"]
                ids.append(short)
            url, params = data.get("@odata.nextLink"), None  # the next link already carries its own query
        return ids[:limit]

    def fetch_message(self, message_id: str) -> dict:
        graph_id = self._real_ids.get(message_id)
        if graph_id is None:
            raise ValueError("Unknown message id; list_message_ids must run first on this provider.")

        message = self._get(
            f"/me/messages/{graph_id}",
            params={
                "$select": "subject,from,body,bodyPreview,receivedDateTime,conversationId,internetMessageHeaders,isRead"
            },
            headers={"Prefer": 'outlook.body-content-type="text"'},
        )
        body = ((message.get("body") or {}).get("content") or "").strip() or message.get("bodyPreview", "")
        result = {
            "id": message_id,
            "sender": _format_sender(message),
            "subject": message.get("subject") or "No Subject",
            "body": normalize_body(body, MAX_BODY_CHARS),
            "date": _parse_date(message.get("receivedDateTime")),
        }
        if "isRead" in message:
            result["is_unread"] = not message["isRead"]
        if message.get("conversationId"):
            result["thread_id"] = message["conversationId"]

        headers = {
            h.get("name", "").lower(): h.get("value", "") for h in message.get("internetMessageHeaders") or []
        }
        url, one_click = parse_list_unsubscribe(
            headers.get("list-unsubscribe"), headers.get("list-unsubscribe-post")
        )
        if url:
            result["unsubscribe_url"] = url
            result["unsubscribe_one_click"] = one_click
        return result

    def export_credentials(self) -> str | None:
        """Updated credentials (plaintext JSON) if the access token was refreshed while in use."""
        if self._creds.get("access_token") == json.loads(self._original).get("access_token"):
            return None
        return json.dumps(self._creds)
