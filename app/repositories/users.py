from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.security import hash_password, new_calendar_token
from ..db.models import User


def normalize_email(email: str) -> str:
    return email.strip().lower()


def get(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


def get_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == normalize_email(email)))


def get_by_calendar_token(db: Session, token: str) -> User | None:
    return db.scalar(select(User).where(User.calendar_token == token))


def create(db: Session, email: str, password: str) -> User:
    user = User(
        email=normalize_email(email),
        password_hash=hash_password(password),
        calendar_token=new_calendar_token(),
    )
    db.add(user)
    db.commit()
    return user


def rotate_calendar_token(db: Session, user: User) -> str:
    user.calendar_token = new_calendar_token()
    db.commit()
    return user.calendar_token
