from collections.abc import Callable

from ..core.security import decrypt
from ..db.models import MailAccount
from .base import MailProvider, ProviderAuthError
from .gmail import GmailProvider
from .microsoft import MicrosoftProvider

__all__ = ["PROVIDERS", "MailProvider", "ProviderAuthError", "get_provider"]

PROVIDERS: dict[str, Callable[[str], MailProvider]] = {
    "google": GmailProvider,
    "microsoft": MicrosoftProvider,  # Outlook / Microsoft 365 (Graph API)
    # "imap": ImapProvider,        # Yahoo, iCloud, custom domains (planned)
}


def get_provider(account: MailAccount) -> MailProvider:
    try:
        provider_cls = PROVIDERS[account.provider]
    except KeyError:
        raise ValueError(f"Unsupported mail provider: {account.provider}") from None
    return provider_cls(decrypt(account.credentials))
