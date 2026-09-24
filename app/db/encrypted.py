"""Transparent encryption of sensitive text columns (email subject / body / summary, calendar notes).

`EncryptedText` behaves like Text to the rest of the app: values are encrypted with the app's ENCRYPTION_KEY
just before they are written and decrypted as they are read, so nothing else needs to change. The database
(and its dumps and backups) only ever holds ciphertext.

Stored form: "enc1:" + a Fernet token. Values without that prefix are legacy plaintext (written before encryption
was switched on): they are read as they are and become encrypted the next time they are written, or all at once
with `python -m scripts.encrypt_stored_mail`.

What this does NOT do: the running app has the key, so whoever runs the server can still read the mail, and
columns the database has to filter or group on (sender address, dates, priority) stay readable.
SQL cannot search inside encrypted columns: filter in Python after loading (see repositories/emails.py).
"""

import logging

from cryptography.fernet import InvalidToken
from sqlalchemy import Text, text
from sqlalchemy.types import TypeDecorator

from ..core.config import get_settings
from ..core.security import decrypt, encrypt

logger = logging.getLogger(__name__)

PREFIX = "enc1:"
UNREADABLE = "[This message is encrypted with a different key and cannot be read.]"

_warned_unreadable = False


class EncryptedText(TypeDecorator):
    """Text that is encrypted at rest."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None or not get_settings().encrypt_email_text:
            return value
        return PREFIX + encrypt(value)

    def process_result_value(self, value, dialect):
        if value is None or not value.startswith(PREFIX):
            return value  # NULL, or legacy plaintext
        try:
            return decrypt(value[len(PREFIX) :])
        except InvalidToken:
            global _warned_unreadable
            if not _warned_unreadable:  # once per process: a wrong key affects every row
                logger.error(
                    "Stored mail could not be decrypted: is ENCRYPTION_KEY the one it was stored with?"
                )
                _warned_unreadable = True
            return UNREADABLE


# Columns that hold text encrypted with EncryptedText: (table, column). Used by the status check below.
ENCRYPTED_COLUMNS = (
    ("emails", "subject"),
    ("emails", "body"),
    ("emails", "summary"),
    ("activities", "notes"),
    ("emails", "task"),
    ("emails", "due_text"),
    ("emails", "reason"),
    ("notifications", "title"),
    ("notifications", "body"),
    ("follow_ups", "subject"),
    ("thread_summaries", "summary"),
)


def plaintext_counts(connection) -> dict[str, int]:
    """How many non-empty values in each encrypted column are still stored as readable plaintext."""
    counts = {}
    for table, column in ENCRYPTED_COLUMNS:
        query = text(
            f"SELECT COUNT(*) FROM {table} WHERE {column} IS NOT NULL AND {column} <> '' "  # noqa: S608 (fixed names)
            f"AND {column} NOT LIKE :prefix"
        )
        counts[f"{table}.{column}"] = connection.execute(query, {"prefix": PREFIX + "%"}).scalar_one()
    return counts


def warn_if_plaintext(engine) -> None:
    """Startup notice when stored text is still readable plaintext (mail stored before encryption was on)."""
    if not get_settings().encrypt_email_text:
        return
    try:
        with engine.connect() as connection:
            counts = plaintext_counts(connection)
    except Exception:  # tables not created yet (fresh install, before migrations)
        return
    if sum(counts.values()):
        details = ", ".join(f"{column}: {n}" for column, n in counts.items() if n)
        logger.warning(
            "Some stored text is still readable plaintext (%s). Encrypt it with: python -m scripts.encrypt_stored_mail",
            details,
        )
