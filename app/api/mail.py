"""The inbox: browse and search all mail, act on single emails, and clean up newsletters."""

import logging
from typing import Literal

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..core.config import TIMEFRAMES
from ..core.security import LoginRateLimiter
from ..db.models import Email, User
from ..db.session import get_db
from ..repositories import accounts as accounts_repo
from ..repositories import emails as emails_repo
from ..repositories import rules as rules_repo
from ..schemas import (
    EmailListItem,
    EmailOut,
    EmailUpdate,
    InboxPage,
    NewsletterOut,
    SearchHit,
    SearchPlanOut,
    SearchResponse,
    ThreadMessage,
    ThreadOut,
    UnsubscribeRequest,
    UnsubscribeResult,
)
from ..services import email_actions, search_service, threads
from ..services.nlp_engine import PROMOTIONAL
from ..services.rules import match, to_specs
from ..services.textify import normalize_body
from ..services.unsubscribe import UnsafeUrl, one_click_unsubscribe
from .deps import current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["mail"])

Category = Literal["Urgent / Action Required", "Important", "General", "Promotional"]
MAX_HISTORY_DAYS = max(days for _, days in TIMEFRAMES.values())

# Smart search and thread summaries call the AI, so each user gets a modest budget per minute.
# (Reuses the failure counter as a hit counter; in-memory, so per process.)
ai_limiter = LoginRateLimiter(max_failures=30, window_seconds=60)


def _spend_ai_budget(user: User) -> None:
    key = f"ai:{user.id}"
    if ai_limiter.is_blocked(key):
        raise HTTPException(status_code=429, detail="Too many requests, please wait a moment.")
    ai_limiter.record_failure(key)


def to_list_item(email: Email) -> EmailListItem:
    return EmailListItem(
        id=email.id,
        account_id=email.account_id,
        sender=email.sender,
        sender_address=email.sender_address,
        subject=email.subject,
        snippet=" ".join(normalize_body(email.body).split())[:160],
        date=email.date,
        score=email.score,
        category=email.category,
        category_source=email.category_source,
        reason=email.reason,
        is_done=email.is_done,
        snoozed_until=email.snoozed_until,
        has_summary=bool(email.summary),
        thread_id=email.thread_id,
        can_unsubscribe=bool(email.unsubscribe_url),
    )


def _own_email(db: Session, user: User, email_id: str) -> Email:
    email = emails_repo.get_for_user(db, user.id, email_id)
    if email is None:
        raise HTTPException(status_code=404, detail="Email not found")
    return email


