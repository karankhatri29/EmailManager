"""Encryption of stored email text (subject, body, summary) and calendar notes."""

import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from app.core.config import get_settings
from app.db.encrypted import PREFIX, UNREADABLE, plaintext_counts, warn_if_plaintext
from app.db.models import Activity, Email
from app.repositories import emails as emails_repo
from scripts.encrypt_stored_mail import main as script_main
from scripts.encrypt_stored_mail import reencrypt_all
from tests.conftest import stored_email

SECRET_SUBJECT = "Salary review with the CEO"
SECRET_BODY = "Your new salary is 123456 rupees. Please keep this confidential."


def raw(db, column, email_id):
    """What the database file actually contains (bypasses the ORM's decryption)."""
    return db.execute(text(f"SELECT {column} FROM emails WHERE id = :id"), {"id": email_id}).scalar_one()  # noqa: S608


def with_settings(**changes):
    """Patches the settings both the column type and the key handling read."""
    current = get_settings().model_copy(update=changes)
    return (
        patch("app.db.encrypted.get_settings", return_value=current),
        patch("app.core.security.get_settings", return_value=current),
    )


class patched:
    def __init__(self, **changes):
        self.patches = with_settings(**changes)

    def __enter__(self):
        for p in self.patches:
            p.start()

    def __exit__(self, *exc):
        for p in self.patches:
            p.stop()


@pytest.fixture
def row(db, account):
    email = stored_email(
        account, "m1", subject=SECRET_SUBJECT, body=SECRET_BODY, summary="<p>Salary news</p>"
    )
    emails_repo.upsert_many(db, [email])
    return email["id"]


# --- what is stored vs what the app sees -----------------------------------------------------------


def test_the_database_holds_only_ciphertext_and_the_app_still_reads_plaintext(db, row):
    for column, secret in (("subject", SECRET_SUBJECT), ("body", SECRET_BODY), ("summary", "Salary news")):
        stored = raw(db, column, row)
        assert stored.startswith(PREFIX) and secret not in stored and "123456" not in stored, column

    db.expire_all()
    email = db.get(Email, row)
    assert (email.subject, email.body, email.summary) == (SECRET_SUBJECT, SECRET_BODY, "<p>Salary news</p>")


def test_metadata_the_database_needs_stays_readable(db, row):
    assert raw(db, "sender", row) == "Bob <bob@x.com>"
    assert raw(db, "category", row) == "General"


def test_the_same_text_encrypts_differently_each_time(db, account):
    emails_repo.upsert_many(
        db, [stored_email(account, "a", body="same"), stored_email(account, "b", body="same")]
    )
    assert raw(db, "body", f"{account.id}:a") != raw(db, "body", f"{account.id}:b")


def test_null_empty_and_lookalike_values_round_trip(db, account):
    emails_repo.upsert_many(
        db,
        [
            stored_email(account, "null", summary=None),
            stored_email(account, "empty", summary=""),  # the booking scan's "do not summarise" marker
            stored_email(account, "look", body=PREFIX + "not really a token"),
        ],
    )
    db.expire_all()
    assert db.get(Email, f"{account.id}:null").summary is None
    assert raw(db, "summary", f"{account.id}:null") is None  # NULL stays NULL (the summariser looks for it)
    assert db.get(Email, f"{account.id}:empty").summary == ""
    assert raw(db, "summary", f"{account.id}:empty") is not None  # ...and "" is not NULL
    assert db.get(Email, f"{account.id}:look").body == PREFIX + "not really a token"


def test_calendar_notes_are_encrypted_but_titles_are_not(db, user):
    activity = Activity(user_id=user.id, title="Dentist", notes="Root canal, ask about insurance")
    db.add(activity)
    db.commit()
    stored = db.execute(text("SELECT title, notes FROM activities WHERE id = :i"), {"i": activity.id}).one()
    assert stored.title == "Dentist" and stored.notes.startswith(PREFIX) and "insurance" not in stored.notes
    db.expire_all()
    assert db.get(Activity, activity.id).notes == "Root canal, ask about insurance"


# --- legacy plaintext, keys, and the switch -------------------------------------------------------------


def test_legacy_plaintext_still_reads_and_is_counted_until_converted(db, row):
    db.execute(text("UPDATE emails SET body = :b WHERE id = :id"), {"b": "old readable body", "id": row})
    db.commit()
    db.expire_all()
    assert db.get(Email, row).body == "old readable body"

    counts = plaintext_counts(db.connection())
    assert counts["emails.body"] == 1 and counts["emails.subject"] == 0

    assert reencrypt_all(db)["emails"] == 1
    db.expire_all()
    assert raw(db, "body", row).startswith(PREFIX) and "old readable" not in raw(db, "body", row)
    assert db.get(Email, row).body == "old readable body"
    assert sum(plaintext_counts(db.connection()).values()) == 0


def test_conversion_is_safe_to_repeat(db, row):
    reencrypt_all(db)
    reencrypt_all(db)
    db.expire_all()
    assert db.get(Email, row).body == SECRET_BODY


def test_a_different_key_cannot_read_the_mail_and_does_not_crash(db, row):
    with patched(encryption_key=Fernet.generate_key().decode()):
        db.expire_all()
        email = db.get(Email, row)
        assert email.body == UNREADABLE and email.subject == UNREADABLE
    db.expire_all()
    assert db.get(Email, row).body == SECRET_BODY  # the right key still works


