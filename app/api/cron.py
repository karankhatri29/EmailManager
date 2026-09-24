"""The scheduled call for serverless hosting (Vercel Cron). Elsewhere a Celery beat or the in-process loops do this."""

import hmac

from fastapi import APIRouter, Header, HTTPException

from ..core.config import get_settings
from ..services import background

router = APIRouter(prefix="/api/cron", tags=["cron"])


@router.get("/run")
def run(authorization: str | None = Header(default=None)):
    """Syncs every mailbox, then runs briefings / reminders / follow-up nudges. Needs CRON_SECRET (Vercel Cron sends
    it as a bearer token); without a configured secret the endpoint does not exist."""
    settings = get_settings()
    if not settings.cron_secret:
        raise HTTPException(status_code=404, detail="Not found")
    expected = f"Bearer {settings.cron_secret}"
    if not hmac.compare_digest((authorization or "").encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return background.run_scheduled(budget_seconds=settings.cron_budget_seconds)
