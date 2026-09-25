"""Finds bookings (flights, trains, buses, movies, events, hotels) in email text.

Rule-based, like the rest of the app's NLP: it looks at the sender, the subject and labelled lines such as
"PNR: ...", "Date of Journey: ...", "Seats: ...", and turns a confirmation email into a small structured record.
It is deliberately conservative: promotions, OTPs and "rate your trip" mails are ignored, and when a field
cannot be found it is left out rather than guessed.

Times in tickets are local wall-clock times at the place of travel, and the email does not say which time zone
that is. They are therefore returned as *naive* ISO strings ("2026-10-12T19:30:00", or "2026-10-12" when the
mail gives no time) and shown as written.
"""

import re
from datetime import datetime, timedelta
from typing import Any

import dateparser
from icalendar import Calendar, Event

from .html_text import html_to_text

KINDS = ("flight", "train", "bus", "movie", "event", "hotel")

# --- who sends what --------------------------------------------------------------------------------------

# sender domain -> (kind or None when the sender sells several kinds, display name)
SENDERS: dict[str, tuple[str | None, str]] = {
    "bookmyshow.com": ("movie", "BookMyShow"),
    "pvrcinemas.com": ("movie", "PVR"),
    "pvrinox.com": ("movie", "PVR INOX"),
    "inoxmovies.com": ("movie", "INOX"),
    "cinepolis.com": ("movie", "Cinepolis"),
    "insider.in": ("event", "Paytm Insider"),
    "eventbrite.com": ("event", "Eventbrite"),
    "ticketmaster.com": ("event", "Ticketmaster"),
    "district.in": (None, "District"),
    "irctc.co.in": ("train", "IRCTC"),
    "irctc.com": ("train", "IRCTC"),
    "indianrailways.gov.in": ("train", "Indian Railways"),
    "confirmtkt.com": ("train", "ConfirmTkt"),
    "trainman.in": ("train", "Trainman"),
    "redbus.in": ("bus", "redBus"),
    "abhibus.com": ("bus", "AbhiBus"),
    "goindigo.in": ("flight", "IndiGo"),
    "airindia.com": ("flight", "Air India"),
    "airindia.in": ("flight", "Air India"),
    "airindiaexpress.com": ("flight", "Air India Express"),
    "vistara.com": ("flight", "Vistara"),
    "spicejet.com": ("flight", "SpiceJet"),
    "akasaair.com": ("flight", "Akasa Air"),
    "airasia.co.in": ("flight", "AirAsia"),
    "emirates.com": ("flight", "Emirates"),
    "qatarairways.com": ("flight", "Qatar Airways"),
    "etihad.com": ("flight", "Etihad"),
    "singaporeair.com": ("flight", "Singapore Airlines"),
    "britishairways.com": ("flight", "British Airways"),
    "lufthansa.com": ("flight", "Lufthansa"),
    "turkishairlines.com": ("flight", "Turkish Airlines"),
    "makemytrip.com": (None, "MakeMyTrip"),
    "goibibo.com": (None, "Goibibo"),
    "cleartrip.com": (None, "Cleartrip"),
    "easemytrip.com": (None, "EaseMyTrip"),
    "ixigo.com": (None, "ixigo"),
    "yatra.com": (None, "Yatra"),
    "booking.com": ("hotel", "Booking.com"),
    "agoda.com": ("hotel", "Agoda"),
    "airbnb.com": ("hotel", "Airbnb"),
    "oyorooms.com": ("hotel", "OYO"),
    "treebo.com": ("hotel", "Treebo"),
    "marriott.com": ("hotel", "Marriott"),
    "hilton.com": ("hotel", "Hilton"),
    "tajhotels.com": ("hotel", "Taj Hotels"),
}

AIRLINE_CODES = {
    "6E": "IndiGo", "AI": "Air India", "UK": "Vistara", "SG": "SpiceJet", "QP": "Akasa Air",
    "IX": "Air India Express", "I5": "AirAsia India", "9I": "Alliance Air", "EK": "Emirates",
    "QR": "Qatar Airways", "EY": "Etihad", "SQ": "Singapore Airlines", "BA": "British Airways",
    "LH": "Lufthansa", "TK": "Turkish Airlines", "UA": "United", "DL": "Delta", "AA": "American Airlines",
    "AF": "Air France", "KL": "KLM", "QF": "Qantas", "TG": "Thai Airways", "MH": "Malaysia Airlines",
    "CX": "Cathay Pacific", "AK": "AirAsia", "FZ": "flydubai", "WY": "Oman Air", "G9": "Air Arabia",
    "VS": "Virgin Atlantic", "SV": "Saudia", "GF": "Gulf Air",
}  # fmt: skip

