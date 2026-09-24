from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from ..core.security import login_limiter, verify_password
from ..db.models import User
from ..db.session import get_db
from ..repositories import users as users_repo
from ..schemas import LoginRequest, RegisterRequest, UserOut
from .deps import current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _start_session(request: Request, user: User) -> None:
    request.session.clear()  # never keep a pre-login session
    request.session["uid"] = user.id


@router.post("/register", response_model=UserOut, status_code=201)
def register(payload: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    if users_repo.get_by_email(db, payload.email):
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    user = users_repo.create(db, payload.email, payload.password)
    _start_session(request, user)
    return user


@router.post("/login", response_model=UserOut)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    key = users_repo.normalize_email(payload.email)
    if login_limiter.is_blocked(key):
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again in a few minutes.")

    user = users_repo.get_by_email(db, payload.email)
    if not verify_password(payload.password, user.password_hash if user else None) or user is None:
        login_limiter.record_failure(key)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    login_limiter.reset(key)
    _start_session(request, user)
    return user


@router.post("/logout", status_code=204)
def logout(request: Request):
    request.session.clear()
    return Response(status_code=204)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user
