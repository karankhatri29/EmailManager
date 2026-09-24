from datetime import date, datetime, time, timezone
from datetime import date as Day  # `EmailOut.date` is a field, so its own name cannot be used as a type
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from .services.textify import normalize_body, strip_symbols


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
    due_date: Day | None = None
    due_kind: str | None = None
    due_text: str | None = None

    @field_validator("date", "snoozed_until")
    @classmethod
    def _utc(cls, value):
        return _as_utc(value)

    @field_validator("body")
    @classmethod
    def _readable_body(cls, value):
        """Mail stored before HTML was converted at sync time is still shown as text."""
        return normalize_body(value)

    @field_validator("summary")
    @classmethod
    def _plain_summary(cls, value):
        return strip_symbols(value) if value else value


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


class UnopenedNewsletterOut(BaseModel):
    """A bulk sender whose mail has gone unopened for months."""

    sender_address: str
    sender: str
    messages: int
    first_date: datetime
    last_date: datetime
    can_unsubscribe: bool
    one_click: bool  # true: cleaning up can unsubscribe automatically

    @field_validator("first_date", "last_date")
    @classmethod
    def _utc(cls, value):
        return _as_utc(value)


class CleanupRequest(BaseModel):
    sender_addresses: list[str] | None = None  # None: every suggestion


class CleanupResult(BaseModel):
    sender_address: str
    sender: str
    messages: int
    method: Literal["one_click", "link", "none"]
    unsubscribed: bool
    archived: int
    link: str | None = None  # open it to finish unsubscribing when it could not be done automatically
    detail: str = ""


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
    due_date: date | None = None
    deadline_kind: str | None = None  # deadline | event | asap
    deadline_text: str | None = None  # the words in the email the date was read from


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
    remind_at: datetime | None = None
    reminded_at: datetime | None = None
    priority: int = 2
    course: str | None = None

    @field_validator("start_at", "end_at", "remind_at", "reminded_at")
    @classmethod
    def _utc(cls, value):
        return _as_utc(value)


class ActivityCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    notes: str | None = Field(default=None, max_length=5000)
    start_at: datetime | None = None
    end_at: datetime | None = None
    all_day: bool = False
    remind_at: datetime | None = None
    priority: int = Field(default=2, ge=1, le=3)
    course: str | None = Field(default=None, max_length=120)

    @field_validator("start_at", "end_at", "remind_at")
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
    remind_at: datetime | None = None  # null clears the reminder
    priority: int | None = Field(default=None, ge=1, le=3)
    course: str | None = Field(default=None, max_length=120)  # null removes the course

    @field_validator("start_at", "end_at", "remind_at")
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

    @field_validator("summary")
    @classmethod
    def _plain_summary(cls, value):
        return strip_symbols(value) if value else value


# --- settings, briefing, notifications, follow-ups ---------------------------------------------


class SettingsOut(BaseModel):
    timezone: str
    briefing_enabled: bool
    briefing_hour: int
    urgent_alerts: bool
    reminder_emails: bool
    followup_days: int
    digest_enabled: bool
    digest_hour: int
    auto_cleanup: bool
    cleanup_months: int
    email_configured: bool  # whether this server can send email at all


class SettingsUpdate(BaseModel):
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    briefing_enabled: bool | None = None
    briefing_hour: int | None = Field(default=None, ge=0, le=23)
    urgent_alerts: bool | None = None
    reminder_emails: bool | None = None
    followup_days: int | None = Field(default=None, ge=1, le=30)
    digest_enabled: bool | None = None
    digest_hour: int | None = Field(default=None, ge=0, le=23)
    auto_cleanup: bool | None = None
    cleanup_months: int | None = Field(default=None, ge=1, le=12)


class BriefingOut(BaseModel):
    """Today's briefing. Lists hold plain dicts (ids, titles, dates) rendered by the UI."""

    date: str
    greeting: str
    overdue: list[dict[str, Any]]
    today: list[dict[str, Any]]
    upcoming: list[dict[str, Any]]
    top_emails: list[dict[str, Any]]
    promotions: dict[str, Any]
    waiting: list[dict[str, Any]]
    classes: list[dict[str, Any]] = []
    counts: dict[str, int]