AIRPORTS = {
    "DEL": "Delhi", "BOM": "Mumbai", "BLR": "Bengaluru", "HYD": "Hyderabad", "MAA": "Chennai",
    "CCU": "Kolkata", "GOI": "Goa", "PNQ": "Pune", "AMD": "Ahmedabad", "COK": "Kochi", "JAI": "Jaipur",
    "LKO": "Lucknow", "TRV": "Thiruvananthapuram", "IXC": "Chandigarh", "PAT": "Patna", "GAU": "Guwahati",
    "BBI": "Bhubaneswar", "NAG": "Nagpur", "IDR": "Indore", "SXR": "Srinagar", "ATQ": "Amritsar",
    "VNS": "Varanasi", "RPR": "Raipur", "VTZ": "Visakhapatnam", "IXB": "Bagdogra", "CJB": "Coimbatore",
    "IXM": "Madurai", "UDR": "Udaipur", "BHO": "Bhopal", "IXR": "Ranchi", "DED": "Dehradun", "IXL": "Leh",
    "DXB": "Dubai", "AUH": "Abu Dhabi", "DOH": "Doha", "SIN": "Singapore", "BKK": "Bangkok",
    "KUL": "Kuala Lumpur", "LHR": "London", "LGW": "London", "JFK": "New York", "EWR": "Newark",
    "SFO": "San Francisco", "LAX": "Los Angeles", "ORD": "Chicago", "CDG": "Paris", "FRA": "Frankfurt",
    "AMS": "Amsterdam", "IST": "Istanbul", "HKG": "Hong Kong", "NRT": "Tokyo", "SYD": "Sydney",
    "MEL": "Melbourne", "YYZ": "Toronto", "MLE": "Male", "CMB": "Colombo", "KTM": "Kathmandu",
}  # fmt: skip

# --- classification --------------------------------------------------------------------------------------

KIND_WORDS: dict[str, tuple[str, ...]] = {
    "flight": ("flight", "boarding pass", "airline", "airport", "terminal", "web check-in", "departs", "arrives", "cabin"),
    "train": ("train", "irctc", "berth", "coach", "boarding station", "sleeper", "3ac", "2ac", "1ac", "chair car", "railway", "platform"),
    "bus": ("bus", "boarding point", "dropping point", "operator", "volvo", "redbus"),
    "movie": ("movie", "show time", "showtime", "screen", "audi", "cinema", "multiplex", "pvr", "inox", "bookmyshow"),
    "event": ("event", "concert", "admit", "entry pass", "gates open", "doors open", "stand-up", "festival", "venue"),
    "hotel": ("check-in", "check in", "check-out", "check out", "hotel", "room", "guests", "nights", "property"),
}  # fmt: skip

POSITIVE = re.compile(
    r"(?:booking|ticket|reservation|order|payment)\s+(?:is\s+)?(?:successful|confirm)|confirmed"
    r"|e-?ticket|your tickets?|itinerary|boarding pass|booking id|booking ref|\bpnr\b"
    r"|confirmation\s+(?:no|number|code)|order id|ticket (?:no|id|number)",
    re.IGNORECASE,
)
PROMO = re.compile(
    r"\b(?:offers?|sale|discounts?|cashback|deals?|coupons?|promo(?:code)?|save up to|flat \d+|% off|"
    r"book now|earn|rewards?)\b|\d+% off",
    re.IGNORECASE,
)
NOT_A_BOOKING = re.compile(
    r"\b(?:otp|one[- ]time password|verification code|reset your password|rate your|how was your|"
    r"feedback|survey|review your)\b",
    re.IGNORECASE,
)
CANCELLED = re.compile(
    r"\bcancel(?:l)?ed\b|\bcancellation (?:confirm|successful)|\brefund (?:initiated|processed)",
    re.IGNORECASE,
)
CHANGED = re.compile(
    r"\breschedul|re-schedul|schedule change|flight change|time change|has been changed", re.IGNORECASE
)

