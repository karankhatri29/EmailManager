from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


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
    subject: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    score: Mapped[float] = mapped_column(Float)
    category: Mapped[str] = mapped_column(String(64), index=True)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    task: Mapped[str | None] = mapped_column(String(255), nullable=True)

    sender_address: Mapped[str] = mapped_column(String(320), default="", server_default="", index=True)
    thread_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)  # plain-English "why this category"
    category_source: Mapped[str] = mapped_column(String(16), default="auto", server_default="auto")  # auto|rule|user
    is_done: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    unsubscribe_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    unsubscribe_one_click: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)  # float32 vector for semantic search


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
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    all_day: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="todo")  # 'todo' | 'done'
    source: Mapped[str] = mapped_column(String(16), default="manual")  # 'email' | 'manual'
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


class FollowUp(Base):
    """A message you sent that hasn't been answered yet."""

    __tablename__ = "follow_ups"
    __table_args__ = (UniqueConstraint("account_id", "thread_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("mail_accounts.id", ondelete="CASCADE"), index=True)
    thread_id: Mapped[str] = mapped_column(String(128))
    subject: Mapped[str] = mapped_column(Text)
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
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
