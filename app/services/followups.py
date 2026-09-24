"""'Waiting on a reply': mail you sent that nobody has answered, tracked per conversation."""

import logging
import re
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses

from sqlalchemy.orm import Session

from ..db.models import FollowUp, MailAccount
from ..repositories import followups as followups_repo
from ..repositories import settings as settings_repo

logger = logging.getLogger(__name__)

LOOKBACK_DAYS = 14
MAX_AGE_DAYS = 30  # stop tracking a conversation nobody answered for a month
# Nobody is going to reply to these, so waiting on them is noise.
AUTOMATED = re.compile(r"(no-?reply|do-?not-?reply|notifications?@|mailer-daemon|newsletter@)", re.IGNORECASE)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _worth_tracking(recipient: str, own_address: str) -> bool:
    """True if at least one recipient is a real person: not you, and not an automated address."""
    addresses = [a.strip().lower() for _, a in getaddresses([recipient]) if "@" in a]
    return any(a != own_address.lower() and not AUTOMATED.search(a) for a in addresses)


def sync_followups(db: Session, account: MailAccount, provider, now: datetime | None = None) -> int:
    """Refreshes the follow-ups of one mailbox. Returns how many new ones were found.

    Providers that cannot list sent conversations (no list_sent_threads) are skipped silently.
    """
    list_sent = getattr(provider, "list_sent_threads", None)
    if list_sent is None:
        return 0

    now = now or datetime.now(timezone.utc)
    followup_days = settings_repo.get_or_create(db, account.user_id).followup_days
    new = 0
    for thread in list_sent(LOOKBACK_DAYS):
        existing = followups_repo.get_by_thread(db, account.id, thread["thread_id"])
        sent_at = _aware(thread["sent_at"])

        if not thread["awaiting"]:
            if existing is not None and existing.status == followups_repo.WAITING:
                existing.status = followups_repo.REPLIED
            continue
        if now - sent_at > timedelta(days=MAX_AGE_DAYS) or not _worth_tracking(
            thread["recipient"], account.email_address
        ):
            continue

        if existing is None:
            db.add(
                FollowUp(
                    user_id=account.user_id,
                    account_id=account.id,
                    thread_id=thread["thread_id"],
                    subject=thread["subject"],
                    recipient=thread["recipient"][:512],
                    sent_at=sent_at,
                    nudge_at=sent_at + timedelta(days=followup_days),
                )
            )
            new += 1
        elif existing.status == followups_repo.REPLIED:
            # They answered, then you wrote again and it is unanswered once more: track it again.
            existing.status, existing.sent_at, existing.nudged_at = followups_repo.WAITING, sent_at, None
            existing.nudge_at = sent_at + timedelta(days=followup_days)
        elif existing.status == followups_repo.WAITING and sent_at > _aware(existing.sent_at):
            existing.sent_at = sent_at  # you nudged them again; the wait restarts
            existing.nudge_at, existing.nudged_at = sent_at + timedelta(days=followup_days), None
    db.commit()
    if new:
        logger.info("Found %d new conversations awaiting a reply in %s", new, account.email_address)
    return new


def waiting_days(followup: FollowUp, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    return max(0, (now - _aware(followup.sent_at)).days)