# --- dates ------------------------------------------------------------------------------------------------

_MONTHS = r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
_DAYS = r"Mon(?:day)?|Tue(?:s(?:day)?)?|Wed(?:nesday)?|Thu(?:r(?:s(?:day)?)?)?|Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?"
DATE_RE = re.compile(
    rf"""(?:(?:{_DAYS})\.?,?\s+)?(?:
        \d{{1,2}}(?:st|nd|rd|th)?[\s\-/,]+(?:{_MONTHS})\b\.?(?:[\s\-/,]+(?:\d{{4}}|\d{{2}})(?![\d:]))?
      | (?:{_MONTHS})\b\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?
      | \d{{1,2}}[/\-.]\d{{1,2}}[/\-.]\d{{2,4}}
    )""",
    re.IGNORECASE | re.VERBOSE,
)
TIME_COLON = re.compile(r"(?<![\d:.])(\d{1,2}):(\d{2})(?::\d{2})?\s*(AM|PM)?(?!\d)", re.IGNORECASE)
TIME_MERIDIEM = re.compile(r"(?<![\d:.])(\d{1,2})(?:\.(\d{2}))?\s*(AM|PM)\b", re.IGNORECASE)
# Dates that are about the booking itself, not the trip.
META_DATE_LINE = re.compile(
    r"booked on|booking date|date of booking|transaction|issued|generated|payment|invoice|printed",
    re.IGNORECASE,
)

_DATE_SETTINGS = {"DATE_ORDER": "DMY", "PREFER_DATES_FROM": "future", "PREFER_DAY_OF_MONTH": "first"}


def _parse(date_text: str, time_text: str | None, base: datetime | None) -> tuple[datetime | None, bool]:
    settings: dict[str, Any] = dict(_DATE_SETTINGS)
    if base is not None:
        settings["RELATIVE_BASE"] = base.replace(tzinfo=None)
    cleaned = re.sub(r"\b(\d{1,2})(?:st|nd|rd|th)\b", r"\1", date_text)
    parsed = dateparser.parse(
        f"{cleaned} {time_text}" if time_text else cleaned, languages=["en"], settings=settings
    )
    return (parsed.replace(tzinfo=None), bool(time_text)) if parsed else (None, False)


def _time_in(snippet: str) -> str | None:
    match = TIME_COLON.search(snippet)
    if match:
        return match.group(0).strip()
    match = TIME_MERIDIEM.search(snippet)
    return match.group(0).strip() if match else None


def _lines(text: str) -> list[str]:
    return [line for line in text.split("\n") if line.strip()]


def find_when(text: str, labels: tuple[str, ...], base: datetime | None) -> tuple[datetime | None, bool]:
    """The first date (and time, if present) that follows one of the labels, e.g. 'Date of Journey: 12 Oct 2026'."""
    lines = _lines(text)
    for label in labels:
        pattern = re.compile(rf"(?:^|\W){label}\b\W*(.*)$", re.IGNORECASE)
        for i, line in enumerate(lines):
            m = pattern.search(line)
            if not m:
                continue
            # the value is on the same line, or (for tables) on the next one
            snippet = f"{m.group(1)} {lines[i + 1] if i + 1 < len(lines) else ''}"[:140]
            date_match = DATE_RE.search(snippet)
            if not date_match:
                continue
            after = snippet[date_match.end() :]
            when, has_time = _parse(date_match.group(0), _time_in(after) or _time_in(snippet), base)
            if when:
                return when, has_time
    return None, False


def find_any_date(text: str, base: datetime | None) -> tuple[datetime | None, bool]:
    """Fallback: the first date in the mail that is not about the booking itself."""
    lines = _lines(text)
    for i, line in enumerate(lines):
        if META_DATE_LINE.search(line):
            continue
        date_match = DATE_RE.search(line)
        if date_match:
            snippet = f"{line[date_match.end() :]} {lines[i + 1] if i + 1 < len(lines) else ''}"
            when, has_time = _parse(date_match.group(0), _time_in(snippet), base)
            if when:
                return when, has_time
    return None, False


def find_time(text: str, labels: tuple[str, ...]) -> str | None:
    for label in labels:
        m = re.search(rf"(?:^|\W){label}\b\W*(.{{0,40}})", text, re.IGNORECASE | re.MULTILINE)
        if m and _time_in(m.group(1)):
            return _time_in(m.group(1))
    return None


