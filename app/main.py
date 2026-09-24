from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .api import account_tools, accounts, activities, auth, calendar, inbox, mail, routes, rules
from .core.config import get_settings
from .core.logging import configure_logging
from .services.background import start_periodic_jobs, start_periodic_sync

APP_DIR = Path(__file__).resolve().parent
SESSION_MAX_AGE = 14 * 24 * 3600


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    stops = []
    if settings.inprocess_sync:
        stops = [
            start_periodic_sync(settings.sync_interval_seconds),
            start_periodic_jobs(settings.jobs_interval_seconds),
        ]
    yield
    for stop in stops:
        stop.set()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    if not settings.secret_key or not settings.encryption_key:
        raise RuntimeError(
            "SECRET_KEY and ENCRYPTION_KEY must be set. Generate them: python -m scripts.generate_keys"
        )

    app = FastAPI(title="Email Prioritizer", version="0.4.0", lifespan=lifespan)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="session",
        max_age=SESSION_MAX_AGE,
        same_site="lax",  # blocks cross-site POSTs from carrying the cookie (CSRF defence)
        https_only=settings.session_https_only,
    )
    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
    for module in (routes, auth, accounts, inbox, mail, rules, account_tools, activities, calendar):
        app.include_router(module.router)
    return app


app = create_app()
