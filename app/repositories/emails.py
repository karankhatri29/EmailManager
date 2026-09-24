from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from ..core.config import ACTIONABLE_MAX_AGE_DAYS, SUMMARIZED_CATEGORIES
from ..db.models import Email, SyncState


def make_email_id(account_id: int, provider_message_id: str) -> str:
    """Message ids are only unique within a mailbox, so the stored id is prefixed with the account."""
    return f"{account_id}:{provider_message_id}"


def get_existing_ids(db: Session, ids: Iterable[str]) -> set[str]:
    ids = list(ids)
    if not ids:
        return set()
    return set(db.scalars(select(Email.id).where(Email.id.in_(ids))))


def upsert_many(db: Session, emails: Iterable[dict]) -> None:
    for email in emails:
        db.merge(Email(**email))
    db.commit()


def list_in_window(db: Session, user_id: int, days: int, account_id: int | None = None) -> list[Email]:
    """The user's emails newer than `days` days, most recent first (optionally from one mailbox)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    query = select(Email).where(Email.user_id == user_id, Email.date >= cutoff)
    if account_id is not None:
        query = query.where(Email.account_id == account_id)
    return list(db.scalars(query.order_by(Email.date.desc())))


def list_pending_summaries(db: Session, account_id: int) -> list[Email]:
    """Recent Urgent/Important emails of a mailbox that still have no AI summary."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=ACTIONABLE_MAX_AGE_DAYS)
    query = select(Email).where(
        Email.account_id == account_id,
        Email.category.in_(SUMMARIZED_CATEGORIES),
        Email.summary.is_(None),
        Email.date >= cutoff,
    )
    return list(db.scalars(query.order_by(Email.date.desc())))


def set_summary(db: Session, email_id: str, summary: str) -> None:
    db.execute(update(Email).where(Email.id == email_id).values(summary=summary))
    db.commit()


def get_synced_at(db: Session, account_id: int, timeframe: str) -> datetime | None:
    state = db.get(SyncState, (account_id, timeframe))
    if state is None:
        return None
    synced_at = state.synced_at
    return synced_at if synced_at.tzinfo else synced_at.replace(tzinfo=timezone.utc)


def mark_synced(db: Session, account_id: int, timeframe: str) -> None:
    db.merge(SyncState(account_id=account_id, timeframe=timeframe, synced_at=datetime.now(timezone.utc)))
    db.commit()


# --- inbox: lookups, search and states -----------------------------------------------------------

INBOX_STATES = ("open", "done", "snoozed", "all")
MAX_SEARCH_TERMS = 8


def get_for_user(db: Session, user_id: int, email_id: str) -> Email | None:
    return db.scalar(select(Email).where(Email.id == email_id, Email.user_id == user_id))


def _like(term: str) -> str:
    """A LIKE pattern matching the term anywhere, with the user's own % and _ taken literally."""
    esc = chr(92)
    escaped = term.replace(esc, esc * 2).replace("%", esc + "%").replace("_", esc + "_")
    return f"%{escaped}%"


def state_filter(state: str):
    """SQL condition for an inbox state. Snoozed mail reappears in 'open' once its time has passed."""
    now = datetime.now(timezone.utc)
    snoozed = Email.snoozed_until.is_not(None) & (Email.snoozed_until > now)
    if state == "open":
        return (Email.is_done.is_(False)) & ~snoozed
    if state == "done":
        return Email.is_done.is_(True)
    if state == "snoozed":
        return (Email.is_done.is_(False)) & snoozed
    return None  # 'all'


def is_open(email: Email, now: datetime | None = None) -> bool:
    """Not done, and not currently snoozed (the same rule as the 'open' inbox state)."""
    now = now or datetime.now(timezone.utc)
    if email.is_done:
        return False
    snoozed_until = email.snoozed_until
    if snoozed_until is None:
        return True
    if snoozed_until.tzinfo is None:
        snoozed_until = snoozed_until.replace(tzinfo=timezone.utc)
    return snoozed_until <= now


