import re
from dataclasses import dataclass

import spacy

# Initialize the spaCy core model
try:
    nlp = spacy.load("en_core_web_sm")
except OSError:
    import os

    os.system("python -m spacy download en_core_web_sm")
    nlp = spacy.load("en_core_web_sm")


# =============================================================
# CORE NLP PROCESSING PIPELINE
# =============================================================
def process_text(raw_text):
    """
    Advanced NLP Pipeline: Cleans text, extracts proper nouns, named entities (NER),
    and grammatical markers with case-preservation optimizations.
    """
    if not raw_text:
        return {"tokens": [], "entities": {}, "clean_text_raw": ""}

    # Pre-cleaning: Strip heavy web boilerplate, URLs, and duplicate spaces to speed up spaCy
    cleaned_text = re.sub(r"https?://\S+|www\.\S+", "", raw_text)
    cleaned_text = re.sub(r"\s+", " ", cleaned_text).strip()

    doc = nlp(cleaned_text)

    entities: dict[str, list[str]] = {"MONEY": [], "ORG": [], "DATE": []}
    for ent in doc.ents:
        if ent.label_ in entities:
            entities[ent.label_].append(ent.text.strip())

    processed_tokens = []
    for token in doc:
        if not token.is_stop and not token.is_punct and token.text.strip():
            processed_tokens.append(
                {
                    "original": token.text,
                    "lemma": token.lemma_.lower().strip(),
                    "pos": token.pos_,
                    "dep": token.dep_,
                }
            )

    return {"tokens": processed_tokens, "entities": entities, "clean_text_raw": cleaned_text.lower()}


# =============================================================
# SEMANTIC PRIORITY CLASSIFIER (With Informational Guards)
# =============================================================
URGENT = "Urgent / Action Required"
IMPORTANT = "Important"
GENERAL = "General"
PROMOTIONAL = "Promotional"
CATEGORIES = [URGENT, IMPORTANT, GENERAL, PROMOTIONAL]

# Score band of each category; more supporting evidence puts an email higher inside its band.
SCORE_RANGES = {URGENT: (4.0, 4.8), IMPORTANT: (3.1, 3.5), GENERAL: (2.0, 2.5), PROMOTIONAL: (1.0, 1.4)}


@dataclass(frozen=True)
class Classification:
    score: float
    category: str
    reason: str  # a plain-English "why", shown to the user


def category_score(category):
    """A representative score for a category (used when a rule or the user decides the category)."""
    low, high = SCORE_RANGES[category]
    return round((low + high) / 2, 2)


def _scaled(low, high, evidence):
    """Deterministic score in [low, high]: 0 pieces of evidence -> low, 3 or more -> high."""
    return round(low + (high - low) * min(evidence, 3) / 3, 2)


def _matches(text, phrases):
    """The phrases that occur in the text as whole words (allowing a plural / -ed ending)."""
    return [p for p in phrases if re.search(rf"\b{re.escape(p)}(?:s|es|d|ed)?\b", text)]


def _quote(items):
    return ", ".join(f"“{i}”" for i in items[:3])


PROMO_TRIGGERS = [
    "off",
    "discount",
    "sale",
    "subscribe",
    "unsubscribe",
    "cashback offer",
    "pre-approved",
    "bonus points",
    "gift card",
    "newsletter",
    "shop now",
    "advertisement",
]
SECURITY_TRIGGERS = [
    "otp",
    "verification code",
    "2fa",
    "login detected",
    "unauthorized access",
    "password reset",
    "security alert",
    "suspicious sign-in",
    "action required to secure",
]
FINANCIAL_VERBS = ["debited", "credited", "transferred", "charged", "withdrawn", "processed successfully"]
INFORMATIONAL_GUARDS = [
    "no action required",
    "action is not required",
    "no action is required",
    "please do not reply",
    "do not reply to this",
    "automated notification",
    "informational purposes only",
    "requires no action",
    "requires no response",
    "weekly digest",
    "newsletter",
    "monthly update",
    "read below for updates",
    "news summary",
    "daily brief",
    "summary of achievements",
    "successfully updated",
]
_NO_DEADLINE = re.compile(r"\b(?:no|without|not? any|nor) (?:\w+ ){0,2}(?:deadline|due date)\b")
DEADLINE_MARKERS = ["deadline", "due date", "by end of day", "final call to submit"]
TASK_LEMMAS = ["submit", "complete", "review", "verify", "reply", "attend", "fill", "upload", "pay"]
URGENCY_WORDS = ["please", "kindly", "must", "required", "urgently"]
NOTICE_MARKERS = [
    "announcement",
    "notice",
    "update regarding",
    "update",
    "schedule",
    "meeting minutes",
    "status report",
    "digest",
]


