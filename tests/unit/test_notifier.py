import smtplib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.repositories import notifications as notifications_repo
from app.repositories import settings as settings_repo
from app.services import notifier
from app.services.notifier import (
    EmailNotConfigured,
    EmailSendError,
    notify_urgent,
    send_email,
    try_send_email,
)

URGENT = "Urgent / Action Required"


def _smtp_settings(**overrides):
    base = dict(
        smtp_configured=True,
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="me@example.com",
        smtp_password="secret",
        smtp_from="Prioritizer <me@example.com>",
        smtp_starttls=True,
    )
    return patch.object(notifier, "get_settings", return_value=SimpleNamespace(**{**base, **overrides}))


# --- email ---------------------------------------------------------------------------------------


def test_not_configured_raises_and_background_helper_returns_false():
    with _smtp_settings(smtp_configured=False):
        with pytest.raises(EmailNotConfigured):
            send_email("a@b.com", "s", "t")
        assert try_send_email("a@b.com", "s", "t") is False


def test_sends_with_starttls_login_and_both_text_and_html():
    server = MagicMock()
    with _smtp_settings(), patch.object(notifier.smtplib, "SMTP") as smtp:
        smtp.return_value = server
        server.__enter__.return_value = server
        send_email("you@x.com", "Subject", "plain body", "<p>html body</p>")

    smtp.assert_called_once_with("smtp.example.com", 587, timeout=notifier.SMTP_TIMEOUT)
    server.starttls.assert_called_once()
    server.login.assert_called_once_with("me@example.com", "secret")
    message = server.send_message.call_args.args[0]
    assert (
        message["To"] == "you@x.com" and message["Subject"] == "Subject" and "Prioritizer" in message["From"]
    )
    assert message.get_body(("plain",)).get_content().strip() == "plain body"
    assert "html body" in message.get_body(("html",)).get_content()


def test_port_465_uses_implicit_tls_and_no_starttls():
    server = MagicMock()
    server.__enter__.return_value = server
    with (
        _smtp_settings(smtp_port=465),
        patch.object(notifier.smtplib, "SMTP_SSL", return_value=server) as smtp_ssl,
    ):
        send_email("you@x.com", "S", "t")
    smtp_ssl.assert_called_once()
    server.starttls.assert_not_called()


def test_no_login_without_credentials():
    server = MagicMock()
    server.__enter__.return_value = server
    with (
        _smtp_settings(smtp_user="", smtp_from="me@example.com"),
        patch.object(notifier.smtplib, "SMTP", return_value=server),
    ):
        send_email("you@x.com", "S", "t")
    server.login.assert_not_called()


@pytest.mark.parametrize(
    "failure", [smtplib.SMTPAuthenticationError(535, b"bad"), ConnectionRefusedError("down"), TimeoutError()]
)
def test_smtp_failures_become_send_errors(failure):
    with _smtp_settings(), patch.object(notifier.smtplib, "SMTP", side_effect=failure):
        with pytest.raises(EmailSendError):
            send_email("you@x.com", "S", "t")
        assert try_send_email("you@x.com", "S", "t") is False  # background jobs never raise


def test_try_send_email_reports_success():
    with patch.object(notifier, "send_email") as send:
        assert try_send_email("a@b.com", "s", "t", "<p>h</p>") is True
    send.assert_called_once_with("a@b.com", "s", "t", "<p>h</p>")


# --- urgent alerts -------------------------------------------------------------------------------


def _mail(subject, category=URGENT, hours_ago=1):
    return {
        "subject": subject,
        "category": category,
        "date": datetime.now(timezone.utc) - timedelta(hours=hours_ago),
    }


def test_a_batch_of_urgent_mail_is_one_notification(db, user):
    assert (
        notify_urgent(
            db, user.id, [_mail("Server down"), _mail("Invoice overdue"), _mail("Lunch", "General")]
        )
        is True
    )

    (note,) = notifications_repo.list_for_user(db, user.id)
    assert note.kind == "urgent" and note.title == "2 new urgent emails" and note.read_at is None
    assert "Server down" in note.body and "Invoice overdue" in note.body and "Lunch" not in note.body


def test_single_urgent_email_wording_and_long_lists_are_capped(db, user):
    notify_urgent(db, user.id, [_mail("Only one")])
    assert notifications_repo.list_for_user(db, user.id)[0].title == "1 new urgent email"

    notify_urgent(db, user.id, [_mail(f"Mail {i}") for i in range(6)])
    newest = notifications_repo.list_for_user(db, user.id)[0]
    assert newest.title == "6 new urgent emails" and newest.body.count("•") == 3 and newest.body.endswith("…")


def test_nothing_urgent_or_only_old_mail_makes_no_noise(db, user):
    assert (
        notify_urgent(db, user.id, [_mail("Newsletter", "Promotional"), _mail("Hello", "General")]) is False
    )
    assert (
        notify_urgent(db, user.id, [_mail("Old urgent", hours_ago=24 * 10)]) is False
    )  # e.g. a first big import
    assert notify_urgent(db, user.id, []) is False
    assert notifications_repo.list_for_user(db, user.id) == []


def test_users_can_turn_urgent_alerts_off(db, user):
    settings_repo.update(db, settings_repo.get_or_create(db, user.id), urgent_alerts=False)
    assert notify_urgent(db, user.id, [_mail("Server down")]) is False
    assert notifications_repo.list_for_user(db, user.id) == []
