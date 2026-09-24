import base64
import json
from datetime import datetime, timezone

from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from ..core.config import GMAIL_SCOPES, TIMEFRAME_MESSAGE_LIMITS, TIMEFRAMES
from .base import ProviderAuthError
from .common import parse_list_unsubscribe

PAGE_SIZE = 100  # ids per list call (Gmail allows up to 500)
BATCH_SIZE = 25  # messages per batched HTTP request
MAX_BODY_CHARS = 4000
SENT_THREAD_LIMIT = 40
RECEIVED_ONLY = " -in:sent -in:drafts"  # the inbox view of mail: not your own sent mail or drafts


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


def _header(headers, name):
    return next((h["value"] for h in headers if h["name"].lower() == name), None)


def list_message_ids(service, time_filter, limit=None):
    """Ids of the messages in the timeframe, newest first, following pagination up to `limit`."""
    query = TIMEFRAMES.get(time_filter, ("", 0))[0] + RECEIVED_ONLY
    limit = limit or TIMEFRAME_MESSAGE_LIMITS.get(time_filter, PAGE_SIZE)

    ids: list[str] = []
    page_token = None
    while len(ids) < limit:
        params = {"userId": "me", "q": query, "maxResults": min(PAGE_SIZE, limit - len(ids))}
        if page_token:
            params["pageToken"] = page_token
        results = service.users().messages().list(**params).execute()
        ids.extend(m["id"] for m in results.get("messages", []))
        page_token = results.get("nextPageToken")
        if not page_token:
            break
    return ids


def _parse_message(msg_detail, message_id):
    """One Gmail API message resource as our dict: id, sender, subject, body, date, thread, unsubscribe."""
    payload = msg_detail.get("payload", {})
    headers = payload.get("headers", [])
    subject = _header(headers, "subject") or "No Subject"
    sender = _header(headers, "from") or "Unknown"

    body = ""
    if "parts" in payload:
        body = _extract_text_layer(payload["parts"])
    elif "data" in payload.get("body", {}):
        body = _safe_b64decode(payload["body"]["data"])

    if not body.strip():
        body = msg_detail.get("snippet", "")

    unsubscribe_url, one_click = parse_list_unsubscribe(
        _header(headers, "list-unsubscribe"), _header(headers, "list-unsubscribe-post")
    )
    return {
        "id": message_id,
        "sender": sender,
        "subject": subject,
        "body": body[:MAX_BODY_CHARS],
        "date": _parse_date(msg_detail),
        "thread_id": msg_detail.get("threadId"),
        "unsubscribe_url": unsubscribe_url,
        "unsubscribe_one_click": one_click,
    }


def fetch_message(service, message_id):
    """Downloads one message as a dict (see _parse_message)."""
    msg_detail = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    return _parse_message(msg_detail, message_id)


def fetch_messages(service, message_ids, batch_size=BATCH_SIZE):
    """Downloads many messages using batched HTTP requests (far fewer round trips than one by one).

    Messages that fail inside a batch (rate limits, transient errors) are retried one by one;
    authorisation errors are raised so the caller can flag the mailbox for reconnection.
    """
    message_ids = list(message_ids)
    results: dict[str, dict] = {}
    failures: dict[str, Exception] = {}

    def on_response(request_id, response, exception):
        if exception is not None:
            failures[request_id] = exception
        else:
            results[request_id] = _parse_message(response, request_id)

    for start in range(0, len(message_ids), batch_size):
        batch = service.new_batch_http_request()
        for message_id in message_ids[start : start + batch_size]:
            request = service.users().messages().get(userId="me", id=message_id, format="full")
            batch.add(request, callback=on_response, request_id=message_id)
        batch.execute()

    for message_id, exception in failures.items():
        if isinstance(exception, HttpError) and exception.resp is not None and exception.resp.status in (401, 403):
            raise exception
        results[message_id] = fetch_message(service, message_id)  # one retry, on its own

    return [results[m] for m in message_ids if m in results]


def list_sent_threads(service, days, limit=SENT_THREAD_LIMIT):
    """Conversations you replied in recently, and whether the last word is still yours.

    Reads message metadata only (headers, labels, dates), never message bodies.
    Each item: thread_id, subject, recipient, sent_at, awaiting (True if nobody has answered yet).
    """
    results = (
        service.users().messages().list(userId="me", q=f"in:sent newer_than:{days}d", maxResults=limit).execute()
    )
    thread_ids = list(dict.fromkeys(m["threadId"] for m in results.get("messages", []) if m.get("threadId")))

    threads = []
    for thread_id in thread_ids:
        thread = (
            service.users()
            .threads()
            .get(userId="me", id=thread_id, format="metadata", metadataHeaders=["From", "To", "Cc", "Subject"])
            .execute()
        )
        messages = sorted(thread.get("messages", []), key=lambda m: int(m.get("internalDate", 0)))
        if not messages:
            continue

        def mine(message):
            labels = set(message.get("labelIds", []))
            return "SENT" in labels and "DRAFT" not in labels

        last = messages[-1]
        last_sent = next((m for m in reversed(messages) if mine(m)), None)
        if last_sent is None:
            continue
        headers = last_sent.get("payload", {}).get("headers", [])
        threads.append(
            {
                "thread_id": thread_id,
                "subject": _header(headers, "subject") or "No Subject",
                "recipient": _header(headers, "to") or "",
                "sent_at": _parse_date(last_sent),
                "awaiting": mine(last),
            }
        )
    return threads


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

    def _call(self, fn, *args, **kwargs):
        try:
            return fn(self.service, *args, **kwargs)
        except RefreshError as exc:
            raise ProviderAuthError(f"Google rejected the saved login: {exc}") from exc
        except HttpError as exc:
            if exc.resp is not None and exc.resp.status in (401, 403):
                raise ProviderAuthError(f"Gmail access denied (HTTP {exc.resp.status})") from exc
            raise

    def list_message_ids(self, timeframe, limit=None):
        return self._call(list_message_ids, timeframe, limit)

    def fetch_message(self, message_id):
        return self._call(fetch_message, message_id)

    def fetch_messages(self, message_ids):
        return self._call(fetch_messages, message_ids)

    def list_sent_threads(self, days):
        return self._call(list_sent_threads, days)

    def export_credentials(self):
        current = self._creds.to_json()
        return (
            current if json.loads(current).get("token") != json.loads(self._original).get("token") else None
        )
