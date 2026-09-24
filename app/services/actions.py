"""Names the thing an email asks the reader to do, in a short imperative title ("Submit the quarterly report").

Grammar, not patterns: a sentence that asks for something has a base-form verb (an imperative, or the verb after
"please", "need to", "must", "could you"...). That verb and its object, minus the time clause and any second
action, is the title. When no such sentence exists the cleaned subject is used instead.
"""

import re

from .nlp_engine import nlp
from .temporal import _OBLIGATION, strip_history_and_footer

MAX_TITLE = 80
MAX_TEXT_CHARS = 4000

# Everyday verbs of doing something for someone; deliberately broad and not tied to any sender or domain.
ACTION_LEMMAS = frozenset(
    [
        "submit",
        "send",
        "complete",
        "review",
        "verify",
        "reply",
        "respond",
        "pay",
        "upload",
        "fill",
        "sign",
        "renew",
        "confirm",
        "register",
        "attend",
        "return",
        "bring",
        "provide",
        "update",
        "approve",
        "schedule",
        "book",
        "cancel",
        "prepare",
        "finish",
        "share",
        "forward",
        "download",
        "install",
        "apply",
        "enrol",
        "enroll",
        "collect",
        "pick",
        "check",
        "fix",
        "resolve",
        "read",
        "join",
        "contact",
        "call",
        "email",
        "remind",
        "attach",
        "arrange",
        "settle",
        "transfer",
        "deposit",
        "file",
        "rsvp",
        "vote",
        "sort",
        "clear",
        "collect",
        "take",
        "write",
        "draft",
        "answer",
        "follow",
        "bring",
        "pack",
        "order",
        "buy",
        "wear",
        "print",
        "fax",
        "mail",
        "post",
        "deliver",
        "ship",
        "sign",
        "acknowledge",
        "accept",
        "decline",
        "choose",
        "select",
        "nominate",
        "choose",
        "reserve",
        "pick_up",
        "ensure",
        "notify",
        "inform",
        "let",
        "make",
    ]
)
# Verbs that are a request all by themselves, even without "please" ("Pay the electricity bill.").
_DIRECT = frozenset(
    [
        "submit",
        "pay",
        "sign",
        "renew",
        "confirm",
        "complete",
        "upload",
        "return",
        "reply",
        "respond",
        "register",
        "fill",
        "send",
        "approve",
        "verify",
    ]
)
# Verbs that look like actions but only point at the message ("see below", "click here").
_WEAK = frozenset(["make", "let", "take", "follow", "ensure"])
_LEAD_NOISE = re.compile(
    r"^(?:re|fwd?|fw|reminder|action required|urgent|fyi|important)\s*[:\-]\s*", re.IGNORECASE
)
_TIME_CLAUSE = re.compile(
    r"\s+(?:by|before|until|till|within|no later than|not later than|on or before|asap|as soon as possible|"
    r"today|tomorrow|tonight|this (?:week|month|morning|afternoon|evening)|next (?:week|month))\b.*$",
    re.IGNORECASE,
)
_LINE_LABEL = re.compile(
    r"^[ 	]*(?:reminder|action required|urgent|important|note|fyi|task)[ 	]*:[ 	]*",
    re.IGNORECASE | re.MULTILINE,
)
_SPACES = re.compile(r"\s+")


def _clean_subject(subject: str) -> str:
    text = _SPACES.sub(" ", subject or "").strip()
    while True:
        stripped = _LEAD_NOISE.sub("", text).strip()
        if stripped == text:
            break
        text = stripped
    text = re.sub(r"^\[[^\]]{1,40}\]\s*", "", text)
    return text[:MAX_TITLE].strip() or "Review this message"


def _phrase(verb) -> str:
    """The verb with its object, without a second coordinated action or a trailing time clause."""
    skip: set[int] = set()
    for token in verb.subtree:
        if token.i != verb.i and token.dep_ in ("conj", "advcl", "relcl", "cc") and token.i > verb.i:
            skip.update(t.i for t in token.subtree)
            if token.dep_ == "cc":
                skip.add(token.i)
    words = [t for t in verb.subtree if t.i not in skip and t.i >= verb.i]
    text = _SPACES.sub(" ", "".join(t.text_with_ws for t in words)).strip(" ,;:-.!?")
    text = _TIME_CLAUSE.sub("", text)
    text = re.sub(r"^(?:please|kindly)\s+", "", text, flags=re.IGNORECASE)
    return text.strip(" ,;:-.!?")


def _candidates(sentence):
    for token in sentence:
        if token.pos_ not in ("VERB", "AUX") or token.lemma_.lower() not in ACTION_LEMMAS:
            continue
        if token.lemma_.lower() in _WEAK:
            continue
        subjects = [c for c in token.children if c.dep_ in ("nsubj", "nsubjpass")]
        base_form = token.tag_ in ("VB", "VBP")
        imperative = base_form and not subjects
        to_infinitive = base_form and any(c.dep_ == "aux" and c.lower_ == "to" for c in token.children)
        addressed = base_form and all(s.lower_ in ("you", "we", "i") for s in subjects) and bool(subjects)
        if imperative and token.lemma_.lower() not in _DIRECT and not _OBLIGATION.search(sentence.text):
            continue  # "Read more on our site" is an invitation, not a task
        if imperative or to_infinitive or (addressed and _OBLIGATION.search(sentence.text)):
            yield token


def extract_action_title(subject: str, body: str) -> str:
    """The action a message asks for as a short title; the subject when it asks for nothing in particular."""
    text = strip_history_and_footer(body or "")[:MAX_TEXT_CHARS]
    doc = nlp(_LINE_LABEL.sub("", text))
    best = None
    for sentence in doc.sents:
        obliged = bool(_OBLIGATION.search(sentence.text))
        for verb in _candidates(sentence):
            phrase = _phrase(verb)
            if len(phrase.split()) < 2 or len(phrase) > MAX_TITLE * 2:
                continue
            rank = 0 if obliged else 1
            if best is None or rank < best[0]:
                best = (rank, phrase)
        if best and best[0] == 0:
            break
    if best is None:
        return _clean_subject(subject)
    title = best[1][0].upper() + best[1][1:]
    return title if len(title) <= MAX_TITLE else title[: MAX_TITLE - 1].rstrip() + "…"
