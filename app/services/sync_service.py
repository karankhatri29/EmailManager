import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..db.models import MailAccount
from ..providers import ProviderAuthError, get_provider
from ..repositories import accounts as accounts_repo
from ..repositories import emails as emails_repo
from ..repositories import rules as rules_repo
from . import embeddings, followups
from .activities_service import create_activities_for_emails
from .ai_summarizer import summarize_email
from .analysis import order_for_user
from .email_processor import process_emails
from .notifier import notify_urgent
from .rules import to_specs

logger = logging.getLogger(__name__)


def sync_account(db: Session, account: MailAccount, timeframe: str) -> int:
    """Pulls one mailbox's timeframe into the database.

    1. Lists message ids and downloads only the ones not stored yet.
    2. Classifies them (fast, local), stores them and creates their calendar activities,
       so they are visible straight away.
    3. Generates any missing AI summaries concurrently, filling them in as they finish.

    Returns the number of newly stored emails. If the mailbox login no longer works the account
    is flagged for reconnection and ProviderAuthError is re-raised.
    """
    provider = get_provider(account)
    try:
        ids = provider.list_message_ids(timeframe)
        new_ids = _unseen(db, account, ids)
        raw = _download(provider, new_ids)
    except ProviderAuthError as exc:
        accounts_repo.mark_needs_reauth(db, account.id, str(exc))
        raise

    refreshed = provider.export_credentials()
    if refreshed:
        accounts_repo.update_credentials(db, account.id, refreshed)

    for message in raw:
        message["id"] = emails_repo.make_email_id(account.id, message["id"])
    processed = process_emails(
        raw, to_specs(rules_repo.list_for_user(db, account.user_id)), order_for_user(db, account.user_id)
    )
    for email in processed:
        email["user_id"] = account.user_id
        email["account_id"] = account.id

    emails_repo.upsert_many(db, processed)
    create_activities_for_emails(db, processed)
    notify_urgent(db, account.user_id, processed)
    emails_repo.mark_synced(db, account.id, timeframe)
    logger.info("Synced %s (%s): %d listed, %d new", account.email_address, timeframe, len(ids), len(raw))

    summarize_pending(db, account.id)
    embeddings.embed_pending(db, account.id)
    try:
        followups.sync_followups(db, account, provider)
    except Exception:  # a side feature must never fail the mail sync
        db.rollback()
        logger.warning("Follow-up tracking failed for %s", account.email_address, exc_info=True)
    return len(raw)


def _download(provider, message_ids: list[str]) -> list[dict]:
    """Downloads messages in batches when the provider supports it (Gmail), else one by one."""
    fetch_many = getattr(provider, "fetch_messages", None)
    if fetch_many is not None:
        return list(fetch_many(message_ids))
    return [provider.fetch_message(message_id) for message_id in message_ids]


def _unseen(db: Session, account: MailAccount, provider_ids: list[str]) -> list[str]:
    existing = emails_repo.get_existing_ids(
        db, (emails_repo.make_email_id(account.id, i) for i in provider_ids)
    )
    return [i for i in provider_ids if emails_repo.make_email_id(account.id, i) not in existing]


def summarize_pending(db: Session, account_id: int) -> int:
    """Summarises a mailbox's Urgent/Important emails that lack a summary, several at a time.

    Also retries emails whose earlier summary attempt failed. Returns how many succeeded.
    """
    pending = [(e.id, e.body) for e in emails_repo.list_pending_summaries(db, account_id)]
    if not pending:
        return 0

    workers = max(1, get_settings().summary_workers)
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        # Only the AI call runs in threads; database writes stay on this thread.
        for (email_id, _), summary in zip(
            pending, pool.map(lambda p: summarize_email(p[1]), pending), strict=True
        ):
            if summary:
                emails_repo.set_summary(db, email_id, summary)
                done += 1

    logger.info("Summarised %d of %d pending emails", done, len(pending))
    return done


def is_stale(db: Session, account_id: int, timeframe: str, max_age_seconds: int) -> bool:
    synced_at = emails_repo.get_synced_at(db, account_id, timeframe)
    if synced_at is None:
        return True
    return (datetime.now(timezone.utc) - synced_at).total_seconds() > max_age_seconds
