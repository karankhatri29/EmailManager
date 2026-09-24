"""Finds newsletters you never open and, when told to, unsubscribes from them and clears them away.

"Never open" means: over the last N months the sender mailed regularly and every message is still unread
(the mailbox's own read state, as of the last sync). For each one, cleaning up means
  1. unsubscribing automatically when the sender supports one-click (RFC 8058); otherwise the link is kept
     so you can finish it yourself,
  2. muting the sender here (a rule that files their mail under Promotional, easy to undo in Rules),
  3. archiving what is already here (marked done, so it leaves the inbox and the action list).
The mailbox itself is never modified: the app only has read access to it.
"""

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from ..db.models import User, UserSettings
from ..repositories import emails as emails_repo
from ..repositories import notifications as notifications_repo
from ..repositories import rules as rules_repo
from . import email_actions
from .nlp_engine import PROMOTIONAL
from .rules import match, to_specs
from .senders import sender_name
from .unsubscribe import unsubscribe_now

logger = logging.getLogger(__name__)

MIN_MESSAGES = 3
MAX_PER_RUN = 25  # unsubscribe requests sent in one run


def suggestions(db: Session, user_id: int, months: int) -> list[dict]:
    """Unopened bulk senders that are not muted yet, busiest first."""
    specs = [s for s in to_specs(rules_repo.list_for_user(db, user_id)) if s.kind in ("sender", "domain")]
    found = []
    for group in emails_repo.unopened_bulk_groups(db, user_id, months, MIN_MESSAGES):
        rule = match(specs, group["sender_address"], "", "")
        if rule is not None and rule.category == PROMOTIONAL:
            continue
        found.append(group)
    return found


def clean_up(db: Session, user_id: int, groups: list[dict]) -> list[dict]:
    """Unsubscribes (when one-click), mutes and archives each group. One result row per sender."""
    results = []
    for group in groups[:MAX_PER_RUN]:
        address = group["sender_address"]
        method, done, detail = unsubscribe_now(group["unsubscribe_url"], group["one_click"])
        rules_repo.upsert(db, user_id, "sender", address, PROMOTIONAL)
        email_actions.reapply_rules(db, user_id, "sender", address)
        archived = emails_repo.mark_done_for_sender(db, user_id, address)
        results.append(
            {
                "sender_address": address,
                "sender": sender_name(group["sender"]),
                "messages": group["count"],
                "method": method,
                "unsubscribed": done,
                "archived": archived,
                "link": group["unsubscribe_url"] if method == "link" else None,
                "detail": detail,
            }
        )
    return results


def summary_text(results: list[dict]) -> str:
    lines = []
    for r in results:
        outcome = (
            "unsubscribed"
            if r["unsubscribed"]
            else "muted (open its unsubscribe link to finish)"
            if r["link"]
            else "muted"
        )
        lines.append(f"{r['sender']}: {r['messages']} unread in months, {outcome}, {r['archived']} archived")
    return "\n".join(lines)


def run_for_user(db: Session, user: User, settings: UserSettings, now: datetime | None = None) -> int:
    """The automatic run: cleans up every unopened newsletter and tells the user what it did."""
    groups = suggestions(db, user.id, settings.cleanup_months)
    if not groups:
        return 0
    results = clean_up(db, user.id, groups)
    n = len(results)
    notifications_repo.create(
        db,
        user.id,
        "cleanup",
        f"Cleaned up {n} newsletter{'s' if n != 1 else ''} you never open",
        summary_text(results) + "\nUndo any of it under Rules.",
    )
    return n
