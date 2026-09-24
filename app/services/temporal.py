"""Finds the one date a message is really about, and ignores every other date-like piece of text.

Most text that looks like a date is not one that matters: version numbers (2.3.1), prices (12.50), clock
times (10.30), ratios (3/4), years, "sent on" lines, dates in quoted history or footers, and events that
already happened. So nothing is taken at face value. Every date-like phrase becomes a *candidate*, the words
around it are read (a deadline cue such as "by" or "due", an event word such as "meeting", an obligation such
as "please", a past-tense verb, an incidental cue such as "posted on"), and it is scored. Only a candidate
with enough evidence is shown; otherwise the answer is "no date" rather than a guess.

Nothing here depends on a sender, a template or a subject. It is plain English cues plus grammar (spaCy).
"""

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from datetime import time as clock_time
from zoneinfo import ZoneInfo

from .nlp_engine import nlp

MIN_SCORE = 0.6  # below this the candidate is not shown
MAX_DAYS_AHEAD = 366
RECENT_PAST_DAYS = 45  # a month-day this far back is history, not "next year"
MAX_TEXT_CHARS = 6000

DEADLINE = "deadline"
EVENT = "event"
ASAP = "asap"

MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4, "may": 5,
    "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}  # fmt: skip
WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "twelve": 12, "fourteen": 14, "fifteen": 15, "twenty": 20, "thirty": 30,
}  # fmt: skip

_MONTH_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))
_WEEKDAY_ALT = "|".join(WEEKDAYS)
_NUM_ALT = r"\d{1,3}|" + "|".join(NUMBER_WORDS)

# --- what surrounds a date ------------------------------------------------------------------------------------

# Words directly in front of a date that make it something the reader must meet.
_DEADLINE_PRE = re.compile(
    r"(?:\b(?:by|before|until|till|through|thru|prior to|due(?: (?:on|by|date))?|deadline|expires?(?: on)?|expiry(?: date)?|"
    r"closes?(?: on)?|closing(?: date)?|cut-?off|ends?(?: on)?|no later than|not later than|on or before|"
    r"valid (?:until|till|through)|latest(?: by)?|last (?:date|day)(?: to \w+)?(?: is)?|must be (?:received|returned|submitted|completed|paid)(?: by)?)"
    r"\b)(?:[\s:,\-(]+(?:the|on|or|before|this|next|of|end|is|be))*[\s:,\-(]*$",
    re.IGNORECASE,
)
# Nouns and verbs that make the whole sentence about a limit.
_DEADLINE_WORD = re.compile(
    r"\b(?:deadline|due date|closing date|last date|last day|cut-?off|due|expires?|expiry|closes?|no later than|not later than)\b",
    re.IGNORECASE,
)
_DEADLINE_POST = re.compile(
    r"^[\s)\],.:;-]*(?:is |was |will be )?(?:the |our |a )?(?:last (?:date|day)|deadline|due|cut-?off|closing|final (?:date|day))",
    re.IGNORECASE,
)
_LABEL_EVENT = re.compile(
    r"(?:^|\n)\s*(?:event |meeting |exam |class |interview |start )?(?:date|when|day|on)\s*[:\-]\s*$",
    re.IGNORECASE,
)
_LABEL_DEADLINE = re.compile(
    r"(?:^|\n)\s*(?:due|deadline|last date|closing date|expiry|expires)(?: date)?\s*[:\-]\s*$", re.IGNORECASE
)
# A date that only says when something else was written, sent or happened.
_INCIDENTAL_PRE = re.compile(
    r"\b(?:sent|posted|published|updated|last updated|received|dated|issued|created|generated|as of|since|born|founded|"
    r"established|copyright|effective|valid from|purchased|ordered|order date|invoice date|statement date|transaction date|"
    r"paid on|joined|last login|logged in|signed in|shipped|delivered|opened|filed|modified|revised|date of (?:issue|birth))\b"
    r"[\s:,\-(]*(?:on\s+)?$",
    re.IGNORECASE,
)
_FUTURE = re.compile(
    r"\b(?:will|shall|going to|scheduled|planned|upcoming|is due|are due|must|should|need to|to be held|takes? place)\b",
    re.IGNORECASE,
)

