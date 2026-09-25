"""Searching a mailbox for booking mail, and reading such mails in full.

The normal sync only looks at the last day / week / month and keeps the first 4000 characters of each message.
Tickets are usually booked weeks before the trip, and their details (seats, PNR, show time) sit well down in
long HTML mails, so bookings get their own search and a full-text read.

A provider may implement `search_booking_ids(days, limit)` and `fetch_full_text(message_id)` itself; otherwise
the Gmail and Microsoft implementations below are used.
"""

from datetime import datetime, timedelta, timezone

from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

from ..services.bookings import SENDERS
from ..services.html_text import html_to_text, looks_like_html
from .base import ProviderAuthError
from .gmail import GmailProvider, _parse_date, _safe_b64decode, is_auth_failure
from .microsoft import MicrosoftProvider, _format_sender, _short_id
from .microsoft import _parse_date as _parse_graph_date

SUBJECT_TERMS = (
    "ticket",
    "e-ticket",
    "booking",
    "itinerary",
    "PNR",
    '"boarding pass"',
    "reservation",
    "confirmation",
    "confirmed",
)
FULL_TEXT_CHARS = 20000


def gmail_query(days: int) -> str:
    senders = " OR ".join(SENDERS)
    terms = " OR ".join(SUBJECT_TERMS)
    return f"newer_than:{days}d (subject:({terms}) OR from:({senders}))"


# --- Gmail ---------------------------------------------------------------------------------------------


def _gmail_guard(fn):
    try:
        return fn()
    except RefreshError as exc:
        raise ProviderAuthError(f"Google rejected the saved login: {exc}") from exc
    except HttpError as exc:
        if is_auth_failure(exc):
            raise ProviderAuthError(f"Gmail access denied (HTTP {exc.resp.status})") from exc
        raise


def _gmail_page(service, days: int, size: int, token: str | None) -> dict:
    return _gmail_guard(
        lambda: (
            service.users()
            .messages()
            .list(userId="me", q=gmail_query(days), maxResults=size, pageToken=token)
            .execute()
        )
    )


def _gmail_search(provider: GmailProvider, days: int, limit: int) -> list[str]:
    service = provider.service
    ids: list[str] = []
    token = None
    while len(ids) < limit:
        response = _gmail_page(service, days, min(100, limit - len(ids)), token)
        ids += [m["id"] for m in response.get("messages", [])]
        token = response.get("nextPageToken")
        if not token:
            break
    return ids[:limit]


def _collect(part: dict, plain: list[str], html: list[str]) -> None:
    body = part.get("body", {})
    mime = part.get("mimeType", "")
    if "data" in body:
        if mime == "text/plain":
            plain.append(_safe_b64decode(body["data"]))
        elif mime == "text/html":
            html.append(_safe_b64decode(body["data"]))
    for child in part.get("parts", []) or []:
        _collect(child, plain, html)


def _gmail_full(provider: GmailProvider, message_id: str, max_chars: int) -> dict:
    message = _gmail_guard(
        lambda: provider.service.users().messages().get(userId="me", id=message_id, format="full").execute()
    )
    payload = message.get("payload", {})
    headers = payload.get("headers", [])
    header = lambda name, default: next((h["value"] for h in headers if h["name"].lower() == name), default)  # noqa: E731

    plain: list[str] = []
    html: list[str] = []
    _collect(payload, plain, html)
    text = (
        "\n".join(p for p in plain if p.strip())
        or html_to_text("\n".join(html))
        or message.get("snippet", "")
    )
    if looks_like_html(text):
        text = html_to_text(text)
    return {
        "id": message_id,
        "sender": header("from", "Unknown"),
        "subject": header("subject", "No Subject"),
        "body": text[:max_chars],
        "date": _parse_date(message),
    }


# --- Microsoft Graph ---------------------------------------------------------------------------------------


def _graph_search(provider: MicrosoftProvider, days: int, limit: int) -> list[str]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    terms = " OR ".join(term.replace('"', "") for term in SUBJECT_TERMS)
    try:
        data = provider._get(
            "/me/messages",
            params={"$search": f'"{terms}"', "$top": min(limit, 250), "$select": "id,receivedDateTime"},
            headers={"ConsistencyLevel": "eventual"},
        )
    except ProviderAuthError:
        raise
    ids = []
    for message in data.get("value", []):
        if _parse_graph_date(message.get("receivedDateTime")) < since:
            continue
        short = _short_id(message["id"])
        provider._real_ids[short] = message["id"]
        ids.append(short)
    return ids[:limit]


def _graph_full(provider: MicrosoftProvider, message_id: str, max_chars: int) -> dict:
    graph_id = provider._real_ids.get(message_id)
    if graph_id is None:
        raise ValueError("Unknown message id; search_booking_ids must run first on this provider.")
    message = provider._get(
        f"/me/messages/{graph_id}",
        params={"$select": "subject,from,body,bodyPreview,receivedDateTime"},
        headers={"Prefer": 'outlook.body-content-type="text"'},
    )
    text = ((message.get("body") or {}).get("content") or "").strip() or message.get("bodyPreview", "")
    if looks_like_html(text):
        text = html_to_text(text)
    return {
        "id": message_id,
        "sender": _format_sender(message),
        "subject": message.get("subject") or "No Subject",
        "body": text[:max_chars],
        "date": _parse_graph_date(message.get("receivedDateTime")),
    }


# --- dispatch --------------------------------------------------------------------------------------------------


def search_booking_ids(provider, days: int = 180, limit: int = 120) -> list[str]:
    custom = getattr(provider, "search_booking_ids", None)
    if custom is not None:
        return custom(days, limit)
    if isinstance(provider, GmailProvider):
        return _gmail_search(provider, days, limit)
    if isinstance(provider, MicrosoftProvider):
        return _graph_search(provider, days, limit)
    return []


def fetch_full_text(provider, message_id: str, max_chars: int = FULL_TEXT_CHARS) -> dict:
    custom = getattr(provider, "fetch_full_text", None)
    if custom is not None:
        return custom(message_id)
    if isinstance(provider, GmailProvider):
        return _gmail_full(provider, message_id, max_chars)
    if isinstance(provider, MicrosoftProvider):
        return _graph_full(provider, message_id, max_chars)
    raise ValueError(f"Cannot read full messages for {type(provider).__name__}")
