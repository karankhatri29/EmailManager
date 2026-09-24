from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.security import encrypt
from ..db.models import Activity, Email, MailAccount, SyncState

ACTIVE = "active"
NEEDS_REAUTH = "needs_reauth"


def list_for_user(db: Session, user_id: int) -> list[MailAccount]:
    return list(
        db.scalars(select(MailAccount).where(MailAccount.user_id == user_id).order_by(MailAccount.id))
    )


def list_active_for_user(db: Session, user_id: int) -> list[MailAccount]:
    return [a for a in list_for_user(db, user_id) if a.status == ACTIVE]


def list_all_active_ids(db: Session) -> list[int]:
    return list(db.scalars(select(MailAccount.id).where(MailAccount.status == ACTIVE)))


def get_for_user(db: Session, user_id: int, account_id: int) -> MailAccount | None:
    return db.scalar(select(MailAccount).where(MailAccount.id == account_id, MailAccount.user_id == user_id))


def upsert(
    db: Session, user_id: int, provider: str, email_address: str, credentials_json: str
) -> MailAccount:
    """Connects a mailbox, or refreshes the credentials of one that is already connected."""
    account = db.scalar(
        select(MailAccount).where(
            MailAccount.user_id == user_id,
            MailAccount.provider == provider,
            MailAccount.email_address == email_address,
        )
    )
    encrypted = encrypt(credentials_json)
    if account is None:
        account = MailAccount(
            user_id=user_id, provider=provider, email_address=email_address, credentials=encrypted
        )
        db.add(account)
    else:
        account.credentials = encrypted
        account.status = ACTIVE
        account.last_error = None
    db.commit()
    return account


def update_credentials(db: Session, account_id: int, credentials_json: str) -> None:
    account = db.get(MailAccount, account_id)
    if account is not None:
        account.credentials = encrypt(credentials_json)
        db.commit()


def mark_needs_reauth(db: Session, account_id: int, error: str) -> None:
    account = db.get(MailAccount, account_id)
    if account is not None:
        account.status = NEEDS_REAUTH
        account.last_error = error[:500]
        db.commit()


def last_synced_map(db: Session, account_ids: list[int]) -> dict:
    """account_id -> most recent successful sync time (any timeframe)."""
    if not account_ids:
        return {}
    rows = db.execute(
        select(SyncState.account_id, func.max(SyncState.synced_at))
        .where(SyncState.account_id.in_(account_ids))
        .group_by(SyncState.account_id)
    )
    return {account_id: synced_at for account_id, synced_at in rows}


def delete(db: Session, account: MailAccount) -> None:
    """Removes a mailbox and everything derived from it (mail, sync state, email-sourced activities)."""
    email_ids = select(Email.id).where(Email.account_id == account.id)
    db.execute(sql_delete(Activity).where(Activity.email_id.in_(email_ids)))
    db.execute(sql_delete(Email).where(Email.account_id == account.id))
    db.execute(sql_delete(SyncState).where(SyncState.account_id == account.id))
    db.delete(account)
    db.commit()
