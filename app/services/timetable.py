"""Helpers for the student timetable."""

from ..db.models import ClassSlot

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
PALETTE = [
    "#3b82f6",
    "#10b981",
    "#f59e0b",
    "#ef4444",
    "#8b5cf6",
    "#ec4899",
    "#14b8a6",
    "#f97316",
    "#6366f1",
    "#84cc16",
]


def default_color(title: str) -> str:
    """The same course always gets the same colour, without the student having to pick one."""
    return PALETTE[sum(ord(c) for c in title.strip().lower()) % len(PALETTE)]


def describe(slot: ClassSlot) -> str:
    return f"{slot.title} on {WEEKDAY_NAMES[slot.weekday]} {slot.start_time:%H:%M}-{slot.end_time:%H:%M}"


def conflict_message(slot: ClassSlot) -> str:
    return f"That time overlaps with {describe(slot)}."


def time_range(slot: ClassSlot) -> str:
    return f"{slot.start_time:%H:%M}-{slot.end_time:%H:%M}"
