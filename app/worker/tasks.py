import logging

from ..core.config import DEFAULT_TIMEFRAME
from ..db.models import MailAccount
from ..db.session import SessionLocal
from ..repositories import accounts as accounts_repo
from ..services.sync_service import sync_account
from .celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.worker.tasks.sync_inbox")
def sync_inbox(timeframe: str = DEFAULT_TIMEFRAME) -> int:
    """Periodic sync (scheduled by Celery beat) of every active mailbox.

    One failing mailbox never stops the others. Returns the number of new emails stored.
    """
    with SessionLocal() as db:
        account_ids = accounts_repo.list_all_active_ids(db)

    total = 0
    for account_id in account_ids:
        try:
            with SessionLocal() as db:
                account = db.get(MailAccount, account_id)
                if account is not None and account.status == accounts_repo.ACTIVE:
                    total += sync_account(db, account, timeframe)
        except Exception:
            logger.exception("Sync failed for account %s", account_id)
    return total