# --- labelled values --------------------------------------------------------------------------------------


def label_value(text: str, labels: tuple[str, ...], max_len: int = 90) -> str | None:
    """'Seats: A1, A2' or a table row 'Seats   A1, A2' or 'Seats' with the value on the next line."""
    joined = "|".join(labels)  # callers list specific labels first ("Seat Numbers" before "Seats")
    inline = re.search(rf"(?im)^[\s>*\-•\d.)]*(?:{joined})\b[ \t]*[:\-–]?[ \t]+(\S.*?)[ \t]*$", text)
    if inline and inline.group(1).strip(" :-–"):
        return inline.group(1).strip()[:max_len]
    below = re.search(rf"(?im)^[\s>*\-•]*(?:{joined})\b[ \t]*[:\-–]?[ \t]*\n[ \t]*(\S.*?)[ \t]*$", text)
    return below.group(1).strip()[:max_len] if below else None


def _place(text: str, labels: tuple[str, ...]) -> str | None:
    """A station or city name for the first label that has one. Values that are really mail header lines
    ('From: <a@b.com>', 'To: <c@d.com>') are skipped, so they never become the journey."""
    lines = text.split("\n")
    for label in labels:
        pattern = re.compile(rf"(?i)^[\s>*\-•]*{label}\b[ \t]*[:\-–]?[ \t]*(\S.*?)?[ \t]*$")
        for i, line in enumerate(lines):
            m = pattern.match(line)
            if not m:
                continue
            value = m.group(1) or (lines[i + 1].strip() if i + 1 < len(lines) else "")  # table layout: next line
            if re.match(r"[\w .&/]{1,30}:", value):  # the "value" is really the next label ("To:", "Date:" ...)
                continue
            if value and not re.search(r"[@<>]", value) and len(value) <= 60:
                return value
    return None


def _first(*values: str | None) -> str | None:
    return next((v for v in values if v), None)


def _no_code(name: str) -> str:
    """'MUMBAI CENTRAL (MMCT)' -> 'MUMBAI CENTRAL'"""
    return re.sub(r"\s*\(.*", "", name).strip()


def _titlecase(name: str) -> str:
    return name.title() if name.isupper() else name


def _sender_domain(sender: str) -> str:
    match = re.search(r"@([\w.\-]+)", sender or "")
    return match.group(1).lower() if match else ""


def _from_sender(sender: str) -> tuple[str | None, str | None]:
    domain = _sender_domain(sender)
    for known, (kind, name) in SENDERS.items():
        if domain == known or domain.endswith("." + known):
            return kind, name
    return None, None


# --- kind-specific extractors -----------------------------------------------------------------------------


def _reference(text: str, kind: str) -> str | None:
    pnr = re.search(r"\bPNR(?:\s*(?:No\.?|Number|#))?\s*[:\-]?\s*([A-Z0-9]{5,10})\b", text, re.IGNORECASE)
    booking = re.search(
        r"(?:Booking\s*(?:ID|Id|Ref(?:erence)?(?:\s*(?:No\.?|Number))?|No\.?|Number)|Confirmation\s*(?:No\.?|Number|Code)|"
        r"Reservation\s*(?:No\.?|Number|ID)|Ticket\s*(?:No\.?|Number|ID)|Order\s*(?:ID|No\.?|Number)|TIN|"
        r"Transaction\s*ID|Trans\s*ID)\s*[:#\-]?\s*([A-Z0-9][A-Z0-9\-/]{4,24})",
        text,
        re.IGNORECASE,
    )
    pick = (pnr, booking) if kind in ("flight", "train") else (booking, pnr)
    for match in pick:
        if match and any(c.isdigit() for c in match.group(1)):
            return match.group(1).upper()
    return None


