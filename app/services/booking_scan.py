"""Background scan of a mailbox for booking mail (flights, trains, movies, ...).

Runs on its own small thread pool, independent of the regular inbox sync: it searches up to 180 days back,
reads each hit in full, and stores it as a normal email (so it also shows in the inbox and reader) with the
complete text, which the booking parser needs. It does not create tasks or AI summaries for these mails.
"""

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone

from sqlalchemy import select, update

from ..core.config import get_settings
from ..db.models import Email, MailAccount
from ..db.session import SessionLocal
from ..providers import ProviderAuthError, get_provider
from ..providers.booking_search import FULL_TEXT_CHARS, fetch_full_text, search_booking_ids
from ..repositories import accounts as accounts_repo
from ..repositories import emails as emails_repo
from .email_processor import process_emails

logger = logging.getLogger(__name__)

SCAN_KEY = "bookings"  # stored in sync_state like a timeframe
SCAN_DAYS = 180
SCAN_LIMIT = 120
STALE_SECONDS = 6 * 3600
TRUNCATED_AT = 3900  # regular sync keeps 4000 characters; a stored body this long was probably cut short


def scan_account(db, account: MailAccount) -> int:
    """Finds and stores booking mail for one mailbox. Returns how many emails were added or completed."""
    provider = get_provider(account)
    try:
        found = search_booking_ids(provider, SCAN_DAYS, SCAN_LIMIT)
        stored_ids = {i: emails_repo.make_email_id(account.id, i) for i in found}
        # Lengths are measured on the decrypted text (in SQL they would be the ciphertext's length).
        rows = db.execute(select(Email.id, Email.body).where(Email.id.in_(list(stored_ids.values())))).all()
        lengths = {email_id: len(body or "") for email_id, body in rows}

        added = completed = 0
        fresh: list[dict] = []
        for provider_id, email_id in stored_ids.items():
            length = lengths.get(email_id)
            if length is not None and length < TRUNCATED_AT:
                continue  # already stored in full
            message = fetch_full_text(provider, provider_id, FULL_TEXT_CHARS)
            if length is None:
                message["id"] = email_id
                fresh.append(message)
            else:  # stored earlier, but cut off at 4000 characters: complete it
                db.execute(update(Email).where(Email.id == email_id).values(body=message["body"]))
                completed += 1
    except ProviderAuthError as exc:
        accounts_repo.mark_needs_reauth(db, account.id, str(exc))
        raise

    refreshed = provider.export_credentials()
    if refreshed:
        accounts_repo.update_credentials(db, account.id, refreshed)

    if fresh:
        rows = process_emails(fresh)
        for row in rows:
            row["user_id"] = account.user_id
            row["account_id"] = account.id
            row["summary"] = (
                ""  # not None: keeps the AI summariser (which fills NULLs) away from old ticket mails
            )
        emails_repo.upsert_many(db, rows)
        added = len(rows)
    db.commit()
    emails_repo.mark_synced(db, account.id, SCAN_KEY)
    logger.info(
        "Booking scan of %s: %d found, %d added, %d completed",
        account.email_address,
        len(found),
        added,
        completed,
    )
    return added + completed


def is_stale(db, account_id: int) -> bool:
    synced = emails_repo.get_synced_at(db, account_id, SCAN_KEY)
    return synced is None or (datetime.now(timezone.utc) - synced).total_seconds() > STALE_SECONDS


class BookingScanner:
    """One scan per mailbox at a time, a couple of mailboxes in parallel."""

    def __init__(self, max_workers: int = 2, inline: bool = False) -> None:
        """`inline=True` runs each scan in the calling thread (deterministic tests; never used in production)."""
        self._inline = inline
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="booking-scan")
        self._running: set[int] = set()
        self._errors: dict[int, str] = {}
        self._futures: list[Future] = []

    def trigger(self, account_id: int) -> bool:
        with self._lock:
            if account_id in self._running:
                return False
            self._running.add(account_id)
            self._errors.pop(account_id, None)
            if not self._inline:
                self._futures = [f for f in self._futures if not f.done()]
                self._futures.append(self._pool.submit(self._run, account_id))
        if self._inline:
            self._run(account_id)
        return True

    def _run(self, account_id: int) -> None:
        try:
            with SessionLocal() as db:
                account = db.get(MailAccount, account_id)
                if account is not None and account.status == accounts_repo.ACTIVE:
                    scan_account(db, account)
        except Exception as exc:
            logger.exception("Booking scan failed for account %s", account_id)
            with self._lock:
                self._errors[account_id] = f"{type(exc).__name__}: {exc}"[:200]
        finally:
            with self._lock:
                self._running.discard(account_id)

    def is_scanning(self, account_ids) -> bool:
        with self._lock:
            return any(i in self._running for i in account_ids)

    def error(self, account_ids) -> str | None:
        with self._lock:
            return next((self._errors[i] for i in account_ids if i in self._errors), None)

    def wait(self, timeout: float | None = None) -> None:
        """Blocks until running scans finish. Mainly for tests."""
        with self._lock:
            futures = list(self._futures)
        wait(futures, timeout=timeout)


scanner = BookingScanner(
    max_workers=max(1, get_settings().sync_max_workers // 2), inline=get_settings().is_serverless
)
