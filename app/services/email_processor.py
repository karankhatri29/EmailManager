from datetime import datetime, timezone

from ..core.config import SUMMARIZED_CATEGORIES
from .nlp_engine import calculate_priority


def process_emails(raw_emails):
    """Scores and categorises raw emails (fast, local NLP only).

    Returns dicts with the fields the Email model stores. AI summaries are filled in
    afterwards by sync_service.summarize_pending, so emails can be shown immediately.
    """
    processed = []

    for email in raw_emails:
        score, category = calculate_priority(email["subject"], email["body"])
        task = f"{email['subject'][:50]}..." if category in SUMMARIZED_CATEGORIES else None

        processed.append(
            {
                "id": email["id"],
                "sender": email["sender"],
                "subject": email["subject"],
                "body": email["body"],
                "date": email.get("date") or datetime.now(timezone.utc),
                "score": score,
                "category": category,
                "summary": None,
                "task": task,
            }
        )

    return processed