def search_inbox(
    db: Session,
    user_id: int,
    *,
    q: str | None = None,
    category: str | None = None,
    account_id: int | None = None,
    sender_address: str | None = None,
    state: str = "open",
    days: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Email], int]:
    """Filtered, keyword-searched, paginated listing of a user's mail (newest first) and the total count."""
    conditions = [Email.user_id == user_id]
    if category:
        conditions.append(Email.category == category)
    if account_id is not None:
        conditions.append(Email.account_id == account_id)
    if sender_address:
        conditions.append(Email.sender_address == sender_address.lower())
    if days is not None:
        conditions.append(Email.date >= datetime.now(timezone.utc) - timedelta(days=days))
    by_state = state_filter(state)
    if by_state is not None:
        conditions.append(by_state)
    for term in (q or "").split()[:MAX_SEARCH_TERMS]:
        pattern = _like(term)
        conditions.append(
            or_(
                Email.subject.ilike(pattern, escape="\\"),
                Email.sender.ilike(pattern, escape="\\"),
                Email.body.ilike(pattern, escape="\\"),
            )
        )

    total = db.scalar(select(func.count()).select_from(Email).where(*conditions)) or 0
    rows = db.scalars(select(Email).where(*conditions).order_by(Email.date.desc()).limit(limit).offset(offset))
    return list(rows), total


def list_matching_rule(db: Session, user_id: int, kind: str, pattern: str) -> list[Email]:
    """The user's emails a rule applies to (candidates only; callers re-check with the rule engine)."""
    query = select(Email).where(Email.user_id == user_id)
    if kind == "sender":
        query = query.where(Email.sender_address == pattern)
    elif kind == "domain":
        query = query.where(
            or_(
                Email.sender_address.like(f"%@{pattern}", escape="\\"),
                Email.sender_address.like(f"%.{pattern}", escape="\\"),
            )
        )
    else:
        like = _like(pattern)
        query = query.where(or_(Email.subject.ilike(like, escape="\\"), Email.body.ilike(like, escape="\\")))
    return list(db.scalars(query))


def thread_messages(db: Session, user_id: int, account_id: int, thread_id: str) -> list[Email]:
    """Stored messages of one conversation, oldest first."""
    query = select(Email).where(
        Email.user_id == user_id, Email.account_id == account_id, Email.thread_id == thread_id
    )
    return list(db.scalars(query.order_by(Email.date)))


def newsletter_groups(db: Session, user_id: int, days: int = 30, min_count: int = 2) -> list[dict]:
    """Senders of bulk mail (promotions, or anything with an unsubscribe link), busiest first."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    query = (
        select(Email)
        .where(
            Email.user_id == user_id,
            Email.date >= cutoff,
            Email.sender_address != "",
            or_(Email.category == "Promotional", Email.unsubscribe_url.is_not(None)),
        )
        .order_by(Email.date.desc())
    )
    groups: dict[str, dict] = {}
    for email in db.scalars(query):
        group = groups.setdefault(
            email.sender_address,
            {
                "sender_address": email.sender_address,
                "sender": email.sender,
                "count": 0,
                "last_date": email.date,
                "last_subject": email.subject,
                "account_id": email.account_id,
                "unsubscribe_url": None,
                "one_click": False,
                "email_ids": [],
            },
        )
        group["count"] += 1
        group["email_ids"].append(email.id)
        if group["unsubscribe_url"] is None and email.unsubscribe_url:  # newest link wins
            group["unsubscribe_url"] = email.unsubscribe_url
            group["one_click"] = email.unsubscribe_one_click
    return sorted((g for g in groups.values() if g["count"] >= min_count), key=lambda g: -g["count"])


# --- embeddings (semantic search) ----------------------------------------------------------------

EMBED_MAX_AGE_DAYS = 400


def list_pending_embeddings(db: Session, account_id: int, limit: int) -> list[Email]:
    """Newest emails of a mailbox that do not have a vector yet."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=EMBED_MAX_AGE_DAYS)
    query = select(Email).where(Email.account_id == account_id, Email.embedding.is_(None), Email.date >= cutoff)
    return list(db.scalars(query.order_by(Email.date.desc()).limit(limit)))


def set_embeddings(db: Session, vectors: dict[str, bytes]) -> None:
    for email_id, blob in vectors.items():
        db.execute(update(Email).where(Email.id == email_id).values(embedding=blob))
    db.commit()


def search_candidates(
    db: Session,
    user_id: int,
    *,
    account_id: int | None = None,
    category: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    sender: str | None = None,
    limit: int = 5000,
) -> list[Email]:
    """The user's mail (any state) a smart search should rank: newest first, capped."""
    conditions = [Email.user_id == user_id]
    if account_id is not None:
        conditions.append(Email.account_id == account_id)
    if category:
        conditions.append(Email.category == category)
    if date_from is not None:
        conditions.append(Email.date >= date_from)
    if date_to is not None:
        conditions.append(Email.date < date_to)
    if sender:
        like = _like(sender.lower())
        conditions.append(or_(Email.sender_address.like(like, escape=chr(92)), Email.sender.ilike(like, escape=chr(92))))
    return list(db.scalars(select(Email).where(*conditions).order_by(Email.date.desc()).limit(limit)))
