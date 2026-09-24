from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..db.models import User
from ..db.session import get_db
from ..repositories import users as users_repo
from ..services import background


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """The logged-in user (from the session cookie), or 401."""
    user_id = request.session.get("uid")
    user = users_repo.get(db, user_id) if user_id else None
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def get_sync_manager() -> background.SyncManager:
    return background.sync_manager
