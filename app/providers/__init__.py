from ..core.security import decrypt
from ..db.models import MailAccount
from .base import MailProvider, ProviderAuthError
from .gmail import GmailProvider

__all__ = ["MailProvider", "ProviderAuthError", "get_provider"]

PROVIDERS = {
    "google": GmailProvider,
    # "microsoft": GraphProvider,  # Outlook / Microsoft 365 (planned)
    # "imap": ImapProvider,        # Yahoo, iCloud, custom domains (planned)
}


def get_provider(account: MailAccount) -> MailProvider:
    try:
        provider_cls = PROVIDERS[account.provider]
    except KeyError:
        raise ValueError(f"Unsupported mail provider: {account.provider}") from None
    return provider_cls(decrypt(account.credentials))