def _flight(text: str, base) -> dict:
    out: dict = {"details": []}
    number = re.search(r"\b(" + "|".join(AIRLINE_CODES) + r")[\s\-]?(\d{2,4})\b", text)
    labelled = re.search(
        r"Flight(?:\s*(?:No\.?|Number))?\s*[:\-]?\s*([A-Z0-9]{2})[\s\-]?(\d{2,4})\b", text, re.IGNORECASE
    )
    number = number or (labelled if labelled and labelled.group(1).upper() in AIRLINE_CODES else None)
    if number:
        code = number.group(1).upper()
        out["flight_no"] = f"{code} {number.group(2)}"
        out["airline"] = AIRLINE_CODES[code]

    origin = dest = None
    coded = re.search(
        r"([A-Z][A-Za-z ]{2,25}?)\s*\(([A-Z]{3})\)\s*(?:→|➔|->|>|–|—|-|to|TO)\s*([A-Z][A-Za-z ]{2,25}?)\s*\(([A-Z]{3})\)",
        text,
    )
    if coded:
        out["route"] = (
            f"{coded.group(1).strip()} ({coded.group(2)}) → {coded.group(3).strip()} ({coded.group(4)})"
        )
    pair = None if coded else re.search(r"\b([A-Z]{3})\s*(?:→|➔|->|>|–|—|-|to|TO)\s*([A-Z]{3})\b", text)
    if pair and pair.group(1) in AIRPORTS and pair.group(2) in AIRPORTS:
        origin, dest = pair.group(1), pair.group(2)
    if coded:
        pass
    elif origin and dest:
        out["route"] = f"{AIRPORTS[origin]} ({origin}) → {AIRPORTS[dest]} ({dest})"
    else:
        cities = "|".join(sorted(set(AIRPORTS.values()), key=len, reverse=True))
        city_pair = re.search(
            rf"\b({cities})\s*(?:\([A-Z]{{3}}\))?\s*(?:→|➔|->|>|–|—|-|to)\s*({cities})\b", text, re.IGNORECASE
        )
        if city_pair:
            out["route"] = f"{city_pair.group(1).title()} → {city_pair.group(2).title()}"
        else:
            src = _place(text, ("Origin", "Departure City", "Departing from", "From"))
            dst = _place(text, ("Destination", "Arrival City", "Arriving at", "To"))
            if src and dst:
                out["route"] = f"{_titlecase(src)} → {_titlecase(dst)}"

    out["when"] = find_when(
        text,
        (
            "Departure",
            "Departs",
            "Depart",
            "Date of Journey",
            "Journey Date",
            "Travel Date",
            "Flight Date",
            "Onward",
            "Date",
        ),
        base,
    )
    for label, keys in (
        ("Passenger", ("Passenger Name", "Passengers?", "Travell?ers?")),
        ("Seat", ("Seat No\\.?", "Seats?")),
        ("Terminal", ("Departure Terminal", "Terminals?")),
        ("Class", ("Class", "Cabin", "Fare Type")),
        ("Baggage", ("Baggage", "Check-in Baggage")),
    ):
        value = label_value(text, keys)
        if value:
            out["details"].append((label, value))
    return out


def _train(text: str, base) -> dict:
    out: dict = {"details": []}
    tr = re.search(
        r"Train\s*(?:No\.?|Number|Name)?(?:\s*/\s*Name)?\s*[:\-]?\s*(\d{5})\s*[-/–:]?\s*([A-Za-z][A-Za-z .&'\-]{2,40})",
        text,
        re.IGNORECASE,
    )
    if not tr:
        tr = re.search(r"\b(\d{5})\s*[-/–]\s*([A-Z][A-Za-z .&'\-]{2,40})", text)
    if tr:
        out["train_no"] = tr.group(1)
        out["train_name"] = _titlecase(tr.group(2).strip(" -–/"))

    stations = re.search(
        r"([A-Z][A-Za-z .]{2,30}?)\s*\(([A-Z]{2,5})\)\s*(?:→|➔|->|>|–|—|-|to|TO)\s*([A-Z][A-Za-z .]{2,30}?)\s*\(([A-Z]{2,5})\)",
        text,
    )
    if stations:
        out["route"] = f"{_titlecase(stations.group(1).strip())} → {_titlecase(stations.group(3).strip())}"
    else:
        src = _place(text, ("From", "Source", "Boarding Station", "Boarding At", "Boarding"))
        dst = _place(text, ("Reservation Upto", "Destination", "Dest", "To"))
        if src and dst:
            out["route"] = f"{_titlecase(_no_code(src))} → {_titlecase(_no_code(dst))}"

    out["when"] = find_when(
        text,
        ("Date of Journey", "Journey Date", "Scheduled Departure", "Departure", "Boarding Date", "Date"),
        base,
    )
    berth = re.search(
        r"\b(CNF|RAC|WL|GNWL|RLWL|PQWL|RSWL|TQWL)\b\s*/?\s*([A-Z]{1,2}\d{1,2})?\s*/?\s*(\d{1,3})?", text
    )
    if berth:
        out["details"].append(("Status / berth", " / ".join(g for g in berth.groups() if g)))
    for label, keys in (
        ("Class", ("Class", "Travel Class")),
        ("Coach / seat", ("Coach No\\.?", "Coach")),
        ("Passenger", ("Passenger Name", "Passengers?")),
        ("Boarding", ("Boarding Station", "Boarding At")),
    ):
        value = label_value(text, keys)
        if value:
            out["details"].append((label, value))
    return out


