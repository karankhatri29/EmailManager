import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
MICROSOFT_SCOPES = ["offline_access", "User.Read", "Mail.Read"]

Timeframe = Literal["Last 1 Day", "Last 1 Week", "Last 1 Month", "Last 3 Months", "Last 1 Year"]
DEFAULT_TIMEFRAME: Timeframe = "Last 1 Day"

# timeframe label -> (Gmail search query, window size in days)
TIMEFRAMES: dict[str, tuple[str, int]] = {
    "Last 1 Day": ("newer_than:1d", 1),
    "Last 1 Week": ("newer_than:7d", 7),
    "Last 1 Month": ("newer_than:30d", 30),
    "Last 3 Months": ("newer_than:90d", 90),
    "Last 1 Year": ("newer_than:365d", 365),
}

# How many messages one sync pulls at most, per timeframe (long windows are for search and history).
TIMEFRAME_MESSAGE_LIMITS: dict[str, int] = {
    "Last 1 Day": 200,
    "Last 1 Week": 400,
    "Last 1 Month": 800,
    "Last 3 Months": 1500,
    "Last 1 Year": 3000,
}

# Older mail is stored, classified and searchable, but is not summarised by AI, turned into
# calendar items or announced with notifications: those only make sense for recent mail.
ACTIONABLE_MAX_AGE_DAYS = 30
URGENT_ALERT_MAX_AGE_DAYS = 2

# Categories that get an AI summary and an activity
SUMMARIZED_CATEGORIES = ["Urgent / Action Required", "Important"]


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables and .env."""

    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    database_url: str = f"sqlite:///{(BASE_DIR / 'app.db').as_posix()}"

    @field_validator("retention_days")
    @classmethod
    def _retention_floor(cls, days: int) -> int:
        return 0 if days <= 0 else max(days, 30)

    @field_validator("database_url")
    @classmethod
    def _use_the_psycopg_driver(cls, url: str) -> str:
        """Hosted Postgres (Neon, Supabase, Heroku...) hands out postgres:// or postgresql:// URLs; SQLAlchemy needs
        the driver we ship (psycopg 3) spelled out."""
        for plain in ("postgres://", "postgresql://"):
            if url.startswith(plain):
                return "postgresql+psycopg://" + url[len(plain) :]
        return url

    redis_url: str = "redis://localhost:6379/0"

    # Security. Generate both with: python -m scripts.generate_keys
    secret_key: str = ""  # signs session cookies
    encryption_key: str = ""  # Fernet key; encrypts stored mailbox credentials and stored email text
    # Old keys, comma separated, that stored data may still be encrypted with (key rotation): they can only
    # decrypt. Run `python -m scripts.encrypt_stored_mail` to re-encrypt everything with the current key.
    encryption_keys_previous: str = ""
    encrypt_email_text: bool = True  # encrypt email subject / body / summary and calendar notes at rest
    # Who may create an account here: comma separated email addresses. Empty = anyone (open sign-up).
    # Use it for a private group; it does not affect which Gmail / Outlook mailboxes a member connects.
    allowed_emails: str = ""
    # Stored mail older than this many days is deleted automatically (0 = keep forever). Keeps the database from
    # growing without limit on small hosted databases; values between 1 and 29 are raised to 30.
    retention_days: int = 180
    session_https_only: bool = False  # set true behind HTTPS in production
    public_base_url: str = "http://localhost:8000"  # used to build calendar feed links

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.1-flash-lite"
    summary_workers: int = 5  # concurrent Gemini calls per sync

    # Google OAuth client. Either set these, or point at the downloaded credentials.json.
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/accounts/google/callback"
    google_credentials_path: Path = BASE_DIR / "credentials.json"

    # Microsoft (Outlook / Microsoft 365) app registration used by "Connect Outlook"
    # (Azure portal -> App registrations). "common" allows work, school and personal accounts.
    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    microsoft_tenant: str = "common"
    microsoft_redirect_uri: str = "http://localhost:8000/api/accounts/microsoft/callback"

    gemini_embedding_model: str = "gemini-embedding-001"

    # Outgoing mail for the daily briefing and reminder emails. Leave smtp_host empty to disable email
    # (briefings and reminders then appear in the app only).
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True

    sync_interval_seconds: int = 300
    jobs_interval_seconds: int = 60  # briefings, reminders and follow-up nudges are checked this often
    sync_max_workers: int = 4  # mailboxes synced concurrently
    # Run the periodic sync inside the API process (local dev without Redis).
    # Set to false when a Celery worker + beat does it instead.
    inprocess_sync: bool = True
    # Serverless hosting (Vercel): no background threads survive between requests, so syncs run inside the request
    # that asks for them and a scheduled call to /api/cron/run does the rest. Detected from the VERCEL variable.
    serverless: bool = False
    cron_secret: str = (
        ""  # Vercel Cron sends it as "Authorization: Bearer <secret>"; empty disables /api/cron/run
    )
    cron_budget_seconds: int = 45  # stop starting new mailbox syncs after this long, the next run continues
    log_level: str = "INFO"

    def google_client(self) -> tuple[str, str]:
        """(client_id, client_secret) from settings, else from the credentials.json file."""
        if self.google_client_id and self.google_client_secret:
            return self.google_client_id, self.google_client_secret
        if self.google_credentials_path.exists():
            data = json.loads(self.google_credentials_path.read_text(encoding="utf-8"))
            cfg = data.get("web") or data.get("installed") or {}
            if cfg.get("client_id") and cfg.get("client_secret"):
                return cfg["client_id"], cfg["client_secret"]
        raise RuntimeError(
            "Google OAuth client not configured: set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET, "
            "or provide credentials.json."
        )

    @property
    def is_serverless(self) -> bool:
        return self.serverless or bool(os.environ.get("VERCEL"))

    @property
    def allowed_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.allowed_emails.split(",") if e.strip()}

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and (self.smtp_from or self.smtp_user))

    @property
    def microsoft_configured(self) -> bool:
        return bool(self.microsoft_client_id and self.microsoft_client_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
