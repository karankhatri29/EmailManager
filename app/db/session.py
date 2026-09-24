from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from ..core.config import get_settings


def enable_sqlite_foreign_keys(engine: Engine) -> None:
    """SQLite ignores foreign keys (and ON DELETE CASCADE) unless told otherwise."""

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")


_url = get_settings().database_url
_engine_kwargs = (
    {"connect_args": {"check_same_thread": False}} if _url.startswith("sqlite") else {"pool_pre_ping": True}
)

engine = create_engine(_url, **_engine_kwargs)
if _url.startswith("sqlite"):
    enable_sqlite_foreign_keys(engine)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    with SessionLocal() as session:
        yield session
