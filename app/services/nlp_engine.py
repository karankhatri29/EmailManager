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
# PROJECT / COURSE CONTEXT EXTRACTOR
# =============================================================
def extract_project_context(subject, text_raw):
    """
    NLP Rule-Based Extractor to isolate specific courses, university codes,
    or corporate program identifiers from headers and email boundaries.
    """
    if not subject:
        subject = ""

    # Pattern A: Capture brackets (e.g., "[VIT_VELLORE_2027]" or "[Project-X]")
    bracket_match = re.search(r"\[([^\]]+)\]", subject)
    if bracket_match:
        return bracket_match.group(1).strip()

    # Pattern B: Split pipes or dashes often found in transactional mailer logs
    for divider in ["|", " - "]:
        if divider in subject:
            parts = subject.split(divider)
            for part in parts:
                if any(
                    k in part.lower()
                    for k in ["intern", "course", "assessment", "project", "class", "lab", "assignment"]
                ):
                    return part.strip()
            return parts[0].strip()

    # Pattern C: Grammatical fallbacks using keywords near target concepts
    course_keywords = ["course", "program", "assessment", "project", "assignment"]
    words = text_raw.split()
    for keyword in course_keywords:
        if keyword in words:
            idx = words.index(keyword)
            if idx > 0:
                prefix = words[idx - 1].upper()
                if len(prefix) > 2 and prefix.isalnum():
                    return f"{prefix} {keyword.capitalize()}"

    return "General Operations"


# =============================================================
# ADVANCED TASK GRAMMATICAL OBJECT MINER
# =============================================================
def extract_action_task(subject, doc, text_raw):
    """
    Grammatical dependency object miner: extracts the literal action command
    (Verb + Direct Object Context) from text rather than using subject headings.
    """
    # Pass 1: Scan for clear directive request action words in the email body
    for token in doc:
        if token.pos_ == "VERB" and token.lemma_ in [
            "submit",
            "complete",
            "review",
            "verify",
            "reply",
            "pay",
            "upload",
            "fill",
            "attend",
            "action",
        ]:
            # Track dependency subtree paths to assemble noun attributes cleanly
            subtree = [w.text for w in token.rights if w.dep_ in ["dobj", "prep", "pobj", "attr"]]
            if subtree:
                extracted = f"{token.text.capitalize()} {' '.join(subtree).strip()}"
                cleaned = re.sub(r"\s+", " ", extracted).strip()
                # Enforce safe length bounds to prevent messy run-on layout lines
                if 10 < len(cleaned) < 75:
                    return cleaned

    # Pass 2: Fallback to filtering the subject header line if body parsing yields no clean verbs
    clean_sub = re.sub(r"(?i)(fwd:|re:|\[.*?\])", "", subject).strip()
    return clean_sub if clean_sub else "Review Context Details"


# =============================================================
# ROBUST CALENDAR DATE & TIMELINE TRACKER
# =============================================================
def extract_explicit_deadline(text_raw, entities):
    """
    Advanced Date and Timeline Analyzer. Combines Regex matching rules
    and Named Entity Recognition (NER) to isolate standard calendar matrices.
    """
    # 1. Advanced Structural Regex Pass (Catches common Indian/Global format layouts missed by spaCy)
    # Pattern A: Numeric sequences like 15/06/2026, 22-07-2026, 05.08
    numeric_date_match = re.search(r"\b\d{1,2}[/\-\.]\d{1,2}([/\-\.]\d{2,4})?\b", text_raw)
    if numeric_date_match:
        return {"value": f"Date: {numeric_date_match.group(0)}", "tier": 1}

    # Pattern B: Textual months like June 15, 14th of August, 2nd July
    month_regex = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    textual_date_match = re.search(
        rf"\b(\d{{1,2}}(st|nd|rd|th)?\s+(of\s+)?{month_regex}|{month_regex}\s+\d{{1,2}}(st|nd|rd|th)?)\b",
        text_raw,
        re.IGNORECASE,
    )
    if textual_date_match:
        return {"value": textual_date_match.group(0).title(), "tier": 1}

    # 2. Named Entity Recognition (NER) Pass
    if entities.get("DATE"):
        # Safeguard fallback filter: prevent generic timestamps like '5 minutes' from counting as dates
        generic_time_indicators = ["minute", "hour", "seconds", "weeks ago", "years ago", "yesterday"]
        discovered_date = entities["DATE"][0]
        if not any(g in discovered_date.lower() for g in generic_time_indicators):
            return {"value": discovered_date.title(), "tier": 1}

    # 3. Tier 3 Relative Day Sweeps ("today", "tomorrow", specific weekdays)
    immediate_triggers = [
        "today",
        "tomorrow",
        "tonight",
        "eod",
        "end of day",
        "by tonight",
        "asap",
        "as soon as possible",
        "urgently",
    ]
    if any(trigger in text_raw for trigger in immediate_triggers):
        return {"value": "Immediate / End of Day Target", "tier": 3}

    weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for day in weekdays:
        if f"by {day}" in text_raw or f"on {day}" in text_raw:
            return {"value": f"Upcoming {day.capitalize()}", "tier": 3}

    # 4. Tier 4: No stated timeframe boundary exists, but task obligation is verified
    return {"value": "No Explicit Deadline Stated", "tier": 4}


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
    deadline_markers = _matches(text_raw, DEADLINE_MARKERS)

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
