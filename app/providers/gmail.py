import base64
import json
from datetime import datetime, timezone

from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from ..core.config import GMAIL_SCOPES, TIMEFRAMES
from .base import ProviderAuthError

MAX_RESULTS = 50
MAX_BODY_CHARS = 4000


# --- Gmail API helpers (operate on a googleapiclient service) -----------------------------------


def _safe_b64decode(raw_string):
    """Decodes Gmail's urlsafe base64, repairing missing padding."""
    if not raw_string:
        return ""
    try:
        missing_padding = len(raw_string) % 4
        if missing_padding:
            raw_string += "=" * (4 - missing_padding)
        return base64.urlsafe_b64decode(raw_string).decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _extract_text_layer(parts_list):
    """Prefers text/plain (searching nested parts first), falling back to text/html."""
    for part in parts_list:
        part_body = part.get("body", {})
        if part.get("mimeType") == "text/plain" and "data" in part_body:
            return _safe_b64decode(part_body["data"])
        if "parts" in part:
            nested_result = _extract_text_layer(part["parts"])
            if nested_result:
                return nested_result

    for part in parts_list:
        part_body = part.get("body", {})
        if part.get("mimeType") == "text/html" and "data" in part_body:
            return _safe_b64decode(part_body["data"])
    return ""


def _parse_date(msg_detail):
    """Gmail's internalDate (epoch millis) as an aware datetime; now() if absent."""
    internal_date = msg_detail.get("internalDate")
    if internal_date:
        return datetime.fromtimestamp(int(internal_date) / 1000, tz=timezone.utc)
    return datetime.now(timezone.utc)


def list_message_ids(service, time_filter):
    """Ids of the messages in the timeframe (one cheap API call, no message bodies)."""
    query = TIMEFRAMES.get(time_filter, ("", 0))[0]
    results = service.users().messages().list(userId="me", q=query, maxResults=MAX_RESULTS).execute()
    return [m["id"] for m in results.get("messages", [])]


def fetch_message(service, message_id):
    """Downloads one message as a dict: id, sender, subject, body, date."""
    msg_detail = service.users().messages().get(userId="me", id=message_id, format="full").execute()

    payload = msg_detail.get("payload", {})
    headers = payload.get("headers", [])
    subject = next((h["value"] for h in headers if h["name"].lower() == "subject"), "No Subject")
    sender = next((h["value"] for h in headers if h["name"].lower() == "from"), "Unknown")

    body = ""
    if "parts" in payload:
        body = _extract_text_layer(payload["parts"])
    elif "data" in payload.get("body", {}):
        body = _safe_b64decode(payload["body"]["data"])

    if not body.strip():
        body = msg_detail.get("snippet", "")

    return {
        "id": message_id,
        "sender": sender,
        "subject": subject,
        "body": body[:MAX_BODY_CHARS],
        "date": _parse_date(msg_detail),
    }


# --- Provider ------------------------------------------------------------------------------------


class GmailProvider:
    """MailProvider for a Gmail mailbox, built from the stored (decrypted) OAuth credentials."""

    def __init__(self, credentials_json: str) -> None:
        self._original = credentials_json
        self._creds = Credentials.from_authorized_user_info(json.loads(credentials_json), GMAIL_SCOPES)
        self._service = None

    @property
    def service(self):
        if self._service is None:
            self._service = build("gmail", "v1", credentials=self._creds, cache_discovery=False)
        return self._service

    def _call(self, fn, *args):
        try:
            return fn(self.service, *args)
        except RefreshError as exc:
            raise ProviderAuthError(f"Google rejected the saved login: {exc}") from exc
        except HttpError as exc:
            if exc.resp is not None and exc.resp.status in (401, 403):
                raise ProviderAuthError(f"Gmail access denied (HTTP {exc.resp.status})") from exc
            raise

    def list_message_ids(self, timeframe):
        return self._call(list_message_ids, timeframe)

    def fetch_message(self, message_id):
        return self._call(fetch_message, message_id)

    def export_credentials(self):
        current = self._creds.to_json()
        return (
            current if json.loads(current).get("token") != json.loads(self._original).get("token") else None
        )