def _bus(text: str, base) -> dict:
    out: dict = {"details": []}
    src = _place(text, ("Source", "Origin", "From"))
    dst = _place(text, ("Destination", "To"))
    if src and dst:
        out["route"] = f"{_titlecase(src)} → {_titlecase(dst)}"
    out["operator"] = label_value(text, ("Operator", "Bus Operator", "Travels", "Travel Name"))
    out["when"] = find_when(
        text, ("Date of Journey", "Journey Date", "Travel Date", "Departure", "Boarding Time", "Date"), base
    )
    for label, keys in (
        ("Seats", ("Seat Numbers?", "Seat No\\.?s?", "Seats?")),
        ("Boarding point", ("Boarding Point", "Pickup Point", "Pick-up Point")),
        ("Dropping point", ("Dropping Point", "Drop Point")),
        ("Passenger", ("Passenger Name", "Passengers?")),
    ):
        value = label_value(text, keys)
        if value:
            out["details"].append((label, value))
    return out


_MOVIE_SUBJECT = (
    r"tickets?\s+for\s+[\"“']?(.+?)[\"”']?\s*(?:\(|\[|\bat\b|\bon\b|\bis\b|\||—|–|$)",
    r"booking\s+(?:confirmed|confirmation)\s*[:\-–|]\s*[\"“']?(.+?)[\"”']?\s*(?:\(|\[|\bat\b|\bon\b|\||—|–|$)",
    r"(?:booking|order)\s+for\s+[\"“']?(.+?)[\"”']?\s*(?:\(|\[|\bat\b|\bon\b|\||—|–|$)",
    r"\b(?:movie|show|event)\s*[:\-–]\s*[\"“']?(.+?)[\"”']?$",
)


def _title_from(subject: str, text: str, labels: tuple[str, ...]) -> str | None:
    labelled = label_value(text, labels)
    if labelled:
        return labelled
    for pattern in _MOVIE_SUBJECT:
        m = re.search(pattern, subject, re.IGNORECASE)
        if m and 2 <= len(m.group(1).strip()) <= 80:
            return m.group(1).strip(" -–|:\"'")
    return None


_NOT_A_TITLE = re.compile(
    r"(?i)^(?:booking|ticket|order|payment|amount|screen|seats?|date|time|show|hi|hello|dear|thank|your|"
    r"confirm|e-?ticket|total|qty|quantity|category|from|to|subject)|@|https?:|^\W*$"
)


def _title_above(text: str, venue: str) -> str | None:
    """Ticket mails (BookMyShow and the like) print the movie or event name on the line just above the
    cinema and date lines, with no 'Movie:' label. Take the nearest plain line above the venue."""
    lines = _lines(text)
    try:
        at = next(i for i, line in enumerate(lines) if venue[:30] in line)
    except StopIteration:
        return None
    for line in reversed(lines[max(0, at - 3) : at]):
        line = line.strip(" -–|:\"'")
        if 2 <= len(line) <= 80 and not _NOT_A_TITLE.search(line) and not DATE_RE.search(line):
            return line
    return None


