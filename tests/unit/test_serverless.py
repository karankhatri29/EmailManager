"""Hosting without background threads (Vercel): syncs run inside the request, a scheduled call keeps mail fresh."""

from unittest.mock import patch

from app.core.config import Settings, get_settings
from app.services import background
from app.services.background import SyncManager


def test_hosted_postgres_urls_get_the_psycopg_driver():
    for given in ("postgres://u:p@h/db?sslmode=require", "postgresql://u:p@h/db?sslmode=require"):
        assert Settings(database_url=given).database_url == "postgresql+psycopg://u:p@h/db?sslmode=require"
    assert Settings(database_url="sqlite:///x.db").database_url == "sqlite:///x.db"
    same = "postgresql+psycopg://u:p@h/db"
    assert Settings(database_url=same).database_url == same


def test_vercel_is_detected_from_its_environment_variable(monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    assert not Settings().is_serverless
    monkeypatch.setenv("VERCEL", "1")
    assert Settings().is_serverless


def test_inline_manager_only_syncs_when_asked_and_does_it_before_returning(db, account):
    manager = SyncManager(inline=True)
    with patch.object(background, "sync_account", return_value=3) as sync:
        assert manager.trigger(account.id, "Last 1 Day") is False  # the "looks stale" trigger does nothing
        sync.assert_not_called()
        assert manager.trigger(account.id, "Last 1 Day", wait=True) is True
        sync.assert_called_once()
    status = manager.status([account.id])
    assert status["syncing"] is False and status["new_emails"] == 3 and status["finished_at"]


def test_cron_endpoint_is_absent_without_a_secret_and_rejects_a_wrong_one(client):
    without = get_settings().model_copy(update={"cron_secret": ""})  # whatever the developer's .env sets
    with patch("app.api.cron.get_settings", return_value=without):
        assert client.get("/api/cron/run").status_code == 404
    configured = get_settings().model_copy(update={"cron_secret": "s3cret"})
    with patch("app.api.cron.get_settings", return_value=configured):
        assert client.get("/api/cron/run").status_code == 401
        assert client.get("/api/cron/run", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_cron_endpoint_syncs_and_runs_the_jobs_with_the_right_secret(client):
    configured = get_settings().model_copy(update={"cron_secret": "s3cret"})
    with (
        patch("app.api.cron.get_settings", return_value=configured),
        patch.object(
            background, "run_scheduled", return_value={"synced": 2, "skipped": 0, "jobs": {}}
        ) as run,
    ):
        r = client.get("/api/cron/run", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200 and r.json()["synced"] == 2
    run.assert_called_once_with(budget_seconds=configured.cron_budget_seconds)


def test_scheduled_run_syncs_oldest_first_and_stops_at_the_time_budget(db, user):
    from app.db.models import MailAccount

    ids = []
    for n in range(3):
        a = MailAccount(user_id=user.id, provider="google", email_address=f"m{n}@x.com", credentials="c")
        db.add(a)
        db.flush()
        ids.append(a.id)
    db.commit()
    order: list[int] = []
    manager = SyncManager(inline=True)
    with (
        patch.object(background, "sync_manager", manager),
        patch.object(background, "sync_account", side_effect=lambda _db, acc, _tf: order.append(acc.id) or 1),
        patch.object(background.jobs, "run_periodic_jobs", return_value={}),
    ):
        done = background.run_scheduled()
        assert done["synced"] == 3 and sorted(order) == sorted(ids)
        order.clear()
        clock = iter([0, 0, 100, 100, 100, 100])  # the budget runs out after the first mailbox
        with patch.object(background.time, "monotonic", lambda: next(clock)):
            limited = background.run_scheduled(budget_seconds=45)
    assert limited["synced"] == 1 and limited["skipped"] == 2
