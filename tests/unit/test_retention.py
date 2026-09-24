"""Old stored mail is deleted automatically so a small hosted database does not fill up."""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from app.core.config import Settings, get_settings
from app.db.models import Activity, Email, ThreadSummary
from app.repositories import emails as emails_repo
from app.services import jobs
from tests.conftest import stored_email

NOW = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)


def ago(days):
    return NOW - timedelta(days=days)


def test_settings_floor_and_off_switch():
    assert Settings(retention_days=180).retention_days == 180
    assert Settings(retention_days=5).retention_days == 30  # too short to be sensible
    assert Settings(retention_days=0).retention_days == 0 and Settings(retention_days=-1).retention_days == 0


def test_only_mail_older_than_the_limit_is_deleted(db, account):
    emails_repo.upsert_many(
        db,
        [
            stored_email(account, "old", date=ago(200)),
            stored_email(account, "edge", date=ago(179)),
            stored_email(account, "new", date=ago(1)),
        ],
    )
    assert emails_repo.delete_older_than(db, 180, NOW) == 1
    assert {e.id.split(":")[1] for e in db.query(Email)} == {"edge", "new"}


def test_old_mail_with_a_date_still_ahead_is_kept(db, account):
    """A flight booked long ago must survive until the trip."""
    emails_repo.upsert_many(
        db,
        [
            stored_email(account, "trip", date=ago(300), due_date=NOW.date() + timedelta(days=20)),
            stored_email(account, "past", date=ago(300), due_date=date(2025, 12, 1)),
        ],
    )
    emails_repo.delete_older_than(db, 180, NOW)
    assert {e.id.split(":")[1] for e in db.query(Email)} == {"trip"}


def test_calendar_items_survive_but_lose_their_link_to_the_deleted_mail(db, user, account):
    emails_repo.upsert_many(db, [stored_email(account, "old", date=ago(400))])
    db.add(Activity(user_id=user.id, title="Pay fees", email_id=f"{account.id}:old", source="email"))
    db.commit()
    emails_repo.delete_older_than(db, 180, NOW)
    db.expire_all()
    item = db.query(Activity).one()
    assert item.title == "Pay fees" and item.email_id is None


def test_the_job_deletes_old_mail_and_summaries_and_can_be_switched_off(db, user, account):
    emails_repo.upsert_many(db, [stored_email(account, "old", date=ago(400))])
    db.add(
        ThreadSummary(user_id=user.id, thread_key="1:t", message_count=2, summary="s", created_at=ago(400))
    )
    db.commit()

    off = get_settings().model_copy(update={"retention_days": 0})
    with patch.object(jobs, "get_settings", return_value=off):
        assert jobs.enforce_retention(db, NOW) == 0
    assert db.query(Email).count() == 1

    on = get_settings().model_copy(update={"retention_days": 90})
    with patch.object(jobs, "get_settings", return_value=on):
        assert jobs.enforce_retention(db, NOW) == 1
    assert db.query(Email).count() == 0 and db.query(ThreadSummary).count() == 0
