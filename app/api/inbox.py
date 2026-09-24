from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..core.config import DEFAULT_TIMEFRAME, TIMEFRAMES, Timeframe, get_settings
from ..db.models import User
from ..db.session import get_db
from ..repositories import accounts as accounts_repo
from ..repositories import emails as emails_repo
from ..schemas import EmailOut, SyncStatus, TaskOut
from ..services.background import SyncManager
from ..services.graph_scheduler import build_scheduler_graph
from ..services.sync_service import is_stale
from .deps import current_user, get_sync_manager

router = APIRouter(prefix="/api", tags=["inbox"])


def _check_account(db: Session, user: User, account_id: int | None) -> None:
    """A mailbox filter must name one of the user's own mailboxes."""
    if account_id is not None and accounts_repo.get_for_user(db, user.id, account_id) is None:
        raise HTTPException(status_code=404, detail="Mailbox not found")


@router.get("/emails", response_model=list[EmailOut])
def get_emails(
    time_filter: Timeframe = DEFAULT_TIMEFRAME,
    refresh: bool = False,
    account_id: int | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    manager: SyncManager = Depends(get_sync_manager),
):
    """The user's stored emails for the timeframe, returned immediately.

    Any connected mailbox whose data is stale (or all of them with refresh=true) is synced on a
    background thread; poll /api/sync/status and call this again when it finishes.
    Pass account_id to return only one mailbox's mail.
    """
    _check_account(db, user, account_id)
    max_age = get_settings().sync_interval_seconds
    for account in accounts_repo.list_active_for_user(db, user.id):
        if refresh or is_stale(db, account.id, time_filter, max_age):
            manager.trigger(account.id, time_filter)
    return emails_repo.list_in_window(db, user.id, TIMEFRAMES[time_filter][1], account_id)


@router.post("/sync", response_model=SyncStatus, status_code=202)
def start_sync(
    time_filter: Timeframe = DEFAULT_TIMEFRAME,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    manager: SyncManager = Depends(get_sync_manager),
):
    """Starts a background sync of all the user's mailboxes (mailboxes already syncing are skipped)."""
    accounts = accounts_repo.list_active_for_user(db, user.id)
    for account in accounts:
        manager.trigger(account.id, time_filter)
    return manager.status(a.id for a in accounts)


@router.get("/sync/status", response_model=SyncStatus)
def sync_status(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    manager: SyncManager = Depends(get_sync_manager),
):
    return manager.status(a.id for a in accounts_repo.list_for_user(db, user.id))


@router.get("/scheduler", response_model=list[TaskOut])
def get_graph_schedule(
    time_filter: Timeframe = DEFAULT_TIMEFRAME,
    account_id: int | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """The user's action items ordered by deadline tier (optionally for one mailbox)."""
    _check_account(db, user, account_id)
    rows = emails_repo.list_in_window(db, user.id, TIMEFRAMES[time_filter][1], account_id)
    open_rows = [
        row for row in rows if emails_repo.is_open(row)
    ]  # handled or snoozed mail is not an action item
    emails = [EmailOut.model_validate(row).model_dump() for row in open_rows]
    return build_scheduler_graph(emails)