def test_key_rotation_old_key_keeps_working_until_everything_is_re_encrypted(db, row):
    old = get_settings().encryption_key
    new = Fernet.generate_key().decode()

    with patched(encryption_key=new, encryption_keys_previous=old):
        db.expire_all()
        assert db.get(Email, row).body == SECRET_BODY  # readable with the old key listed as previous
        reencrypt_all(db)  # rewrites everything under the new key

    with patched(encryption_key=new, encryption_keys_previous=""):  # old key removed
        db.expire_all()
        assert db.get(Email, row).body == SECRET_BODY
    db.expire_all()
    assert db.get(Email, row).body == UNREADABLE  # the old key alone can no longer read it


def test_encryption_can_be_switched_off_and_existing_ciphertext_still_reads(db, account, row):
    with patched(encrypt_email_text=False):
        emails_repo.upsert_many(db, [stored_email(account, "plain", body="stored plainly")])
        assert raw(db, "body", f"{account.id}:plain") == "stored plainly"
        db.expire_all()
        assert db.get(Email, row).body == SECRET_BODY  # earlier ciphertext is still decrypted


def test_the_startup_notice_only_appears_when_plaintext_is_left(db, row, caplog):
    engine = db.get_bind()
    with caplog.at_level("WARNING", logger="app.db.encrypted"):
        warn_if_plaintext(engine)
    assert "plaintext" not in caplog.text

    db.execute(text("UPDATE emails SET body = 'readable' WHERE id = :id"), {"id": row})
    db.commit()
    with caplog.at_level("WARNING", logger="app.db.encrypted"):
        warn_if_plaintext(engine)
    assert "still readable plaintext" in caplog.text and "emails.body: 1" in caplog.text


# --- the script ---------------------------------------------------------------------------------------------------


def test_script_check_reports_leftover_plaintext_and_exits_non_zero(db, row, capsys, session_factory):
    db.execute(text("UPDATE emails SET subject = 'readable' WHERE id = :id"), {"id": row})
    db.commit()
    engine = db.get_bind()
    with patch("scripts.encrypt_stored_mail.engine", engine):
        assert script_main(["--check"]) == 1
        assert "emails.subject: 1 still plaintext" in capsys.readouterr().out
        with patch("scripts.encrypt_stored_mail.SessionLocal", session_factory):
            assert script_main([]) == 0
        assert "Values still readable as plaintext afterwards: 0" in capsys.readouterr().out
        assert script_main(["--check"]) == 0


# --- cost ------------------------------------------------------------------------------------------------------------------


def test_reading_a_thousand_emails_is_fast_enough(db, account):
    now = datetime.now(timezone.utc)
    emails_repo.upsert_many(
        db, [stored_email(account, f"m{i}", body="x" * 4000, date=now) for i in range(1000)]
    )
    db.expire_all()
    started = time.perf_counter()
    rows = emails_repo.list_in_window(db, account.user_id, 1)
    elapsed = time.perf_counter() - started
    assert len(rows) == 1000 and all(len(r.body) == 4000 for r in rows)
    assert elapsed < 3.0, f"decrypting 1000 x 4 KB took {elapsed:.2f}s"
    print(f"decrypted 1000 x 4KB in {elapsed:.2f}s")


# --- what is really in the database FILE --------------------------------------------------------------------------------


def _file_db(tmp_path):
    """A real SQLite file (the in-memory test database cannot show what is left on disk)."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    from app.db.models import MailAccount, User
    from app.db.session import enable_sqlite_foreign_keys

    path = tmp_path / "mail.db"
    engine = create_engine(f"sqlite:///{path}")
    enable_sqlite_foreign_keys(engine)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as s:
        s.add(User(id=1, email="a@b.c", password_hash="x", calendar_token="t"))
        s.flush()
        s.add(
            MailAccount(id=1, user_id=1, provider="google", email_address="me@gmail.com", credentials="tok")
        )
        s.commit()
    return path, engine, Session


def _file_bytes(path):
    return b"".join(p.read_bytes() for p in path.parent.glob(path.name + "*"))  # the file and any -wal / -shm


SECRETS = (b"123456 rupees", b"Salary review", b"secret summary")


def _old_style_rows(Session):
    """Mail stored before encryption existed."""
    with patched(encrypt_email_text=False), Session() as s:
        s.add(
            Email(
                id="1:m0", user_id=1, account_id=1, sender="boss@corp.com", subject="Salary review",
                body="Your new salary is 123456 rupees", score=2.0, category="Important",
                date=datetime.now(timezone.utc), summary="<p>secret summary</p>",
            )
        )  # fmt: skip
        s.commit()


def test_converting_old_mail_leaves_no_readable_text_in_the_file(tmp_path):
    from scripts.encrypt_stored_mail import compact

    path, engine, Session = _file_db(tmp_path)
    _old_style_rows(Session)
    assert all(secret in _file_bytes(path) for secret in SECRETS)  # the starting point: readable on disk

    with Session() as s:
        reencrypt_all(s)
    assert "Compacted" in compact(engine)
    data = _file_bytes(path)
    assert not any(secret in data for secret in SECRETS)  # gone from the file, not just hidden from the app
    with Session() as s:
        email = s.get(Email, "1:m0")
        assert email.body == "Your new salary is 123456 rupees" and email.summary == "<p>secret summary</p>"


def test_deleting_mail_overwrites_it_in_the_file(tmp_path):
    """ "Disconnect a mailbox" must really remove old readable mail: SQLite's secure_delete zeroes freed content."""
    from app.db.models import MailAccount

    path, engine, Session = _file_db(tmp_path)
    _old_style_rows(Session)
    with Session() as s:
        s.delete(s.get(MailAccount, 1))  # cascades to its emails
        s.commit()
    assert not any(secret in _file_bytes(path) for secret in SECRETS)
