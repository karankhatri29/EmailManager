from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command

ROOT = Path(__file__).resolve().parents[2]
TABLES = {"users", "mail_accounts", "emails", "sync_state", "activities"}


def _config(url):
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def test_upgrade_creates_tables_and_downgrade_removes_them(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    cfg = _config(url)

    command.upgrade(cfg, "head")
    inspector = inspect(create_engine(url))
    assert set(inspector.get_table_names()) >= TABLES
    assert {c["name"] for c in inspector.get_columns("emails")} >= {"id", "user_id", "account_id", "summary"}

    command.downgrade(cfg, "base")
    assert TABLES.isdisjoint(inspect(create_engine(url)).get_table_names())


def test_migrations_match_the_models(tmp_path):
    """Fails if someone edits app/db/models.py without generating a migration."""
    url = f"sqlite:///{(tmp_path / 'drift.db').as_posix()}"
    cfg = _config(url)
    command.upgrade(cfg, "head")
    command.check(cfg)  # raises if autogenerate would produce any change


def test_upgrading_from_the_single_user_schema_keeps_working(tmp_path):
    url = f"sqlite:///{(tmp_path / 'old.db').as_posix()}"
    cfg = _config(url)
    command.upgrade(cfg, "0001")
    command.upgrade(cfg, "head")
    assert set(inspect(create_engine(url)).get_table_names()) >= TABLES
