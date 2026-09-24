from datetime import date, datetime, timezone
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
    """A full email, including its body (used for the dashboard and the detail view)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    account_id: int
    sender: str
    sender_address: str = ""
    subject: str
    body: str
    score: float
    category: str
    category_source: str = "auto"  # auto | rule | user
    reason: str | None = None  # plain-English "why this category"
    date: datetime
    summary: str | None = None
    task: str | None = None
    is_done: bool = False
    snoozed_until: datetime | None = None
    thread_id: str | None = None
    unsubscribe_url: str | None = None
    unsubscribe_one_click: bool = False

    @field_validator("date", "snoozed_until")
    @classmethod
    def _utc(cls, value):
        return _as_utc(value)


class EmailListItem(BaseModel):
    """A row in the inbox list: no full body, just enough to scan."""

    id: str
    account_id: int
    sender: str
    sender_address: str
    subject: str
    snippet: str
    date: datetime
    score: float
    category: str
    category_source: str
    reason: str | None = None
    is_done: bool
    snoozed_until: datetime | None = None
    has_summary: bool
    thread_id: str | None = None
    can_unsubscribe: bool = False

    @field_validator("date", "snoozed_until")
    @classmethod
    def _utc(cls, value):
        return _as_utc(value)


class InboxPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[EmailListItem]


class EmailUpdate(BaseModel):
    """Partial update of one email. Only the fields sent are applied."""

    category: Literal["Urgent / Action Required", "Important", "General", "Promotional"] | None = None
    apply_to_sender: bool = False  # with `category`: also remember it as a rule for this sender
    is_done: bool | None = None
    snoozed_until: datetime | None = None  # null wakes the email now

    @field_validator("snoozed_until")
    @classmethod
    def _utc(cls, value):
        return _as_utc(value)


class RuleIn(BaseModel):
    kind: Literal["sender", "domain", "keyword"]
    pattern: str = Field(min_length=1, max_length=320)
    category: Literal["Urgent / Action Required", "Important", "General", "Promotional"]


class RuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    pattern: str
    category: str
    created_at: datetime


class RuleCreated(RuleOut):
    affected: int  # how many stored emails changed category because of it


class NewsletterOut(BaseModel):
    sender_address: str
    sender: str
    count: int
    last_date: datetime
    last_subject: str
    account_id: int
    can_unsubscribe: bool
    one_click: bool
    muted: bool

    @field_validator("last_date")
    @classmethod
    def _utc(cls, value):
        return _as_utc(value)


class UnsubscribeRequest(BaseModel):
    sender_address: str = Field(min_length=3, max_length=320)
    mute: bool = True  # also stop showing this sender's mail as anything but Promotional


class UnsubscribeResult(BaseModel):
    method: Literal["one_click", "link", "none"]
    url: str | None = None  # for "link": open it to finish unsubscribing
    ok: bool
    detail: str = ""
    muted: bool = False


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


# --- smart search and threads ------------------------------------------------------------------


class SearchPlanOut(BaseModel):
    keywords: list[str]
    date_from: date | None = None
    date_to: date | None = None
    category: str | None = None
    sender: str | None = None
    explanation: str  # e.g. "Looking for mail about couch, invoice between 2026-06-01 and 2026-08-31"
    used_ai: bool


class SearchHit(EmailListItem):
    match: float  # relevance, higher is better


class SearchResponse(BaseModel):
    plan: SearchPlanOut
    semantic: bool  # True when ranked by meaning as well as words
    items: list[SearchHit]


class ThreadMessage(BaseModel):
    id: str
    sender: str
    date: datetime
    snippet: str

    @field_validator("date")
    @classmethod
    def _utc(cls, value):
        return _as_utc(value)


class ThreadOut(BaseModel):
    message_count: int
    messages: list[ThreadMessage]
    summary: str | None = None  # HTML built server-side from escaped text
    summary_current: bool = False  # False if the thread has grown since the summary was made
