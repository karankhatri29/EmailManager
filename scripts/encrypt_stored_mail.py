"""Encrypts stored email text, or re-encrypts it with the current key.

Mail stored before encryption was switched on is readable plaintext until it is next written; this converts all of
it at once. It is also how you rotate the key:

    1. Put the OLD key in ENCRYPTION_KEYS_PREVIOUS and the NEW key in ENCRYPTION_KEY.
    2. python -m scripts.encrypt_stored_mail
    3. When it reports nothing left on an old key, remove the old key from ENCRYPTION_KEYS_PREVIOUS.

Usage:
    python -m scripts.encrypt_stored_mail            encrypt / re-encrypt everything with the current key
    python -m scripts.encrypt_stored_mail --check    only report how many values are still plaintext

It also compacts the database afterwards (VACUUM), because otherwise the old readable text stays recoverable inside the
file. Backups and copies made before the conversion still contain plaintext: delete or re-create them.
It is safe to run more than once. (With ENCRYPT_EMAIL_TEXT=false it does the opposite and writes plaintext.)
"""

import sys

from sqlalchemy import select, text
from sqlalchemy.orm.attributes import flag_modified

from app.db.encrypted import plaintext_counts
from app.db.models import Activity, Email, FollowUp, Notification, ThreadSummary
from app.db.session import SessionLocal, engine

# model, table name, the EncryptedText columns to rewrite
TARGETS = (
    (Email, "emails", ("subject", "body", "summary", "task", "due_text", "reason")),
    (Activity, "activities", ("notes",)),
    (Notification, "notifications", ("title", "body")),
    (FollowUp, "follow_ups", ("subject",)),
    (ThreadSummary, "thread_summaries", ("summary",)),
)


def reencrypt_all(db, batch: int = 500) -> dict[str, int]:
    """Rewrites every encrypted column so it is stored under the current key. Returns rows touched per table.

    Reading decrypts (with the current or a previous key, or passes legacy plaintext through); flagging the columns
    as modified makes SQLAlchemy write them back, which encrypts them again with the current key.
    """
    totals = {name: 0 for _, name, _ in TARGETS}
    for model, name, columns in TARGETS:
        last = None
        while True:
            query = select(model).order_by(model.id).limit(batch)
            if last is not None:
                query = query.where(model.id > last)
            rows = db.scalars(query).all()
            if not rows:
                break
            last = rows[-1].id
            for row in rows:
                for column in columns:
                    flag_modified(row, column)
            db.commit()
            db.expire_all()  # let go of the decrypted text before the next batch
            totals[name] += len(rows)
    return totals


def compact(engine) -> str:
    """Rewrites the database file so rows that were replaced by encrypted ones no longer linger in it.

    Databases do not erase old row versions when a row is updated: without this, readable text from before the
    conversion can still be recovered from the file. SQLite: VACUUM. PostgreSQL: VACUUM FULL (locks the two tables
    while it runs). Returns a sentence describing what was done.
    """
    dialect = engine.dialect.name
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        if dialect == "sqlite":
            connection.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
            connection.execute(text("VACUUM"))
            return "Compacted the SQLite file (VACUUM)."
        if dialect == "postgresql":
            for _, table, _ in TARGETS:
                connection.execute(text(f"VACUUM FULL {table}"))  # noqa: S608 (fixed table names)
            return "Compacted the PostgreSQL tables (VACUUM FULL)."
    return f"Not compacted: unknown database type {dialect!r}."


def main(argv: list[str]) -> int:
    if "--check" in argv:
        with engine.connect() as connection:
            counts = plaintext_counts(connection)
        for column, count in counts.items():
            print(f"{column}: {count} still plaintext")
        return 1 if any(counts.values()) else 0

    with SessionLocal() as db:
        totals = reencrypt_all(db)
    print("Re-encrypted: " + ", ".join(f"{n} {name}" for name, n in totals.items()))
    print(compact(engine))
    with engine.connect() as connection:
        left = sum(plaintext_counts(connection).values())
    print(f"Values still readable as plaintext afterwards: {left}")
    print("Note: backups and copies of the database made BEFORE this still contain the old plaintext.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