def classify(subject, body):
    """
    Granular classifier. Separates security alerts, real transaction receipts and true
    assignments from generic noise or newsletters, and says why in plain English.
    """
    nlp_data = process_text(f"{subject} {body}")

    text_raw = nlp_data["clean_text_raw"]
    tokens = nlp_data["tokens"]
    entities = nlp_data["entities"]

    # Rule 1: promotional / ads / marketing
    promo = _matches(text_raw, PROMO_TRIGGERS)
    if promo:
        return Classification(
            _scaled(*SCORE_RANGES[PROMOTIONAL], len(promo) - 1),
            PROMOTIONAL,
            f"Looks promotional: mentions {_quote(promo)}.",
        )

    # Rule 2: security and authentication
    security = _matches(text_raw, SECURITY_TRIGGERS)
    if security:
        return Classification(
            _scaled(4.5, 4.8, len(security)),
            URGENT,
            f"Security or sign-in message: mentions {_quote(security)}.",
        )

    # Rule 3: banking and real transaction receipts
    verbs = _matches(text_raw, FINANCIAL_VERBS)
    has_real_currency = len(entities["MONEY"]) > 0
    money_context = [w for w in ("bank", "statement", "invoice") if w in text_raw]
    if verbs or (money_context and has_real_currency):
        if verbs:
            reason = f"Reports money movement: mentions {_quote(verbs)}."
        else:
            reason = f"A {money_context[0]} message that includes an amount of money."
        return Classification(_scaled(4.2, 4.4, len(verbs) + int(has_real_currency)), URGENT, reason)

    # Rule 4: imperative obligations (with informational guards)
    informational = _matches(text_raw, INFORMATIONAL_GUARDS)
    deadline_markers = [] if _NO_DEADLINE.search(text_raw) else _matches(text_raw, DEADLINE_MARKERS)

    evidence = []
    if not informational:
        if "action required" in text_raw and not any(neg in text_raw for neg in ["no action", "not require"]):
            evidence.append("says “action required”")
        for t in tokens:
            if t["pos"] == "VERB" and t["dep"] in ["ROOT", "xcomp"] and t["lemma"] in TASK_LEMMAS:
                urgency = _matches(text_raw, URGENCY_WORDS)
                if urgency:
                    evidence.append(f"asks you to {t['lemma']} something ({_quote(urgency)})")
                    break
        if deadline_markers:
            evidence.append(f"mentions a deadline ({_quote(deadline_markers)})")

    if evidence:
        return Classification(
            _scaled(4.0, 4.1, len(evidence)), URGENT, "Needs action: " + "; ".join(evidence) + "."
        )

    # Rule 4b: a plain request to the reader ("please log in to confirm your participation")
    if not informational:
        from .actions import find_request  # imported here: actions itself builds on this module's parser

        request = find_request(body)
        if request is not None and request[0] == 0:
            return Classification(
                _scaled(3.3, 3.5, 1), IMPORTANT, f"Asks you to do something: {request[1].lower()}."
            )

    # Rule 5: announcements, updates, information broadcasts
    notices = _matches(text_raw, NOTICE_MARKERS)
    tagged = subject.strip().startswith("[")
    if notices or tagged or informational:
        if notices:
            reason = f"Looks like an announcement or update: mentions {_quote(notices)}."
        elif tagged:
            reason = "The subject carries a project or course tag."
        else:
            reason = "Informational message; no action is asked of you."
        return Classification(_scaled(3.1, 3.5, len(notices) + int(tagged)), IMPORTANT, reason)

    # Rule 6: default
    return Classification(2.0, GENERAL, "No urgent, action or announcement wording found.")


def calculate_priority(subject, body):
    """(score, category) for an email. See classify() for the reason as well."""
    result = classify(subject, body)
    return result.score, result.category
