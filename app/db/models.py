from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text, UniqueConstraint
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
