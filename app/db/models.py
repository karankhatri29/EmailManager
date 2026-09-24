from datetime import date, datetime, time, timezone
from datetime import date as Day  # `Email.date` is a column, so its own name cannot be used as a type

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .encrypted import EncryptedText


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    calendar_token: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # secret ICS feed link
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class MailAccount(Base):
    """A mailbox a user has connected (Gmail today; Outlook/IMAP later)."""

    __tablename__ = "mail_accounts"
    __table_args__ = (UniqueConstraint("user_id", "provider", "email_address"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(32))  # 'google'
    email_address: Mapped[str] = mapped_column(String(320))
    credentials: Mapped[str] = mapped_column(Text)  # Fernet-encrypted JSON
    status: Mapped[str] = mapped_column(String(32), default="active")  # 'active' | 'needs_reauth'
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Email(Base):
    """A message after NLP classification (and optional AI summary)."""

    __tablename__ = "emails"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)  # "<account_id>:<provider message id>"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("mail_accounts.id", ondelete="CASCADE"), index=True)
    sender: Mapped[str] = mapped_column(String(512))
    subject: Mapped[str] = mapped_column(EncryptedText)  # encrypted at rest (see db/encrypted.py)
    body: Mapped[str] = mapped_column(EncryptedText)  # encrypted at rest
    score: Mapped[float] = mapped_column(Float)
    category: Mapped[str] = mapped_column(String(64), index=True)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    summary: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)  # encrypted at rest
    task: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)  # an action line from the mail

    sender_address: Mapped[str] = mapped_column(String(320), default="", server_default="", index=True)
    thread_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    reason: Mapped[str | None] = mapped_column(
        EncryptedText, nullable=True
    )  # "why this category"; quotes the mail
    category_source: Mapped[str] = mapped_column(
        String(16), default="auto", server_default="auto"
    )  # auto|rule|user
    is_done: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    unsubscribe_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    unsubscribe_one_click: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    embedding: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True
    )  # float32 vector for semantic search

    # What the text analysis found (see services/analysis.py); nlp_version 0 = not analysed yet
    due_date: Mapped[Day | None] = mapped_column(Date, nullable=True, index=True)
    due_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)  # deadline | event | asap
    due_text: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)  # the words it was read from
    due_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    due_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    nlp_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_unread: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # as of the sync; None = unknown


class SyncState(Base):
    """When each mailbox + timeframe was last synced."""

    __tablename__ = "sync_state"

    account_id: Mapped[int] = mapped_column(
        ForeignKey("mail_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    timeframe: Mapped[str] = mapped_column(String(32), primary_key=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Activity(Base):
    """A calendar item: created automatically from an email's task/deadline, or manually."""

    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    email_id: Mapped[str | None] = mapped_column(
        ForeignKey("emails.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    title: Mapped[str] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)  # encrypted at rest
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    all_day: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="todo")  # 'todo' | 'done'
    source: Mapped[str] = mapped_column(String(16), default="manual")  # 'email' | 'manual'
    priority: Mapped[int] = mapped_column(Integer, default=2, server_default="2")  # 1 low, 2 normal, 3 high
    course: Mapped[str | None] = mapped_column(String(120), nullable=True)  # which course a to-do belongs to
    remind_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class Rule(Base):
    """A user's own classification rule: mail from this sender/domain, or containing this keyword, gets a category."""

    __tablename__ = "rules"
    __table_args__ = (UniqueConstraint("user_id", "kind", "pattern"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # 'sender' | 'domain' | 'keyword'
    pattern: Mapped[str] = mapped_column(String(320))  # lower-cased
    category: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class UserSettings(Base):
    __tablename__ = "user_settings"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", server_default="UTC")  # IANA name
    briefing_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")  # daily email
    briefing_hour: Mapped[int] = mapped_column(Integer, default=8, server_default="8")  # local hour to send
    briefing_last_sent: Mapped[date | None] = mapped_column(Date, nullable=True)  # local date of the last one
    urgent_alerts: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")  # in-app alerts
    reminder_emails: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    followup_days: Mapped[int] = mapped_column(Integer, default=3, server_default="3")  # nudge after N days
    digest_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0"
    )  # evening promo digest
    digest_hour: Mapped[int] = mapped_column(Integer, default=19, server_default="19")  # local hour to send
    digest_last_sent: Mapped[date | None] = mapped_column(Date, nullable=True)
    auto_cleanup: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0"
    )  # unsubscribe automatically
    cleanup_months: Mapped[int] = mapped_column(
        Integer, default=3, server_default="3"
    )  # "unopened for N months"
    cleanup_last_run: Mapped[date | None] = mapped_column(Date, nullable=True)


class FollowUp(Base):
    """A message you sent that hasn't been answered yet."""

    __tablename__ = "follow_ups"
    __table_args__ = (UniqueConstraint("account_id", "thread_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("mail_accounts.id", ondelete="CASCADE"), index=True)
    thread_id: Mapped[str] = mapped_column(String(128))
    subject: Mapped[str] = mapped_column(EncryptedText)  # encrypted at rest
    recipient: Mapped[str] = mapped_column(String(512))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="waiting", server_default="waiting")
    nudge_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # when to remind you
    nudged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24))  # 'reminder' | 'followup' | 'urgent' | 'briefing'
    title: Mapped[str] = mapped_column(EncryptedText)  # often names an email subject: encrypted at rest
    body: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)
    ref: Mapped[str | None] = mapped_column(String(128), nullable=True)  # e.g. 'activity:12' or an email id
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ThreadSummary(Base):
    """Cached AI summary of a whole conversation; recomputed only when the thread gains messages."""

    __tablename__ = "thread_summaries"
    __table_args__ = (UniqueConstraint("user_id", "thread_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    thread_key: Mapped[str] = mapped_column(String(160))  # "<account_id>:<provider thread id>"
    message_count: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(EncryptedText)  # encrypted at rest
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ClassSlot(Base):
    """One weekly meeting of a class in a student's timetable (a course with three lectures a week is three slots)."""

    __tablename__ = "class_slots"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    code: Mapped[str | None] = mapped_column(String(32), nullable=True)  # e.g. CSE1001
    weekday: Mapped[int] = mapped_column(Integer)  # 0 = Monday ... 6 = Sunday
    start_time: Mapped[time] = mapped_column(Time)  # local wall-clock time, in the user's own time zone
    end_time: Mapped[time] = mapped_column(Time)
    room: Mapped[str | None] = mapped_column(String(80), nullable=True)
    instructor: Mapped[str | None] = mapped_column(String(120), nullable=True)
    color: Mapped[str] = mapped_column(String(7), default="#3b82f6", server_default="#3b82f6")
    term_start: Mapped[date | None] = mapped_column(
        Date, nullable=True
    )  # first day of the semester, if known
    term_end: Mapped[date | None] = mapped_column(Date, nullable=True)  # last day of the semester, if known
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
