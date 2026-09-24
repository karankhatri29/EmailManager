"""Outgoing email (briefings, reminders) over SMTP, and in-app notifications."""

import logging
import smtplib
import ssl
from email.message import EmailMessage

from sqlalchemy.orm import Session

from ..core.config import URGENT_ALERT_MAX_AGE_DAYS, get_settings
from ..repositories import notifications as notifications_repo
from ..repositories import settings as settings_repo

logger = logging.getLogger(__name__)

SMTP_TIMEOUT = 20


class EmailNotConfigured(Exception):
    """This server has no SMTP settings, so it cannot send email."""


class EmailSendError(Exception):
    """The SMTP server refused or could not be reached."""


def send_email(to: str, subject: str, text: str, html: str | None = None) -> None:
    """Sends one email. Raises EmailNotConfigured or EmailSendError."""
    settings = get_settings()
    if not settings.smtp_configured:
        raise EmailNotConfigured

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from or settings.smtp_user
    message["To"] = to
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")

    try:
        if settings.smtp_port == 465:
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                settings.smtp_host,
                settings.smtp_port,
                timeout=SMTP_TIMEOUT,
                context=ssl.create_default_context(),
            )
        else:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT)
        with server:
            if settings.smtp_port != 465 and settings.smtp_starttls:
                server.starttls(context=ssl.create_default_context())
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        raise EmailSendError(str(exc)) from exc


def try_send_email(to: str, subject: str, text: str, html: str | None = None) -> bool:
    """send_email for background jobs: never raises, returns whether it was sent."""
    try:
        send_email(to, subject, text, html)
        return True
    except EmailNotConfigured:
        return False
    except EmailSendError:
        logger.warning("Could not send email to %s", to, exc_info=True)
        return False


def notify_urgent(db: Session, user_id: int, new_emails: list[dict], now=None) -> bool:
    """One in-app alert for a batch of newly arrived urgent mail (never one per email: that is the noise
    this app exists to remove). Old mail from a first big import is ignored."""
    from datetime import datetime, timedelta, timezone

    if not settings_repo.get_or_create(db, user_id).urgent_alerts:
        return False
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=URGENT_ALERT_MAX_AGE_DAYS)

    def aware(d):
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)

    urgent = [
        e for e in new_emails if e["category"] == "Urgent / Action Required" and aware(e["date"]) >= cutoff
    ]
    if not urgent:
        return False

    title = "1 new urgent email" if len(urgent) == 1 else f"{len(urgent)} new urgent emails"
    body = "\n".join(f"• {e['subject'][:80]}" for e in urgent[:3]) + ("\n…" if len(urgent) > 3 else "")
    notifications_repo.create(db, user_id, "urgent", title, body)
    return True
