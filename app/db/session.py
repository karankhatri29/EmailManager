from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from ..core.config import get_settings


def enable_sqlite_foreign_keys(engine: Engine) -> None:
    """SQLite ignores foreign keys (and ON DELETE CASCADE) unless told otherwise."""

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")
        # Overwrite deleted / replaced content with zeros. Without this SQLite leaves the old bytes in the file, so
        # "disconnect a mailbox" (and re-encrypting old mail) would not really remove the readable text.
        dbapi_connection.execute("PRAGMA secure_delete=ON")
        # Syncs, the ticket scan and web requests write at the same time: a writer that meets another one waits
        # (up to 30 s) instead of failing with "database is locked". (Not WAL mode: it would keep deleted mail
        # readable in the -wal file, which secure_delete above is there to prevent.)
        dbapi_connection.execute("PRAGMA busy_timeout=30000")


_url = get_settings().database_url
_engine_kwargs = (
    {"connect_args": {"check_same_thread": False, "timeout": 30}}
    if _url.startswith("sqlite")
    else {"pool_pre_ping": True}
)

engine = create_engine(_url, **_engine_kwargs)
if _url.startswith("sqlite"):
    enable_sqlite_foreign_keys(engine)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    with SessionLocal() as session:
        yield session