class DigestOut(BaseModel):
    """The evening promotions digest."""

    date: str
    count: int
    headline: str
    deals: list[dict[str, Any]]
    top_senders: list[dict[str, Any]]


class BriefingSent(BaseModel):
    sent_to: str


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    title: str
    body: str | None = None
    ref: str | None = None
    created_at: datetime
    read_at: datetime | None = None

    @field_validator("created_at", "read_at")
    @classmethod
    def _utc(cls, value):
        return _as_utc(value)


class NotificationList(BaseModel):
    unread: int
    items: list[NotificationOut]


class MarkReadRequest(BaseModel):
    ids: list[int] | None = None  # omit to mark everything read


class FollowUpOut(BaseModel):
    id: int
    account_id: int
    thread_id: str
    subject: str
    recipient: str
    sent_at: datetime
    status: str
    nudge_at: datetime
    waiting_days: int

    @field_validator("sent_at", "nudge_at")
    @classmethod
    def _utc(cls, value):
        return _as_utc(value)


class SnoozeRequest(BaseModel):
    days: int = Field(ge=1, le=30)


# --- timetable ---------------------------------------------------------------------------------

COLOR_PATTERN = r"^#[0-9a-fA-F]{6}$"


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


class ClassSlotCreate(BaseModel):
    """A class and the weekdays it meets on; one slot is created per weekday."""

    title: str = Field(min_length=1, max_length=120)
    code: str | None = Field(default=None, max_length=32)
    weekdays: list[int] = Field(min_length=1, max_length=7)  # 0 = Monday ... 6 = Sunday
    start_time: time
    end_time: time
    room: str | None = Field(default=None, max_length=80)
    instructor: str | None = Field(default=None, max_length=120)
    color: str | None = Field(default=None, pattern=COLOR_PATTERN)
    term_start: date | None = None
    term_end: date | None = None
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("title")
    @classmethod
    def _title(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value

    @field_validator("code", "room", "instructor", "notes")
    @classmethod
    def _blank_is_none(cls, value):
        return _clean(value)

    @field_validator("weekdays")
    @classmethod
    def _weekdays(cls, value):
        if any(d < 0 or d > 6 for d in value):
            raise ValueError("weekdays must be 0 (Monday) to 6 (Sunday)")
        return sorted(set(value))

    @model_validator(mode="after")
    def _check(self):
        if self.end_time <= self.start_time:
            raise ValueError("the class must end after it starts")
        if self.term_start and self.term_end and self.term_end < self.term_start:
            raise ValueError("term_end must not be before term_start")
        return self


class ClassSlotUpdate(BaseModel):
    """Partial update of one meeting. Only the fields sent change."""

    title: str | None = Field(default=None, min_length=1, max_length=120)
    code: str | None = Field(default=None, max_length=32)
    weekday: int | None = Field(default=None, ge=0, le=6)
    start_time: time | None = None
    end_time: time | None = None
    room: str | None = Field(default=None, max_length=80)
    instructor: str | None = Field(default=None, max_length=120)
    color: str | None = Field(default=None, pattern=COLOR_PATTERN)
    term_start: date | None = None
    term_end: date | None = None
    notes: str | None = Field(default=None, max_length=2000)
    # Also apply title, code, colour, instructor and term dates to every other meeting of the same course.
    apply_to_course: bool = False

    @field_validator("title")
    @classmethod
    def _title(cls, value):
        return value.strip() if value is not None else value

    @field_validator("code", "room", "instructor", "notes")
    @classmethod
    def _blank_is_none(cls, value):
        return _clean(value)


class ClassSlotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    code: str | None = None
    weekday: int
    start_time: time
    end_time: time
    room: str | None = None
    instructor: str | None = None
    color: str
    term_start: date | None = None
    term_end: date | None = None
    notes: str | None = None

    @field_serializer("start_time", "end_time")
    def _hhmm(self, value: time) -> str:
        return value.strftime("%H:%M")
