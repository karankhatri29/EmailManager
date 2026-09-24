"""The daily briefing: what needs attention today, across every connected mailbox."""

import html
import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Activity, User, UserSettings
from ..repositories import emails as emails_repo
from ..repositories import followups as followups_repo
from ..repositories import timetable as timetable_repo
from . import ai_summarizer
from .followups import waiting_days
from .timetable import time_range

logger = logging.getLogger(__name__)

UPCOMING_DAYS = 3
OVERDUE_LOOKBACK_DAYS = 30
TOP_EMAILS = 5
PROMO_SUBJECTS_FOR_AI = 15
MAX_LIST = 8

PROMO_PROMPT = """These are the subjects of promotional emails one person received today.
In one or two short sentences, point out the 2 or 3 offers that look most worthwhile (real savings on things
people commonly buy) and ignore the fluff. Do not invent details that are not in the subjects. No introduction.

{lines}"""


def valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False


def _tz(name: str) -> ZoneInfo:
    return ZoneInfo(name) if valid_timezone(name) else ZoneInfo("UTC")


def local_now(settings: UserSettings, now: datetime | None = None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(_tz(settings.timezone))


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def activity_day(activity: Activity, tz: ZoneInfo) -> date:
    """All-day items are dates (stored as midnight UTC); timed ones are instants in the user's own day."""
    if activity.start_at is None:
        raise ValueError("activity has no start time")
    start = _aware(activity.start_at)
    return start.date() if activity.all_day else start.astimezone(tz).date()


def greeting(hour: int) -> str:
    return "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"


def _promo_highlights(subjects: list[str], use_ai: bool = True) -> str | None:
    if not subjects:
        return None
    try:
        if not use_ai or not ai_summarizer.is_configured():
            raise RuntimeError("AI not used")
        text = ai_summarizer.generate_text(PROMO_PROMPT.format(lines="\n".join(f"- {s}" for s in subjects)))
        if text:
            return text
    except Exception:
        logger.info("Promotions highlight unavailable; listing subjects instead", exc_info=True)
    return "Top subjects: " + "; ".join(subjects[:3])


def build_briefing(
    db: Session, user: User, settings: UserSettings, now: datetime | None = None, use_ai: bool = True
) -> dict:
    """Today's briefing. use_ai=False skips the AI sentence about promotions (for screens that must be quick)."""
    now = now or datetime.now(timezone.utc)
    tz = _tz(settings.timezone)
    today = now.astimezone(tz).date()

    # --- calendar items ---
    horizon_start = datetime.combine(
        today - timedelta(days=OVERDUE_LOOKBACK_DAYS + 1), datetime.min.time(), tzinfo=timezone.utc
    )
    horizon_end = datetime.combine(
        today + timedelta(days=UPCOMING_DAYS + 2), datetime.min.time(), tzinfo=timezone.utc
    )
    rows = db.scalars(
        select(Activity)
        .where(
            Activity.user_id == user.id,
            Activity.status != "done",
            Activity.start_at.is_not(None),
            Activity.start_at >= horizon_start,
            Activity.start_at < horizon_end,
        )
        .order_by(Activity.start_at)
    )
    overdue, due_today, upcoming = [], [], []
    for activity in rows:
        if activity.start_at is None:
            continue
        day = activity_day(activity, tz)
        item = {
            "id": activity.id,
            "title": activity.title,
            "due": day.isoformat(),
            "all_day": activity.all_day,
            "time": None if activity.all_day else _aware(activity.start_at).astimezone(tz).strftime("%H:%M"),
        }
        if day < today:
            overdue.append({**item, "days_overdue": (today - day).days})
        elif day == today:
            due_today.append(item)
        elif day <= today + timedelta(days=UPCOMING_DAYS):
            upcoming.append(item)

    # --- mail from the last 24 hours ---
    recent = emails_repo.list_in_window(db, user.id, 1, now=now)
    top = sorted(
        (
            e
            for e in recent
            if e.category in ("Urgent / Action Required", "Important") and emails_repo.is_open(e, now)
        ),
        key=lambda e: -e.score,
    )[:TOP_EMAILS]
    promos = [e for e in recent if e.category == "Promotional"]
    sender_counts: dict[str, int] = {}
    for e in promos:
        sender_counts[e.sender_address or e.sender] = sender_counts.get(e.sender_address or e.sender, 0) + 1

    waiting = followups_repo.list_for_user(db, user.id, followups_repo.WAITING)
    return {
        "date": today.isoformat(),
        "greeting": greeting(now.astimezone(tz).hour),
        "overdue": overdue[:MAX_LIST],
        "today": due_today[:MAX_LIST],
        "upcoming": upcoming[:MAX_LIST],
        "top_emails": [
            {"id": e.id, "sender": e.sender, "subject": e.subject, "category": e.category, "reason": e.reason}
            for e in top
        ],
        "promotions": {
            "count": len(promos),
            "top_senders": [
                {"sender": s, "count": c} for s, c in sorted(sender_counts.items(), key=lambda kv: -kv[1])[:3]
            ],
            "highlights": _promo_highlights([e.subject for e in promos[:PROMO_SUBJECTS_FOR_AI]], use_ai),
        },
        "classes": [
            {"title": s.title, "time": time_range(s), "room": s.room}
            for s in timetable_repo.slots_on(db, user.id, today)
        ],
        "waiting": [
            {"id": f.id, "recipient": f.recipient, "subject": f.subject, "days": waiting_days(f, now)}
            for f in waiting[:MAX_LIST]
        ],
        "counts": {
            "emails": len(recent),
            "urgent": sum(1 for e in recent if e.category == "Urgent / Action Required"),
            "promotional": len(promos),
        },
    }


def is_empty(briefing: dict) -> bool:
    return not (
        briefing["overdue"]
        or briefing["today"]
        or briefing["upcoming"]
        or briefing["top_emails"]
        or briefing["promotions"]["count"]
        or briefing["waiting"]
        or briefing["classes"]
    )


# --- rendering (for the email version) -----------------------------------------------------------


def _line(item: dict) -> str:
    when = f" at {item['time']}" if item.get("time") else ""
    late = f" ({item['days_overdue']}d overdue)" if item.get("days_overdue") else ""
    return f"{item['title']}{when}{late}"


def render_text(b: dict) -> str:
    out = [f"{b['greeting']}! Here is your briefing for {b['date']}.", ""]
    sections = [
        ("Overdue", [_line(i) for i in b["overdue"]]),
        (
            "Classes today",
            [f"{c['time']}  {c['title']}" + (f" ({c['room']})" if c["room"] else "") for c in b["classes"]],
        ),
        ("Due today", [_line(i) for i in b["today"]]),
        ("Coming up", [f"{i['due']}: {_line(i)}" for i in b["upcoming"]]),
        ("Needs your attention", [f"{e['subject']} — {e['sender']}" for e in b["top_emails"]]),
        ("Waiting on a reply", [f"{w['subject']} ({w['recipient']}, {w['days']}d)" for w in b["waiting"]]),
    ]
    for title, lines in sections:
        if lines:
            out += [title.upper(), *[f"  • {line}" for line in lines], ""]
    promos = b["promotions"]
    if promos["count"]:
        out += [
            f"PROMOTIONS ({promos['count']} today, kept out of your way)",
            f"  {promos['highlights'] or ''}",
            "",
        ]
    if is_empty(b):
        out.append("Nothing needs your attention. Enjoy the quiet.")
    return "\n".join(out).strip() + "\n"


def render_html(b: dict) -> str:
    e = html.escape

    def section(title: str, lines: list[str]) -> str:
        if not lines:
            return ""
        items = "".join(f"<li style='margin:4px 0'>{line}</li>" for line in lines)
        return f"<h3 style='margin:18px 0 6px;font-size:14px;color:#334155'>{e(title)}</h3><ul style='padding-left:18px;margin:0'>{items}</ul>"

    def when(item: dict) -> str:
        time_part = f" <span style='color:#64748b'>at {e(item['time'])}</span>" if item.get("time") else ""
        late = (
            f" <span style='color:#dc2626'>({item['days_overdue']}d overdue)</span>"
            if item.get("days_overdue")
            else ""
        )
        return f"{e(item['title'])}{time_part}{late}"

    promos = b["promotions"]
    body = "".join(
        [
            section("Overdue", [when(i) for i in b["overdue"]]),
            section(
                "Classes today",
                [
                    f"{e(c['time'])} {e(c['title'])}" + (f" ({e(c['room'])})" if c["room"] else "")
                    for c in b["classes"]
                ],
            ),
            section("Due today", [when(i) for i in b["today"]]),
            section("Coming up", [f"{e(i['due'])}: {when(i)}" for i in b["upcoming"]]),
            section(
                "Needs your attention",
                [
                    f"{e(m['subject'])} <span style='color:#64748b'>— {e(m['sender'])}</span>"
                    for m in b["top_emails"]
                ],
            ),
            section(
                "Waiting on a reply",
                [
                    f"{e(w['subject'])} <span style='color:#64748b'>({e(w['recipient'])}, {w['days']}d)</span>"
                    for w in b["waiting"]
                ],
            ),
            section(
                f"Promotions ({promos['count']} today, kept out of your way)", [e(promos["highlights"] or "")]
            )
            if promos["count"]
            else "",
            "<p style='color:#64748b'>Nothing needs your attention. Enjoy the quiet.</p>"
            if is_empty(b)
            else "",
        ]
    )
    return (
        "<div style='font-family:system-ui,sans-serif;max-width:560px;margin:auto;color:#0f172a'>"
        f"<h2 style='margin-bottom:2px'>{e(b['greeting'])}!</h2><div style='color:#64748b'>Your briefing for {e(b['date'])}</div>{body}</div>"
    )