def _movie_or_event(text: str, subject: str, base, kind: str) -> dict:
    out: dict = {"details": []}
    out["title"] = _title_from(
        subject, text, ("Movie(?: Name)?", "Event(?: Name)?", "Show(?: Name)?", "Title")
    )
    venue = label_value(text, ("Cinema", "Theatre", "Theater", "Venue", "Location"))
    if not venue:
        m = re.search(
            r"^.*\b(?:PVR|INOX|Cinepolis|Carnival|Miraj|Multiplex|Cinemas?)\b.*$",
            text,
            re.IGNORECASE | re.MULTILINE,
        )
        venue = m.group(0).strip()[:90] if m else None
    out["venue"] = venue
    if not out["title"] and venue:
        out["title"] = _title_above(text, venue)

    when, has_time = find_when(
        text,
        ("Show Time", "Showtime", "Show Date", "Date & Time", "Date and Time", "Event Date", "Date"),
        base,
    )
    if when and not has_time:
        t = find_time(
            text, ("Show Time", "Showtime", "Time", "Timing", "Doors Open", "Gates Open", "Start Time")
        )
        if t:
            when, has_time = _parse(when.strftime("%d %b %Y"), t, base)
    if not when:
        when, has_time = find_any_date(text, base)
    out["when"] = (when, has_time)

    for label, keys in (
        ("Seats", ("Seat No\\.?s?", "Seats?")),
        ("Screen", ("Screen", "Audi", "Auditorium")),
        ("Tickets", ("No\\.? of Tickets", "Quantity", "Tickets?")),
        ("Category", ("Category", "Ticket Type")),
    ):
        value = label_value(text, keys)
        if label == "Tickets" and value and not re.search(r"\d", value):
            value = None  # the "TICKETS  AMOUNT" table header, not a count
        if value:
            out["details"].append((label, value))
    return out


def _hotel(text: str, subject: str, base) -> dict:
    out: dict = {"details": []}
    name = label_value(text, ("Hotel(?: Name)?", "Property(?: Name)?", "Accommodation"))
    if not name:
        m = re.search(
            r"\b(?:at|stay at|reservation at|booking at)\s+(.+?)(?:\s*[-–|(]|$)", subject, re.IGNORECASE
        )
        name = m.group(1).strip() if m else None
    out["name"] = name
    check_in, _ = find_when(text, ("Check[- ]?in", "Arrival"), base)
    check_out, _ = find_when(text, ("Check[- ]?out", "Departure"), base)
    out["when"] = (check_in, False)
    out["end"] = check_out
    for label, keys in (
        ("Guests", ("Guest Name", "Guests?", "Adults?")),
        ("Rooms", ("Room Type", "Rooms?")),
        ("Address", ("Address", "Location")),
    ):
        value = label_value(text, keys)
        if value:
            out["details"].append((label, value))
    return out


# --- main entry point -------------------------------------------------------------------------------------


def _detect_kind(text: str, sender_kind: str | None) -> str | None:
    if sender_kind:
        return sender_kind
    lower = text.lower()
    scores = {k: sum(lower.count(word) for word in words) for k, words in KIND_WORDS.items()}
    if re.search(r"\b[A-Z]{3}\s*(?:→|->|to)\s*[A-Z]{3}\b", text):
        scores["flight"] += 2
    best, score = max(scores.items(), key=lambda kv: kv[1])
    return best if score >= 2 else None


def _iso(value: datetime | None, has_time: bool) -> str | None:
    if value is None:
        return None
    return value.strftime("%Y-%m-%dT%H:%M:%S") if has_time else value.strftime("%Y-%m-%d")