@router.get("/inbox", response_model=InboxPage)
def browse_inbox(
    q: str | None = Query(default=None, max_length=200, description="Words that must all appear"),
    category: Category | None = None,
    account_id: int | None = None,
    sender_address: str | None = Query(default=None, max_length=320),
    state: Literal["open", "done", "snoozed", "all"] = "open",
    days: int | None = Query(default=None, ge=1, le=MAX_HISTORY_DAYS),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """All of the user's stored mail, newest first, with filters and keyword search. Served from the database."""
    if account_id is not None and accounts_repo.get_for_user(db, user.id, account_id) is None:
        raise HTTPException(status_code=404, detail="Mailbox not found")
    rows, total = emails_repo.search_inbox(
        db,
        user.id,
        q=q,
        category=category,
        account_id=account_id,
        sender_address=sender_address,
        state=state,
        days=days,
        limit=limit,
        offset=offset,
    )
    return InboxPage(total=total, limit=limit, offset=offset, items=[to_list_item(e) for e in rows])


@router.get("/emails/{email_id}", response_model=EmailOut)
def get_email(email_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _own_email(db, user, email_id)


@router.patch("/emails/{email_id}", response_model=EmailOut)
def update_email(
    email_id: str,
    payload: EmailUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Correct the category (optionally remembering it for the sender), mark done, or snooze."""
    email = _own_email(db, user, email_id)
    sent = payload.model_fields_set

    if "category" in sent and payload.category is not None:
        email_actions.set_category(db, email, payload.category, payload.apply_to_sender)
    if "is_done" in sent and payload.is_done is not None:
        email_actions.set_done(db, email, payload.is_done)
    if "snoozed_until" in sent:
        email_actions.snooze(db, email, payload.snoozed_until)

    db.refresh(email)
    return email


# --- newsletters ---------------------------------------------------------------------------------


def _is_muted(specs, address: str) -> bool:
    rule = match([s for s in specs if s.kind in ("sender", "domain")], address, "", "")
    return rule is not None and rule.category == PROMOTIONAL


@router.get("/newsletters", response_model=list[NewsletterOut])
def list_newsletters(
    days: int = Query(default=30, ge=1, le=MAX_HISTORY_DAYS),
    min_count: int = Query(default=2, ge=1, le=50),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Senders that keep mailing you (promotions or anything with an unsubscribe link), busiest first."""
    specs = to_specs(rules_repo.list_for_user(db, user.id))
    return [
        NewsletterOut(
            sender_address=g["sender_address"],
            sender=g["sender"],
            count=g["count"],
            last_date=g["last_date"],
            last_subject=g["last_subject"],
            account_id=g["account_id"],
            can_unsubscribe=bool(g["unsubscribe_url"]),
            one_click=g["one_click"],
            muted=_is_muted(specs, g["sender_address"]),
        )
        for g in emails_repo.newsletter_groups(db, user.id, days, min_count)
    ]


@router.post("/newsletters/unsubscribe", response_model=UnsubscribeResult)
def unsubscribe(
    payload: UnsubscribeRequest, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    """Unsubscribes from a sender: automatically when it supports one-click, else by handing back its link.

    With `mute` the sender is also muted here, so its mail stops cluttering the inbox either way.
    """
    address = payload.sender_address.strip().lower()
    group = next(
        (
            g
            for g in emails_repo.newsletter_groups(db, user.id, MAX_HISTORY_DAYS, 1)
            if g["sender_address"] == address
        ),
        None,
    )
    if group is None:
        raise HTTPException(status_code=404, detail="No mail from that sender")

    url = group["unsubscribe_url"]
    if not url:
        result = UnsubscribeResult(
            method="none", ok=False, detail="This sender did not include an unsubscribe link."
        )
    elif group["one_click"]:
        result = _try_one_click(url)
    else:
        result = UnsubscribeResult(
            method="link", url=url, ok=True, detail="Open the link to finish unsubscribing."
        )

    if payload.mute:
        rules_repo.upsert(db, user.id, "sender", address, PROMOTIONAL)
        email_actions.reapply_rules(db, user.id, "sender", address)
        result.muted = True
    return result


def _try_one_click(url: str) -> UnsubscribeResult:
    try:
        if one_click_unsubscribe(url):
            return UnsubscribeResult(method="one_click", ok=True, detail="Unsubscribed.")
        detail = "The sender did not accept the automatic request."
    except UnsafeUrl as exc:
        logger.warning("Refused unsafe unsubscribe link: %s", exc)
        return UnsubscribeResult(
            method="none", ok=False, detail=f"That link was not safe to open automatically ({exc})."
        )
    except requests.RequestException:
        logger.warning("One-click unsubscribe request failed", exc_info=True)
        detail = "The sender could not be reached."
    return UnsubscribeResult(
        method="link", url=url, ok=True, detail=f"{detail} Open the link to finish unsubscribing."
    )


# --- smart search --------------------------------------------------------------------------------


@router.get("/search", response_model=SearchResponse)
def smart_search(
    q: str = Query(min_length=2, max_length=search_service.MAX_QUERY_CHARS),
    account_id: int | None = None,
    limit: int = Query(default=20, ge=1, le=50),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Ask about your mail in plain words, e.g. the invoice for the blue couch I bought last summer."""
    if account_id is not None and accounts_repo.get_for_user(db, user.id, account_id) is None:
        raise HTTPException(status_code=404, detail="Mailbox not found")
    _spend_ai_budget(user)

    result = search_service.search(db, user.id, q, account_id=account_id, limit=limit)
    plan = result.plan
    return SearchResponse(
        plan=SearchPlanOut(
            keywords=plan.keywords,
            date_from=plan.date_from,
            date_to=plan.date_to,
            category=plan.category,
            sender=plan.sender,
            explanation=plan.explain(),
            used_ai=plan.used_ai,
        ),
        semantic=result.semantic,
        items=[SearchHit(**to_list_item(e).model_dump(), match=score) for e, score in result.hits],
    )


# --- conversations -------------------------------------------------------------------------------


def _thread_out(db: Session, user: User, email: Email) -> ThreadOut:
    messages = threads.messages_for(db, user.id, email)
    cached = threads.cached_summary(db, user.id, email)
    return ThreadOut(
        message_count=len(messages),
        messages=[
            ThreadMessage(
                id=m.id, sender=m.sender, date=m.date, snippet=" ".join(normalize_body(m.body).split())[:160]
            )
            for m in messages
        ],
        summary=cached.summary if cached else None,
        summary_current=bool(cached and cached.message_count == len(messages)),
    )


@router.get("/emails/{email_id}/thread", response_model=ThreadOut)
def get_thread(email_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The stored messages of this email's conversation, plus its summary if one was made."""
    return _thread_out(db, user, _own_email(db, user, email_id))


@router.post("/emails/{email_id}/thread/summary", response_model=ThreadOut)
def summarize_thread(email_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Summarises the whole conversation (decisions, open questions, next steps). Cached until it grows."""
    email = _own_email(db, user, email_id)
    _spend_ai_budget(user)
    try:
        threads.summarize(db, user.id, email)
    except threads.NothingToSummarise:
        raise HTTPException(status_code=400, detail="This conversation has only one message.") from None
    except Exception:
        logger.exception("Thread summary failed")
        raise HTTPException(
            status_code=502, detail="Could not summarise the conversation right now."
        ) from None
    return _thread_out(db, user, email)
