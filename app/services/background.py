import logging
import threading
from collections.abc import Iterable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone

from ..core.config import DEFAULT_TIMEFRAME, get_settings
from ..db.models import MailAccount
from ..db.session import SessionLocal
from ..repositories import accounts as accounts_repo
from . import jobs
from .sync_service import sync_account

logger = logging.getLogger(__name__)


def _idle_state() -> dict:
    return {
        "syncing": False,
        "timeframe": None,
        "started_at": None,
        "finished_at": None,
        "new_emails": None,
        "error": None,
    }


class SyncManager:
    """Syncs mailboxes on background threads: one sync per mailbox at a time, a few mailboxes in parallel."""

    def __init__(self, max_workers: int | None = None) -> None:
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers or get_settings().sync_max_workers, thread_name_prefix="mail-sync"
        )
        self._states: dict[int, dict] = {}
        self._futures: list[Future] = []

    def trigger(self, account_id: int, timeframe: str) -> bool:
        """Starts a background sync of a mailbox. Returns False if that mailbox is already syncing."""
        with self._lock:
            state = self._states.get(account_id)
            if state and state["syncing"]:
                return False
            self._states[account_id] = {
                **_idle_state(),
                "syncing": True,
                "timeframe": timeframe,
                "started_at": datetime.now(timezone.utc),
            }
            self._futures = [f for f in self._futures if not f.done()]
            self._futures.append(self._pool.submit(self._run, account_id, timeframe))
        return True

    def _run(self, account_id: int, timeframe: str) -> None:
        new_emails = None
        error = None
        try:
            with SessionLocal() as db:
                account = db.get(MailAccount, account_id)
                if account is not None and account.status == accounts_repo.ACTIVE:
                    new_emails = sync_account(db, account, timeframe)
        except Exception as exc:
            logger.exception("Background sync failed for account %s (%s)", account_id, timeframe)
            error = f"{type(exc).__name__}: {exc}"[:300]
        finally:
            with self._lock:
                self._states[account_id].update(
                    syncing=False, finished_at=datetime.now(timezone.utc), new_emails=new_emails, error=error
                )

    def is_syncing(self, account_id: int) -> bool:
        with self._lock:
            return bool(self._states.get(account_id, {}).get("syncing"))

    def status(self, account_ids: Iterable[int]) -> dict:
        """Combined status over the given mailboxes (a user's accounts)."""
        with self._lock:
            states = [self._states[i] for i in account_ids if i in self._states]
        if not states:
            return _idle_state()

        finished = [s["finished_at"] for s in states if s["finished_at"]]
        started = [s["started_at"] for s in states if s["started_at"]]
        counts = [s["new_emails"] for s in states if s["new_emails"] is not None]
        latest = max(states, key=lambda s: s["started_at"])
        return {
            "syncing": any(s["syncing"] for s in states),
            "timeframe": latest["timeframe"],
            "started_at": max(started) if started else None,
            "finished_at": max(finished) if finished else None,
            "new_emails": sum(counts) if counts else None,
            "error": next((s["error"] for s in states if s["error"]), None),
        }

    def wait(self, timeout: float | None = None) -> None:
        """Blocks until all running syncs finish. Mainly for tests."""
        with self._lock:
            futures = list(self._futures)
        wait(futures, timeout=timeout)


sync_manager = SyncManager()


def sync_all_accounts(timeframe: str = DEFAULT_TIMEFRAME) -> int:
    """Triggers a sync of every active mailbox. Returns how many were started."""
    with SessionLocal() as db:
        account_ids = accounts_repo.list_all_active_ids(db)
    return sum(sync_manager.trigger(account_id, timeframe) for account_id in account_ids)


def start_periodic_sync(interval_seconds: float, timeframe: str = DEFAULT_TIMEFRAME) -> threading.Event:
    """Syncs every mailbox now and then every `interval_seconds`. Set the returned event to stop."""
    stop = threading.Event()

    def loop() -> None:
        logger.info("Periodic mailbox sync started (every %ss)", interval_seconds)
        while True:
            try:
                sync_all_accounts(timeframe)
            except Exception:
                logger.exception("Periodic sync round failed")
            if stop.wait(interval_seconds):
                return

    threading.Thread(target=loop, daemon=True, name="mail-periodic-sync").start()
    return stop


def start_periodic_jobs(interval_seconds: float) -> threading.Event:
    """Runs the recurring jobs (briefings, reminders, follow-up nudges) every `interval_seconds`."""
    stop = threading.Event()

    def loop() -> None:
        logger.info("Periodic jobs started (every %ss)", interval_seconds)
        while not stop.wait(interval_seconds):
            jobs.run_periodic_jobs()

    threading.Thread(target=loop, daemon=True, name="periodic-jobs").start()
    return stop