_OBLIGATION = re.compile(
    r"\b(?:please|kindly|must|need(?:s|ed)? to|needs?|required|requested?|request you|have to|has to|had to|should|make sure|"
    r"ensure|remember to|don'?t forget|do not forget|action required|mandatory|compulsory|expected to|responsible for|"
    r"let (?:me|us) know|get back to|would you|could you|can you|will you|are asked to|is asked to)\b",
    re.IGNORECASE,
)
# Verbs whose object the reader must hand over or do by a limit (lemmas).
_DEADLINE_LEMMAS = frozenset(
    [
        "submit",
        "register",
        "apply",
        "pay",
        "reply",
        "respond",
        "return",
        "complete",
        "confirm",
        "send",
        "upload",
        "renew",
        "sign",
        "enroll",
        "enrol",
        "rsvp",
        "file",
        "fill",
        "provide",
        "book",
        "cancel",
        "finish",
        "email",
        "forward",
        "approve",
        "update",
        "clear",
        "settle",
        "collect",
        "claim",
        "redeem",
        "vote",
        "nominate",
        "hand",
        "deliver",
        "ship",
        "reserve",
        "pick",
        "check",
    ]
)
_EVENT_LEMMAS = frozenset(
    [
        "meeting",
        "meet",
        "call",
        "webinar",
        "seminar",
        "interview",
        "exam",
        "examination",
        "test",
        "quiz",
        "class",
        "lecture",
        "lab",
        "session",
        "workshop",
        "conference",
        "ceremony",
        "appointment",
        "presentation",
        "demo",
        "party",
        "trip",
        "flight",
        "departure",
        "arrive",
        "arrival",
        "delivery",
        "event",
        "celebration",
        "concert",
        "match",
        "game",
        "lunch",
        "dinner",
        "breakfast",
        "coffee",
        "kickoff",
        "orientation",
        "training",
        "hearing",
        "visit",
        "tour",
        "festival",
        "show",
        "screening",
        "briefing",
        "standup",
        "sync",
        "review",
        "retreat",
        "reunion",
        "wedding",
        "graduation",
        "launch",
        "opening",
        "deliver",
    ]
)
_EVENT_PHRASE = re.compile(
    r"\b(?:kick-?off|join us|see you|take(?:s)? place|will be held|is held|scheduled for|scheduled on|starts?|begins?|commences?|invited)\b",
    re.IGNORECASE,
)

# --- where the message stops being the message ----------------------------------------------------------------

_HISTORY = [
    re.compile(r"^[ \t]*On\b[^\n]{5,220}(?:\n[^\n]{0,140})?\bwrote:[ \t]*$", re.IGNORECASE | re.MULTILINE),
    re.compile(
        r"^[ \t]*-{2,}[ \t]*(?:Original Message|Reply above this line)[ \t]*-{2,}",
        re.IGNORECASE | re.MULTILINE,
    ),
    re.compile(r"^[ \t]*_{5,}[ \t]*$", re.MULTILINE),
    re.compile(r"^[ \t]*From:[^\n]+\n[ \t]*(?:Sent|Date):", re.IGNORECASE | re.MULTILINE),
]
_SIGNATURE = re.compile(r"^-- ?$", re.MULTILINE)
_FOOTER = re.compile(
    r"unsubscribe|privacy policy|view (?:this email )?in (?:your )?browser|all rights reserved|©|\(c\)\s*\d{4}|"
    r"you (?:are )?receiv(?:ed|ing) this (?:email|message)|manage (?:your )?(?:preferences|subscription)|"
    r"terms (?:of|&) (?:service|use)|sent from my |get outlook for|this email was sent to",
    re.IGNORECASE,
)


def strip_history_and_footer(text: str) -> str:
    """The part of a message its author wrote now: no quoted replies, signature or boilerplate footer."""
    text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(">"))
    cuts = [m.start() for pattern in _HISTORY for m in [pattern.search(text)] if m]
    if cuts:
        text = text[: min(cuts)]
    signature = _SIGNATURE.search(text)
    if signature:
        text = text[: signature.start()]
    footer = _FOOTER.search(text)
    if footer and footer.start() >= 0.2 * len(text):  # a footer, not a message that is all boilerplate
        text = text[: footer.start()]
    return text.strip()


