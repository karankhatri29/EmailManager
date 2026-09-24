from datetime import date

from app.db.models import Email
from app.services.scheduler import build_schedule
from tests.conftest import stored_email

URGENT = "Urgent / Action Required"
TODAY = date(2026, 9, 24)


def _row(account, mid, **fields):
    return Email(**stored_email(account, mid, category=fields.pop("category", URGENT), **fields))


def test_empty():
    assert build_schedule([], TODAY) == []


def test_promotional_and_general_are_left_out(account):
    rows = [_row(account, "1", category="Promotional"), _row(account, "2", category="General")]
    assert build_schedule(rows, TODAY) == []


def test_order_is_near_deadline_then_later_or_event_then_asap_then_undated(account):
    rows = [
        _row(account, "none", score=4.7),
        _row(account, "asap", due_kind="asap", score=4.1),
        _row(account, "later", due_kind="deadline", due_date=date(2026, 12, 1), score=4.0),
        _row(account, "event", due_kind="event", due_date=date(2026, 10, 1), score=4.0),
        _row(account, "soon", due_kind="deadline", due_date=date(2026, 9, 27), score=3.2),
        _row(account, "late", due_kind="deadline", due_date=date(2026, 9, 20), score=3.2),
    ]
    out = build_schedule(rows, TODAY)
    assert [t["id"].split(":")[1] for t in out] == ["late", "soon", "event", "later", "asap", "none"]
    assert [t["sort_tier"] for t in out] == [1, 1, 2, 2, 3, 4]
    assert out[0]["deadline"] == "Overdue by 4 days" and out[1]["deadline"] == "Due Sun 27 Sep"
    assert out[-1]["deadline"] == "No date" and out[-1]["deadline_kind"] is None