def extract_booking(email: dict) -> dict | None:
    """One booking from one email dict (id, sender, subject, body, date, category), or None."""
    subject = (email.get("subject") or "").strip()
    sender_kind, provider = _from_sender(email.get("sender", ""))
    raw_body = email.get("body") or ""
    if provider is None and not POSITIVE.search(f"{subject}\n{raw_body[:3000]}"):
        return None  # nothing that looks like a booking: skip the costlier HTML conversion
    text = html_to_text(f"{subject}\n{raw_body}")
    if not text:
        return None
    kind = _detect_kind(text, sender_kind)
    if kind is None:
        return None

    reference = _reference(text, kind)
    positive = bool(POSITIVE.search(text))
    if not (reference or positive):
        return None
    if PROMO.search(subject) and not reference:
        return None
    if NOT_A_BOOKING.search(subject) and not reference:
        return None
    if email.get("category") == "Promotional" and not reference:
        return None

    base = email.get("date") if isinstance(email.get("date"), datetime) else None
    head = f"{subject}\n{text[:300]}"
    status = (
        "cancelled"
        if CANCELLED.search(subject) or CANCELLED.search(text[:300])
        else "changed"
        if CHANGED.search(head)
        else "confirmed"
    )

    if kind == "flight":
        parts = _flight(text, base)
        title = parts.get("route") or "Flight"
        subtitle = " · ".join(v for v in (parts.get("airline"), parts.get("flight_no")) if v) or None
        provider = provider or parts.get("airline")
    elif kind == "train":
        parts = _train(text, base)
        title = parts.get("route") or parts.get("train_name") or "Train journey"
        subtitle = " ".join(v for v in (parts.get("train_no"), parts.get("train_name")) if v) or None
        provider = provider or "Railways"
    elif kind == "bus":
        parts = _bus(text, base)
        title = parts.get("route") or "Bus journey"
        subtitle = parts.get("operator")
    elif kind in ("movie", "event"):
        parts = _movie_or_event(text, subject, base, kind)
        title = parts.get("title") or ("Movie" if kind == "movie" else "Event")
        subtitle = parts.get("venue")
    else:
        parts = _hotel(text, subject, base)
        title = parts.get("name") or "Hotel stay"
        subtitle = None

    when, has_time = parts.get("when") or (None, False)
    details = [{"label": label, "value": value} for label, value in parts.get("details", [])]

    found = sum(
        bool(x)
        for x in (
            reference,
            when,
            subtitle,
            details,
            title not in ("Flight", "Movie", "Event", "Hotel stay", "Train journey", "Bus journey"),
        )
    )
    return {
        "email_id": email["id"],
        "kind": kind,
        "provider": provider or "Booking",
        "title": title[:120],
        "subtitle": subtitle[:120] if subtitle else None,
        "start_at": _iso(when, has_time),
        "all_day": bool(when) and not has_time,
        "end_at": _iso(parts.get("end"), False),
        "reference": reference,
        "status": status,
        "details": details,
        "email_subject": subject,
        "email_date": email["date"].isoformat() if isinstance(email.get("date"), datetime) else None,
        "confidence": round(found / 5, 2),
    }


def extract_bookings(emails) -> list[dict]:
    """Every booking found in `emails`. When one trip produces several mails (confirmation, then a change or
    cancellation) with the same reference, only the newest is kept."""
    found: dict[str, dict] = {}
    for email in emails:
        booking = extract_booking(email)
        if booking is None:
            continue
        key = f"{booking['kind']}:{booking['reference']}" if booking["reference"] else booking["email_id"]
        current = found.get(key)
        if current is None or (booking["email_date"] or "") >= (current["email_date"] or ""):
            found[key] = booking
    return sorted(found.values(), key=lambda b: (b["start_at"] is None, b["start_at"] or "", b["email_id"]))


# --- calendar file ------------------------------------------------------------------------------------------

_DEFAULT_LENGTH = {"movie": timedelta(hours=3), "flight": timedelta(hours=2)}


def booking_to_ics(booking: dict) -> bytes | None:
    """A one-event .ics for a booking (floating local times, since tickets do not state a time zone)."""
    if not booking.get("start_at"):
        return None
    cal = Calendar()
    cal.add("prodid", "-//Email Prioritizer//Bookings//EN")
    cal.add("version", "2.0")

    event = Event()
    event.add("uid", f"booking-{booking['email_id']}@email-prioritizer")
    event.add("dtstamp", datetime.now())
    icon = {
        "flight": "Flight",
        "train": "Train",
        "bus": "Bus",
        "movie": "Movie",
        "event": "Event",
        "hotel": "Hotel",
    }[booking["kind"]]
    event.add("summary", f"{icon}: {booking['title']}")
    lines = [booking["subtitle"]] if booking.get("subtitle") else []
    if booking.get("reference"):
        lines.append(f"Reference: {booking['reference']}")
    lines += [f"{d['label']}: {d['value']}" for d in booking.get("details", [])]
    if lines:
        event.add("description", "\n".join(lines))
    if booking["kind"] in ("movie", "event") and booking.get("subtitle"):
        event.add("location", booking["subtitle"])

    start = datetime.fromisoformat(booking["start_at"])
    if booking["all_day"]:
        event.add("dtstart", start.date())
        end = (
            datetime.fromisoformat(booking["end_at"]).date()
            if booking.get("end_at")
            else start.date() + timedelta(days=1)
        )
        event.add("dtend", end)
    else:
        event.add("dtstart", start)
        event.add("dtend", start + _DEFAULT_LENGTH.get(booking["kind"], timedelta(hours=1)))
    cal.add_component(event)
    return cal.to_ical()
