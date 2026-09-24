import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
MICROSOFT_SCOPES = ["offline_access", "User.Read", "Mail.Read"]

Timeframe = Literal["Last 1 Day", "Last 1 Week", "Last 1 Month"]
DEFAULT_TIMEFRAME: Timeframe = "Last 1 Day"

# timeframe label -> (Gmail search query, window size in days)
TIMEFRAMES: dict[str, tuple[str, int]] = {
    "Last 1 Day": ("newer_than:1d", 1),
    "Last 1 Week": ("newer_than:7d", 7),
    "Last 1 Month": ("newer_than:30d", 30),
}

# Categories that get an AI summary and an activity
SUMMARIZED_CATEGORIES = ["Urgent / Action Required", "Important"]


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables and .env."""

    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    database_url: str = f"sqlite:///{(BASE_DIR / 'app.db').as_posix()}"
    redis_url: str = "redis://localhost:6379/0"

    # Security. Generate both with: python -m scripts.generate_keys
    secret_key: str = ""  # signs session cookies
    encryption_key: str = ""  # Fernet key; encrypts stored mailbox credentials
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

    sync_interval_seconds: int = 300
    sync_max_workers: int = 4  # mailboxes synced concurrently
    # Run the periodic sync inside the API process (local dev without Redis).
    # Set to false when a Celery worker + beat does it instead.
    inprocess_sync: bool = True
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
    def microsoft_configured(self) -> bool:
        return bool(self.microsoft_client_id and self.microsoft_client_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
