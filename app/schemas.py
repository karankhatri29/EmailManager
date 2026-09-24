from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


def _as_utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


# --- auth ---------------------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str


# --- mailboxes ----------------------------------------------------------------------------------


class AccountOut(BaseModel):
    id: int
    provider: str
    email_address: str
    status: str
    last_error: str | None = None
    last_synced_at: datetime | None = None
    syncing: bool = False


# --- inbox --------------------------------------------------------------------------------------


class EmailOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    account_id: int
    sender: str
    subject: str
    body: str
    score: float
    category: str
    date: datetime
    summary: str | None = None
    task: str | None = None


class TaskOut(BaseModel):
    id: str
    sender: str
    task: str
    deadline: str
    sort_tier: int
    base_score: float


class SyncStatus(BaseModel):
    syncing: bool
    timeframe: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    new_emails: int | None = None
    error: str | None = None


# --- activities (calendar) ----------------------------------------------------------------------


class ActivityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    notes: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    all_day: bool
    status: str
    source: str
    email_id: str | None = None

    @field_validator("start_at", "end_at")
    @classmethod
    def _utc(cls, value):
        return _as_utc(value)


class ActivityCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    notes: str | None = Field(default=None, max_length=5000)
    start_at: datetime | None = None
    end_at: datetime | None = None
    all_day: bool = False

    @field_validator("start_at", "end_at")
    @classmethod
    def _utc(cls, value):
        return _as_utc(value)

    @model_validator(mode="after")
    def _check_range(self):
        if self.end_at is not None and self.start_at is None:
            raise ValueError("end_at requires start_at")
        if self.start_at and self.end_at and self.end_at < self.start_at:
            raise ValueError("end_at must not be before start_at")
        return self


class ActivityUpdate(BaseModel):
    """Partial update: only the fields present in the request are changed (start_at=null unschedules)."""

    title: str | None = Field(default=None, min_length=1, max_length=255)
    notes: str | None = Field(default=None, max_length=5000)
    start_at: datetime | None = None
    end_at: datetime | None = None
    all_day: bool | None = None
    status: Literal["todo", "done"] | None = None

    @field_validator("start_at", "end_at")
    @classmethod
    def _utc(cls, value):
        return _as_utc(value)


class CalendarFeedOut(BaseModel):
    url: str
