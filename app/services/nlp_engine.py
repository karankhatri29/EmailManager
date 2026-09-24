import random
import re

import spacy

# Initialize the spaCy core model
try:
    nlp = spacy.load("en_core_web_sm")
except OSError:
    import os

    os.system("python -m spacy download en_core_web_sm")
    nlp = spacy.load("en_core_web_sm")


# =============================================================
# 📁 PROJECT / COURSE CONTEXT EXTRACTOR
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
# 🛠️ ADVANCED TASK GRAMMATICAL OBJECT MINER
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
# ⏱️ ROBUST CALENDAR DATE & TIMELINE TRACKER
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
# ⚙️ CORE NLP PROCESSING PIPELINE
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
# ⚡ SEMANTIC PRIORITY CLASSIFIER (With Informational Guards)
# =============================================================
def calculate_priority(subject, body):
    """
    Granular Classifier. Analyzes text metrics to separate security alerts,
    real transaction receipts, and true assignments from generic noise or newsletters.
    """
    nlp_data = process_text(f"{subject} {body}")

    text_raw = nlp_data["clean_text_raw"]
    tokens = nlp_data["tokens"]
    entities = nlp_data["entities"]

    # -------------------------------------------------------------
    # Rule 1: PROMOTIONAL / ADS / MARKETING FILTER
    # -------------------------------------------------------------
    promo_triggers = [
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
    if any(trigger in text_raw for trigger in promo_triggers):
        return random.uniform(1.0, 1.4), "Promotional"

    # -------------------------------------------------------------
    # Rule 2: SECURITY & AUTHENTICATION ENGINE
    # -------------------------------------------------------------
    security_triggers = [
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
    if any(trigger in text_raw for trigger in security_triggers):
        return round(random.uniform(4.5, 4.8), 2), "Urgent / Action Required"

    # -------------------------------------------------------------
    # Rule 3: SEMANTIC BANKING & REAL TRANSACTION RECEIPT VALIDATOR
    # -------------------------------------------------------------
    financial_verbs = ["debited", "credited", "transferred", "charged", "withdrawn", "processed successfully"]
    has_financial_verb = any(verb in text_raw for verb in financial_verbs)
    has_real_currency = len(entities["MONEY"]) > 0

    if has_financial_verb or (
        ("bank" in text_raw or "statement" in text_raw or "invoice" in text_raw) and has_real_currency
    ):
        return round(random.uniform(4.2, 4.4), 2), "Urgent / Action Required"

    # -------------------------------------------------------------
    # Rule 4: IMPERATIVE OBLIGATION PARSER (With Heavy System/Info Noise Filters)
    # -------------------------------------------------------------
    # Guard strings representing system notifications or general text summaries
    informational_guards = [
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
    is_explicitly_informational = any(guard in text_raw for guard in informational_guards)

    has_action_obligation = False
    explicit_deadline_markers = ["deadline", "due date", "by end of day", "final call to submit"]
    has_explicit_deadline = any(marker in text_raw for marker in explicit_deadline_markers)

    if not is_explicitly_informational:
        # Check for un-negated action flags
        if "action required" in text_raw and not any(neg in text_raw for neg in ["no action", "not require"]):
            has_action_obligation = True

        # Dependency verification pass
        for t in tokens:
            if t["pos"] == "VERB" and t["dep"] in ["ROOT", "xcomp"]:
                task_lemmas = [
                    "submit",
                    "complete",
                    "review",
                    "verify",
                    "reply",
                    "attend",
                    "fill",
                    "upload",
                    "pay",
                    "submit",
                ]
                if t["lemma"] in task_lemmas and any(
                    cmd in text_raw for cmd in ["please", "kindly", "must", "required", "urgently"]
                ):
                    has_action_obligation = True
                    break

    # Route true priority items to Urgent
    if (has_action_obligation or has_explicit_deadline) and not is_explicitly_informational:
        return round(random.uniform(4.0, 4.1), 2), "Urgent / Action Required"

    # -------------------------------------------------------------
    # Rule 5: IMPORTANT UPDATES, ANNOUNCEMENTS & INFO BROADCASTS
    # -------------------------------------------------------------
    notice_markers = [
        "announcement",
        "notice",
        "update regarding",
        "update",
        "schedule",
        "meeting minutes",
        "status report",
        "digest",
    ]
    if (
        any(marker in text_raw for marker in notice_markers)
        or subject.strip().startswith("[")
        or is_explicitly_informational
    ):
        # Realistic, normalized intermediate priority scoring range
        return round(random.uniform(3.1, 3.5), 2), "Important"

    # -------------------------------------------------------------
    # Rule 6: DEFAULT GENERAL FALLBACK BOUNDARY
    # -------------------------------------------------------------
    return round(random.uniform(2.0, 2.5), 2), "General"