# --- candidates -------------------------------------------------------------------------------------------------

_L = r"(?<![\w$€£₹#/.\-])"  # not glued to a digit, a currency sign, a slash, a dot or a hyphen
_R = r"(?![\w%]|[./\-]\d)"


@dataclass
class Candidate:
    start: int
    end: int
    text: str
    day: date | None  # None only for "asap"
    base: float
    specific: int  # how specific the pattern is; when two overlap the more specific one wins
    kind_hint: str | None = (
        None  # DEADLINE if the phrase itself is a limit ("within 3 days", "EOD"), else None
    )
    ambiguous: bool = False
    needs_cue: bool = False  # only accepted with a deadline cue right before it (e.g. "by 12/10")
    precision: str = "day"  # day | week | month
    rollover: bool = True  # a past month-day may mean next year (only with a deadline cue)


def _valid(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _month_day_year(month: int, day: int, year: int | None, sent: date) -> tuple[date | None, bool]:
    """(date, year_was_guessed). A missing year means the coming occurrence, when that is plausible."""
    if year is not None:
        return _valid(year, month, day), False
    this_year = _valid(sent.year, month, day)
    if this_year is not None and this_year >= sent:
        return this_year, True
    return _valid(sent.year + 1, month, day), True


def _order_pair(a: int, b: int, order: str) -> tuple[int, int, bool] | None:
    """(day, month, ambiguous) for a numeric a/b pair, or None if it cannot be a date."""
    if a > 12 and 1 <= b <= 12:
        return a, b, False
    if b > 12 and 1 <= a <= 12:
        return b, a, False
    if a <= 12 and b <= 12 and a >= 1 and b >= 1:
        return (a, b, a != b) if order == "DMY" else (b, a, a != b)
    return None


def _to_int(token: str) -> int:
    return int(token) if token.isdigit() else NUMBER_WORDS[token.lower()]


def _last_day(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _weekday_date(target: int, sent: date, qualifier: str | None, deadline_like: bool) -> date:
    if qualifier in ("next", "coming", "upcoming"):
        monday_next = sent + timedelta(days=7 - sent.weekday())
        return monday_next + timedelta(days=target)
    if qualifier == "this":
        offset = (target - sent.weekday()) % 7
        return sent + timedelta(days=offset)
    offset = (target - sent.weekday()) % 7
    if offset == 0 and not deadline_like:
        offset = 7  # "on Friday", said on a Friday, means the next one
    return sent + timedelta(days=offset)


def find_candidates(text: str, sent: date, order: str) -> list[Candidate]:
    found: list[Candidate] = []

    def add(m: re.Match, day: date | None, base: float, specific: int, **kw) -> None:
        if day is not None or kw.get("kind_hint") == ASAP:
            found.append(Candidate(m.start(), m.end(), m.group(0), day, base=base, specific=specific, **kw))

    # 1. 2026-11-15
    for m in re.finditer(rf"{_L}(\d{{4}})-(\d{{1,2}})-(\d{{1,2}}){_R}", text):
        if 1990 <= int(m[1]) <= 2100:
            add(m, _valid(int(m[1]), int(m[2]), int(m[3])), 0.5, 9)

    # 2. 15/10/2026, 15-10-26, 15.10.2026 (a two-digit year only with / or -, so 1.2.34 stays a version number)
    for m in re.finditer(rf"{_L}(\d{{1,2}})([/.\-])(\d{{1,2}})\2(\d{{4}}|\d{{2}}){_R}", text):
        if m[2] == "." and len(m[4]) != 4:
            continue
        pair = _order_pair(int(m[1]), int(m[3]), order)
        if pair is None:
            continue
        year = int(m[4]) + (2000 if len(m[4]) == 2 else 0)
        add(m, _valid(year, pair[1], pair[0]), 0.5, 8, ambiguous=pair[2])

    # 3. 12/10 with no year: only when a cue says it is a date (so 3/4, 24/7, 1/2 and scores never qualify)
    for m in re.finditer(rf"{_L}(\d{{1,2}})([/\-])(\d{{1,2}}){_R}", text):
        pair = _order_pair(int(m[1]), int(m[3]), order)
        if pair is None:
            continue
        day, _ = _month_day_year(pair[1], pair[0], None, sent)
        add(m, day, 0.3, 5, ambiguous=pair[2], needs_cue=True)

    # 4a. 15 October 2026, 3rd of Nov, 2 Oct
    for m in re.finditer(
        rf"{_L}(\d{{1,2}})(?:st|nd|rd|th)?\s*(?:of\s+)?({_MONTH_ALT})\b\.?(?:,?\s*(\d{{4}}))?(?![\w])",
        text,
        re.IGNORECASE,
    ):
        if m[2].lower() == "may" and m[2][0] != "M":
            continue  # the verb, not the month
        day, _ = _month_day_year(MONTHS[m[2].lower()], int(m[1]), int(m[3]) if m[3] else None, sent)
        add(m, day, 0.5 if m[3] else 0.45, 7)

    # 4b. October 15, Oct 8th, 2026
    for m in re.finditer(
        rf"\b({_MONTH_ALT})\b\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?![\d:]|\s*(?:am|pm)\b)(?:,?\s*(\d{{4}}))?",
        text,
        re.IGNORECASE,
    ):
        if m[1].lower() == "may" and m[1][0] != "M":
            continue
        day, _ = _month_day_year(MONTHS[m[1].lower()], int(m[2]), int(m[3]) if m[3] else None, sent)
        add(m, day, 0.5 if m[3] else 0.45, 7)

    # 5. end of the month / end of October / EOM
    for m in re.finditer(r"\b(?:end of (?:the )?month|eom)\b", text, re.IGNORECASE):
        add(m, _last_day(sent.year, sent.month), 0.5, 6, kind_hint=DEADLINE, precision="month")
    for m in re.finditer(rf"\bend of (?:the )?({_MONTH_ALT})\b", text, re.IGNORECASE):
        month = MONTHS[m[1].lower()]
        year = sent.year if _last_day(sent.year, month) >= sent else sent.year + 1
        add(m, _last_day(year, month), 0.5, 6, kind_hint=DEADLINE, precision="month")
    for m in re.finditer(r"\b(?:end of (?:the )?week|eow)\b", text, re.IGNORECASE):
        add(m, sent + timedelta(days=(4 - sent.weekday()) % 7), 0.5, 6, kind_hint=DEADLINE, precision="week")
    for m in re.finditer(
        r"\b(?:by |before |until |till |end of )(?:the )?(this|next) (week|month)\b", text, re.IGNORECASE
    ):
        if m[2].lower() == "week":
            monday = (
                sent - timedelta(days=sent.weekday()) + timedelta(days=7 if m[1].lower() == "next" else 0)
            )
            day = monday + timedelta(days=4)
        else:
            shift = 1 if m[1].lower() == "next" else 0
            month0 = sent.month - 1 + shift
            day = _last_day(sent.year + month0 // 12, month0 % 12 + 1)
        add(m, day, 0.3, 4, kind_hint=DEADLINE, precision=m[2].lower())

    # 6. Friday, next Wednesday, this Monday (whole words, singular, and never "every Monday")
    for m in re.finditer(
        rf"\b(?:(this|next|coming|upcoming)\s+)?({_WEEKDAY_ALT})\b(?!s\b)", text, re.IGNORECASE
    ):
        lead = text[max(0, m.start() - 8) : m.start()].lower()
        if re.search(r"(?:every|each|all|last|previous)\s*$", lead):
            continue
        qualifier = m[1].lower() if m[1] else None
        add(m, _weekday_date(WEEKDAYS[m[2].lower()], sent, qualifier, False), 0.3, 3)

    # 7. today, tonight, tomorrow, the day after tomorrow (not "today's")
    for m in re.finditer(r"\b(day after tomorrow|tomorrow|tonight|today)\b(?!['’]s)", text, re.IGNORECASE):
        word = m[1].lower()
        delta = {"day after tomorrow": 2, "tomorrow": 1}.get(word, 0)
        add(m, sent + timedelta(days=delta), 0.3, 2)

    # 8. EOD, end of the day, close of business
    for m in re.finditer(r"\b(?:eod|cob|end of (?:the )?day|close of business)\b", text, re.IGNORECASE):
        add(m, sent, 0.55, 6, kind_hint=DEADLINE)

    # 9. within 3 days / in two weeks / 5 days from now
    for m in re.finditer(
        rf"\b(within|in)\s+(?:the next\s+)?({_NUM_ALT})\s+(hours?|days?|weeks?|months?)\b",
        text,
        re.IGNORECASE,
    ):
        n = _to_int(m[2])
        unit = m[3].lower().rstrip("s")
        span = {"hour": 0 if n < 24 else n // 24, "day": n, "week": 7 * n, "month": 30 * n}[unit]
        candidate = Candidate(
            m.start(),
            m.end(),
            m[0],
            sent + timedelta(days=span),
            base=0.55,
            specific=4,
            needs_cue=m[1].lower() == "in",
        )
        if m[1].lower() == "within":
            candidate.kind_hint = DEADLINE
        found.append(candidate)
    for m in re.finditer(rf"\b({_NUM_ALT})\s+(days?|weeks?)\s+from\s+(?:now|today)\b", text, re.IGNORECASE):
        found.append(
            Candidate(
                m.start(),
                m.end(),
                m[0],
                sent + timedelta(days=_to_int(m[1]) * (7 if m[2].lower().startswith("w") else 1)),
                base=0.4,
                specific=4,
            )
        )

    # 10. by the 30th / before the 5th (the ordinal must end the phrase: not "the 3rd floor" or "the 2nd door")
    for m in re.finditer(
        r"\b(?:by|before|until|till|on)\s+(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)\b(?=\s*(?:[.,;:!?)]|\n|$)|\s+(?:of|at|to|and|or|so|if|for|in|as|please|latest)\b)",
        text,
        re.IGNORECASE,
    ):
        n = int(m[1])
        day = _valid(sent.year, sent.month, n)
        if day is None or day < sent:
            day = None
            for step in (1, 2, 3):
                year, month = sent.year + (sent.month - 1 + step) // 12, (sent.month - 1 + step) % 12 + 1
                day = _valid(year, month, n)
                if day is not None:
                    break
        if day is not None:
            found.append(
                Candidate(
                    m.start(1),
                    m.end(),
                    text[m.start(1) : m.end()],
                    day,
                    base=0.35,
                    specific=5,
                    rollover=False,
                )
            )

    # 11. asap and friends
    for m in re.finditer(
        r"\b(?:asap|as soon as possible|immediately|right away|at once|without delay|urgently)\b",
        text,
        re.IGNORECASE,
    ):
        found.append(Candidate(m.start(), m.end(), m[0], None, base=0.35, specific=1, kind_hint=ASAP))

    # keep the most specific of any overlapping candidates (so "Monday 28 September" is one date, not two)
    found.sort(key=lambda c: (-c.specific, c.start))
    kept: list[Candidate] = []
    for c in found:
        if not any(c.start < k.end and k.start < c.end for k in kept):
            kept.append(c)
    return sorted(kept, key=lambda c: c.start)


# --- reading the words around a candidate ----------------------------------------------------------------------

_TIME = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)(?!\w)|\b([01]?\d|2[0-3]):([0-5]\d)\b|\b(noon|midnight)\b",
    re.IGNORECASE,
)


def _find_time(sentence: str, start: int, end: int) -> tuple[clock_time | None, str | None]:
    """A clock time in the same sentence, close to the date: '5 pm', '17:00', 'noon'."""
    best = None
    for m in _TIME.finditer(sentence):
        distance = min(abs(m.start() - end), abs(start - m.end()))
        if distance > 40 or (best is not None and distance >= best[0]):
            continue
        if m[6]:
            value = clock_time(12, 0) if m[6].lower() == "noon" else clock_time(0, 0)
        elif m[3]:
            hour = int(m[1]) % 12 + (12 if m[3].lower().startswith("p") else 0)
            if int(m[1]) > 12 or int(m[2] or 0) > 59:
                continue
            value = clock_time(hour, int(m[2] or 0))
        else:
            value = clock_time(int(m[4]), int(m[5]))
        best = (distance, value, m[0])
    return (best[1], best[2]) if best else (None, None)


def _tidy_pre(pre: str) -> str:
    """The words before a date, with clock times taken out ('by 5 pm on 15 Oct' reads as 'by on')."""
    return re.sub(r"\s+", " ", _TIME.sub(" ", pre)).strip() + " "


@dataclass
class Deadline:
    """The date a message asks for or announces."""

    kind: str  # deadline | event | asap
    day: date | None
    text: str  # the words in the email it came from, e.g. "by 15 October 2026"
    confidence: float
    time: clock_time | None = None
    time_text: str | None = None
    precision: str = "day"
    others: list["Deadline"] = field(
        default_factory=list
    )  # other dates that also passed, e.g. the event itself


def _sentences(doc):
    return [(s.start_char, s.end_char, s) for s in doc.sents]


def _sentence_at(bounds, position: int):
    for start, end, span in bounds:
        if start <= position < end:
            return start, end, span
    return bounds[-1] if bounds else (0, 0, None)


def _is_past_tense(span) -> bool:
    if span is None:
        return False
    verbs = [t for t in span if t.pos_ in ("VERB", "AUX")]
    return any(
        "Past" in t.morph.get("Tense") and t.dep_ in ("ROOT", "conj", "ccomp") for t in verbs
    ) and not _FUTURE.search(span.text)


def _score(
    c: Candidate,
    text: str,
    lower: str,
    sentence: str,
    span,
    sentence_start: int,
    in_subject: bool,
    sent: date,
):
    """(score, kind, cue_start) for one candidate."""
    line_start = text.rfind("\n", 0, c.start) + 1
    local_start = max(line_start, sentence_start)
    pre_raw = text[local_start : c.start]
    pre = _tidy_pre(pre_raw)
    post = text[c.end : c.end + 60]

    if _INCIDENTAL_PRE.search(pre):
        return 0.0, None, None

    score = c.base
    kind = c.kind_hint if c.kind_hint in (DEADLINE, EVENT) else None
    cue_start = None

    cue = _DEADLINE_PRE.search(pre)
    label_deadline = _LABEL_DEADLINE.search(text[max(0, line_start - 1) : c.start] + "")
    has_direct = cue is not None or label_deadline is not None
    if c.needs_cue and not has_direct:
        return 0.0, None, None
    if cue:
        score += 0.35
        kind = DEADLINE
        cue_start = local_start + cue.start()
    elif label_deadline:
        score += 0.35
        kind = DEADLINE
    elif _DEADLINE_POST.search(post):
        score += 0.3
        kind = DEADLINE
    elif _LABEL_EVENT.search(text[max(0, line_start - 1) : c.start]):
        score += 0.3
        kind = EVENT

    sentence_lower = sentence.lower()
    lemmas = (
        {t.lemma_.lower() for t in span if t.is_alpha}
        if span is not None
        else set(re.findall(r"[a-z]+", sentence_lower))
    )
    obligation = bool(_OBLIGATION.search(sentence))
    deadline_verb = bool(lemmas & _DEADLINE_LEMMAS)
    event_word = bool(lemmas & _EVENT_LEMMAS) or bool(_EVENT_PHRASE.search(sentence))

    if kind is None:
        if event_word and not (obligation and deadline_verb):
            kind, score = EVENT, score + 0.3
        elif obligation or deadline_verb:
            kind, score = DEADLINE, score + (0.3 if obligation else 0.2)
        elif _DEADLINE_WORD.search(sentence_lower):
            kind, score = DEADLINE, score + 0.25
    elif kind == DEADLINE and (obligation or deadline_verb):
        score += 0.1
    if c.kind_hint == ASAP:
        kind = ASAP
        score += 0.25 if obligation else 0.0

    if in_subject:
        score += 0.1
    if c.ambiguous:
        score -= 0.1
    if _is_past_tense(span):
        score -= 0.3
    if kind is None:
        return 0.0, None, None
    return score, kind, cue_start


def _resolve_year_rollover(c: Candidate, sent: date, cued: bool) -> date | None:
    """A month-day already behind us is history, unless a limit cue says it means next year."""
    if c.day is None or c.day >= sent:
        return c.day
    if not c.rollover:
        return None
    behind = (sent - c.day).days
    if behind <= RECENT_PAST_DAYS and c.day.year == sent.year:
        return None
    return c.day if c.day.year != sent.year else None


def extract_deadline(
    subject: str, body: str, sent_at: datetime | date, order: str = "DMY"
) -> Deadline | None:
    """The deadline or event date a message is about, or None if it does not really state one."""
    sent = sent_at.date() if isinstance(sent_at, datetime) else sent_at
    body = strip_history_and_footer(body or "")[:MAX_TEXT_CHARS]
    subject = (subject or "").strip()
    text = f"{subject}\n{body}" if subject else body
    if not text.strip():
        return None

    doc = nlp(text)
    bounds = _sentences(doc)
    lower = text.lower()
    subject_end = len(subject)

    accepted: list[Deadline] = []
    for c in find_candidates(text, sent, order):
        s_start, s_end, span = _sentence_at(bounds, c.start)
        sentence = text[s_start:s_end]
        score, kind, cue_start = _score(c, text, lower, sentence, span, s_start, c.start < subject_end, sent)
        if kind is None or score < MIN_SCORE - 1e-9:
            continue

        day = c.day
        if day is not None:
            if day < sent:
                continue
            if day > sent + timedelta(days=MAX_DAYS_AHEAD):
                continue
        else:  # asap: only meaningful when nothing dated says otherwise
            pass

        clock, clock_text = _find_time(text[s_start:s_end], c.start - s_start, c.end - s_start)
        phrase_start = cue_start if cue_start is not None else c.start
        phrase = re.sub(r"\s+", " ", text[phrase_start : c.end]).strip(" .,;:")[:60]
        accepted.append(
            Deadline(kind, day, phrase, round(min(score, 1.0), 2), clock, clock_text, c.precision)
        )

    if not accepted:
        return None

    rank = {DEADLINE: 0, EVENT: 1, ASAP: 2}
    accepted.sort(key=lambda d: (rank[d.kind], -d.confidence, d.day or date.max))
    best = accepted[0]
    best.others = accepted[1:4]
    return best


# --- presentation ---------------------------------------------------------------------------------------------

_MONTH_NAMES = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def format_day(day: date, today: date) -> str:
    """'Today', 'Tomorrow', 'Fri 25 Sep', or with the year when it is not this year or far away."""
    delta = (day - today).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Tomorrow"
    label = f"{_DAY_NAMES[day.weekday()]} {day.day} {_MONTH_NAMES[day.month]}"
    if day.year != today.year and delta > 180:
        label += f" {day.year}"
    return label


def deadline_label(kind: str | None, day: date | None, today: date) -> str:
    """The short text shown next to a task: 'Due Tomorrow', 'Overdue by 3 days', 'Fri 2 Oct', 'ASAP', 'No date'."""
    if kind == ASAP:
        return "ASAP"
    if day is None or kind is None:
        return "No date"
    if kind == DEADLINE:
        if day < today:
            late = (today - day).days
            return f"Overdue by {late} day{'s' if late != 1 else ''}"
        return f"Due {format_day(day, today)}"
    return format_day(day, today) if day >= today else f"Was {format_day(day, today)}"


def date_order_for_timezone(tzname: str | None) -> str:
    """Month-first ('MDY') for the United States and neighbours; day-first everywhere else.

    Only used for numeric dates that are truly ambiguous, like 03/04/2026. Anything with a day above 12,
    a month name or an ISO date is read exactly as written.
    """
    if not tzname:
        return "DMY"
    try:
        ZoneInfo(tzname)
    except Exception:
        return "DMY"
    us_style = tzname.startswith(
        (
            "America/New_York",
            "America/Chicago",
            "America/Denver",
            "America/Los_Angeles",
            "America/Phoenix",
            "America/Anchorage",
            "America/Detroit",
            "America/Boise",
            "America/Indiana",
            "America/Kentucky",
            "America/Toronto",
            "America/Vancouver",
            "America/Halifax",
            "America/Edmonton",
            "America/Winnipeg",
            "Pacific/Honolulu",
            "US/",
        )
    )
    return "MDY" if us_style else "DMY"
