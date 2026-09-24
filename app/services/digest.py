"""The evening promotions digest: one message instead of a day of promotional mail.

"You received 42 promotional emails today; the best deals were 40% off running shoes (Nike) and ..."

The deals are picked from the subjects only. The AI, when available, chooses which numbered subjects look
best and can name nothing that is not in the list; without it a plain scoring of the subjects (percentages,
amounts, "free", "sale") does the picking, so the digest never depends on the AI being up.
"""

import html
import logging
import re
from datetime import datetime, time, timezone

from sqlalchemy.orm import Session

from ..db.models import Email, User, UserSettings
from ..repositories import emails as emails_repo
from . import ai_summarizer
from .briefing import _tz, local_now
from .senders import sender_name

logger = logging.getLogger(__name__)

PROMOTIONAL = "Promotional"
MAX_DEALS = 3
SUBJECTS_FOR_AI = 40

_PERCENT = re.compile(r"(\d{1,3})\s?%\s*(?:off|discount|savings?)?", re.IGNORECASE)
_MONEY = re.compile(r"[$€£₹]\s?\d[\d,.]*|\b\d[\d,.]*\s?(?:usd|eur|gbp|inr)\b", re.IGNORECASE)
_WORDS = re.compile(
    r"\b(?:free|sale|save|saving|deal|discount|off|coupon|bogo|clearance|lowest|last chance|ends|today only|"
    r"gift|bonus|cashback|extra)\b",
    re.IGNORECASE,
)
_ORDER_PROMPT = """These are the subjects of promotional emails one person received today, numbered.
Reply with only the numbers (at most 3, comma separated, best first) of the offers that look most worthwhile:
real savings on things people commonly buy. Prefer different senders. Reply with numbers only.

{lines}"""


def deal_score(subject: str) -> float:
    """How much a subject reads like a concrete offer (a percentage, an amount, a sale word)."""
    score = 0.0
    percents = [int(m) for m in _PERCENT.findall(subject) if int(m) <= 100]
    if percents:
        score += 2 + max(percents) / 50
    if _MONEY.search(subject):
        score += 1
    score += 0.5 * min(len(_WORDS.findall(subject)), 3)
    return score


def _pick_by_score(promos: list[Email]) -> list[Email]:
    ranked = sorted(promos, key=lambda e: -deal_score(e.subject))
    picked: list[Email] = []
    for e in ranked:
        if deal_score(e.subject) <= 0 or any(p.sender_address == e.sender_address for p in picked):
            continue
        picked.append(e)
        if len(picked) == MAX_DEALS:
            break
    return picked


def _pick_by_ai(promos: list[Email]) -> list[Email] | None:
    """The AI's choice among the numbered subjects, or None if it is unavailable or answers nonsense."""
    pool = promos[:SUBJECTS_FOR_AI]
    try:
        answer = ai_summarizer.generate_text(
            _ORDER_PROMPT.format(lines="\n".join(f"{n}. {e.subject}" for n, e in enumerate(pool, 1)))
        )
    except Exception:
        logger.info("Deal picking by AI unavailable; scoring subjects instead", exc_info=True)
        return None
    picked: list[Email] = []
    for number in re.findall(r"\d+", answer or ""):
        index = int(number) - 1
        if 0 <= index < len(pool) and pool[index] not in picked:
            picked.append(pool[index])
    return picked[:MAX_DEALS] or None


def promos_today(db: Session, user_id: int, settings: UserSettings, now: datetime) -> list[Email]:
    """Promotional mail received since local midnight, newest first."""
    tz = _tz(settings.timezone)
    local = now.astimezone(tz)
    midnight = datetime.combine(local.date(), time.min, tzinfo=tz).astimezone(timezone.utc)
    rows = emails_repo.list_in_window(db, user_id, 2, now=now)
    return [
        e
        for e in rows
        if e.category == PROMOTIONAL
        and (e.date if e.date.tzinfo else e.date.replace(tzinfo=timezone.utc)) >= midnight
    ]


def build_digest(db: Session, user: User, settings: UserSettings, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    promos = promos_today(db, user.id, settings, now)
    deals = (_pick_by_ai(promos) if promos else None) or _pick_by_score(promos)
    senders: dict[str, int] = {}
    for e in promos:
        name = sender_name(e.sender)
        senders[name] = senders.get(name, 0) + 1
    result = {
        "date": local_now(settings, now).date().isoformat(),
        "count": len(promos),
        "deals": [{"id": e.id, "sender": sender_name(e.sender), "subject": e.subject} for e in deals],
        "top_senders": [
            {"sender": s, "count": c} for s, c in sorted(senders.items(), key=lambda kv: -kv[1])[:3]
        ],
    }
    result["headline"] = headline(result)
    return result


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def headline(d: dict) -> str:
    n = d["count"]
    if n == 0:
        return "No promotional emails today."
    text = f"You received {n} promotional email{'s' if n != 1 else ''} today"
    if d["deals"]:
        deals = [f'"{x["subject"]}" from {x["sender"]}' for x in d["deals"]]
        return f"{text}; the best deal{'s were' if len(deals) > 1 else ' was'} {_join(deals)}."
    return text + "; nothing stood out."


def render_text(d: dict) -> str:
    lines = [d["headline"], ""]
    if d["top_senders"]:
        lines.append(
            "Most emails from: " + ", ".join(f"{s['sender']} ({s['count']})" for s in d["top_senders"])
        )
    lines.append("They stay out of your inbox view; open the Promotions filter if you want to look.")
    return "\n".join(lines) + "\n"


def render_html(d: dict) -> str:
    e = html.escape
    deals = "".join(
        f"<li style='margin:4px 0'>{e(x['subject'])} <span style='color:#64748b'>({e(x['sender'])})</span></li>"
        for x in d["deals"]
    )
    senders = ", ".join(f"{e(s['sender'])} ({s['count']})" for s in d["top_senders"])
    return (
        f"<div style='font-family:system-ui,sans-serif;max-width:560px'><h2 style='font-size:16px'>{e(d['headline'])}</h2>"
        + (
            f"<h3 style='font-size:14px;color:#334155'>Best deals</h3><ul style='padding-left:18px'>{deals}</ul>"
            if deals
            else ""
        )
        + (f"<p style='color:#64748b;font-size:13px'>Most emails from: {senders}</p>" if senders else "")
        + "</div>"
    )


def is_due(settings: UserSettings, now: datetime) -> bool:
    local = local_now(settings, now)
    return local.hour >= settings.digest_hour and settings.digest_last_sent != local.date()
