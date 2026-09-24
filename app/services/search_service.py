"""Smart search: understands a question like 'the invoice for the blue couch I bought last summer'.

1. The question is turned into a plan (keywords, a date range, maybe a sender or category) by the AI.
2. Mail that fits the plan is ranked by keyword hits blended with meaning (embedding similarity),
   so an email about a "sofa" can still answer a question about a "couch".
Without an AI key or connection it degrades to plain keyword search.
"""

import logging
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone

import numpy as np
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db.models import Email
from ..repositories import emails as emails_repo
from . import ai_summarizer, embeddings
from .nlp_engine import CATEGORIES

logger = logging.getLogger(__name__)

MAX_QUERY_CHARS = 300
SEMANTIC_WEIGHT = 0.65
KEYWORD_WEIGHT = 0.35
MIN_SEMANTIC_SIMILARITY = 0.6  # below this, a mail with no keyword hit is not considered a match
RELATIVE_CUTOFF = 0.75  # embedding scores sit in a narrow band, so also drop hits far below the best one
FIELD_WEIGHTS = {"subject": 3, "sender": 2, "body": 1}
STOPWORDS = frozenset(
    "the a an and or of to for from in on at by with about that this those these my me i we you it is are was were be "
    "find show get all any mail email emails message messages please where what which when last".split()
)


class _PlanSchema(BaseModel):
    keywords: list[str] = []
    date_from: str | None = None
    date_to: str | None = None
    category: str | None = None
    sender: str | None = None


PLAN_PROMPT = """You turn a question about someone's email into a search plan. Today is {today}.
Return JSON with:
- keywords: the distinctive words to look for (nouns, product or company names; no filler words), lower case
- date_from / date_to: an inclusive YYYY-MM-DD range if the question implies a time period
  ("last summer" = June 1 to August 31 of the most recent summer that has already started, "in March" = that month,
  "last week", "yesterday"), otherwise null
- category: one of {categories}, but ONLY if the question explicitly asks for that kind of mail
  (e.g. "urgent emails", "promotions", "newsletters" -> Promotional). Invoices, flights, bookings and receipts are
  NOT a category: leave it null unless one of those words is clearly used.
- sender: a person or company name (or address) the mail is from, if mentioned, otherwise null

Question: {question}"""


@dataclass
class QueryPlan:
    keywords: list[str]
    date_from: date | None = None
    date_to: date | None = None  # inclusive
    category: str | None = None
    sender: str | None = None
    used_ai: bool = False

    def explain(self) -> str:
        parts = []
        if self.keywords:
            parts.append("about " + ", ".join(self.keywords))
        if self.sender:
            parts.append(f"from {self.sender}")
        if self.category:
            parts.append(f"marked {self.category}")
        if self.date_from or self.date_to:
            start = self.date_from.isoformat() if self.date_from else "the start"
            end = self.date_to.isoformat() if self.date_to else "today"
            parts.append(f"between {start} and {end}")
        return "Looking for mail " + (" ".join(parts) if parts else "matching your words")


@dataclass
class SearchResult:
    plan: QueryPlan
    semantic: bool  # whether meaning-based ranking was used
    hits: list[tuple[Email, float]] = field(default_factory=list)


def _parse_day(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def fallback_plan(query: str) -> QueryPlan:
    """No AI: every meaningful word in the question is a keyword."""
    words = [w for w in re.findall(r"[\w'-]+", query.lower()) if len(w) > 2 and w not in STOPWORDS]
    return QueryPlan(keywords=list(dict.fromkeys(words)))


def make_plan(query: str, today: date) -> QueryPlan:
    try:
        raw = ai_summarizer.generate_json(
            PLAN_PROMPT.format(today=today.isoformat(), categories=", ".join(CATEGORIES), question=query),
            _PlanSchema,
        )
        plan = _PlanSchema.model_validate(raw)
    except Exception:
        logger.warning("Query understanding failed; using keyword search", exc_info=True)
        return fallback_plan(query)

    keywords = [k.strip().lower() for k in plan.keywords if k and k.strip()]
    return QueryPlan(
        keywords=keywords or fallback_plan(query).keywords,
        date_from=_parse_day(plan.date_from),
        date_to=_parse_day(plan.date_to),
        category=plan.category if plan.category in CATEGORIES else None,
        sender=(plan.sender or "").strip() or None,
        used_ai=True,
    )


def keyword_score(email: Email, keywords: list[str]) -> float:
    """0..1: how many of the keywords appear, weighted towards the subject and sender."""
    if not keywords:
        return 0.0
    fields = {"subject": email.subject.lower(), "sender": email.sender.lower(), "body": email.body.lower()}
    total = 0
    for keyword in keywords:
        total += max((w for name, w in FIELD_WEIGHTS.items() if keyword in fields[name]), default=0)
    return total / (max(FIELD_WEIGHTS.values()) * len(keywords))


def _bounds(plan: QueryPlan) -> tuple[datetime | None, datetime | None]:
    start = datetime.combine(plan.date_from, time.min, tzinfo=timezone.utc) if plan.date_from else None
    end = datetime.combine(plan.date_to + timedelta(days=1), time.min, tzinfo=timezone.utc) if plan.date_to else None
    return start, end


def search(
    db: Session,
    user_id: int,
    query: str,
    *,
    account_id: int | None = None,
    limit: int = 20,
    today: date | None = None,
) -> SearchResult:
    query = query.strip()[:MAX_QUERY_CHARS]
    plan = make_plan(query, today or datetime.now(timezone.utc).date())
    query_vector = embeddings.embed_query(query)
    hits, semantic = _rank(db, user_id, plan, query_vector, account_id, limit)
    if not hits and plan.category:
        # The model may have guessed a category the mail was never given: try again without it.
        plan = replace(plan, category=None)
        hits, semantic = _rank(db, user_id, plan, query_vector, account_id, limit)
    return SearchResult(plan, semantic=semantic, hits=hits)


def _rank(
    db: Session,
    user_id: int,
    plan: QueryPlan,
    query_vector: np.ndarray | None,
    account_id: int | None,
    limit: int,
) -> tuple[list[tuple[Email, float]], bool]:
    """Ranks the mail that fits the plan. Returns (hits, whether meaning-based ranking was used)."""
    date_from, date_to = _bounds(plan)
    candidates = emails_repo.search_candidates(
        db,
        user_id,
        account_id=account_id,
        category=plan.category,
        date_from=date_from,
        date_to=date_to,
        sender=plan.sender,
    )
    if not candidates:
        return [], False

    similarity: dict[str, float] = {}
    if query_vector is not None:
        with_vectors = [e for e in candidates if e.embedding]
        if with_vectors:
            matrix = np.stack([embeddings.unpack(e.embedding) for e in with_vectors])
            similarity = {e.id: float(s) for e, s in zip(with_vectors, matrix @ query_vector, strict=True)}

    hits = []
    for email in candidates:
        kw = keyword_score(email, plan.keywords)
        sem = similarity.get(email.id)
        if kw == 0 and (sem is None or sem < MIN_SEMANTIC_SIMILARITY):
            continue
        score = KEYWORD_WEIGHT * kw + (SEMANTIC_WEIGHT * max(sem, 0.0) if sem is not None else 0.0)
        hits.append((email, round(score, 4)))

    hits.sort(key=lambda pair: -pair[1])  # stable, so equal scores stay newest first
    if hits:
        floor = hits[0][1] * RELATIVE_CUTOFF
        hits = [h for h in hits if h[1] >= floor]
    return hits[:limit], bool(similarity)
