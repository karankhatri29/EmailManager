from datetime import datetime, timezone

from ..core.config import SUMMARIZED_CATEGORIES
from . import rules as rules_service
from .analysis import EMPTY, NLP_VERSION, analyse
from .nlp_engine import category_score, classify
from .senders import sender_address
from .textify import normalize_body


def classify_email(subject, body, address, rules=()):
    """(score, category, reason, source): the user's rules decide first, then the NLP classifier."""
    matched = rules_service.match(rules, address, subject, body)
    if matched:
        return category_score(matched.category), matched.category, rules_service.describe(matched), "rule"
    result = classify(subject, body)
    return result.score, result.category, result.reason, "auto"


def process_emails(raw_emails, rules=(), date_order="DMY"):
    """Scores and categorises raw emails (fast, local NLP only), applying the user's rules first.

    Returns dicts with the fields the Email model stores. AI summaries are filled in
    afterwards by sync_service.summarize_pending, so emails can be shown immediately.
    """
    processed = []

    for email in raw_emails:
        address = sender_address(email["sender"])
        body = normalize_body(email["body"])  # HTML mail becomes readable text before anything reads it
        score, category, reason, source = classify_email(email["subject"], body, address, rules)
        sent = email.get("date") or datetime.now(timezone.utc)
        if category in SUMMARIZED_CATEGORIES:
            analysis = analyse(email["subject"], body, sent, date_order)
        else:
            analysis = {"task": None, "nlp_version": NLP_VERSION, **EMPTY}

        processed.append(
            {
                "id": email["id"],
                "sender": email["sender"],
                "sender_address": address,
                "subject": email["subject"],
                "body": body,
                "date": sent,
                "thread_id": email.get("thread_id"),
                "unsubscribe_url": email.get("unsubscribe_url"),
                "unsubscribe_one_click": bool(email.get("unsubscribe_one_click")),
                "score": score,
                "category": category,
                "reason": reason,
                "category_source": source,
                "summary": None,
                **analysis,
            }
        )

    return processed
