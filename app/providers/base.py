from typing import Protocol


class ProviderAuthError(Exception):
    """The mailbox's credentials no longer work; the user must reconnect it."""


class MailProvider(Protocol):
    """What the sync engine needs from a mailbox, whatever the underlying service.

    To add Outlook (Microsoft Graph) or IMAP, implement this and register it in
    app/providers/__init__.py.
    """

    def list_message_ids(self, timeframe: str, limit: int | None = None) -> list[str]:
        """Ids of the messages in the timeframe (cheap: no message bodies)."""

    def fetch_message(self, message_id: str) -> dict:
        """One message as a dict with id, sender, subject, body and date (aware datetime)."""

    def export_credentials(self) -> str | None:
        """Updated credentials (plaintext JSON) if they changed while in use, e.g. a token refresh."""
