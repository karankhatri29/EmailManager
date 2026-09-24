import logging
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..core.config import DEFAULT_TIMEFRAME, get_settings
from ..core.redirects import GOOGLE_PATH, MICROSOFT_PATH, remember_origin, request_origin, resolve
from ..core.security import decrypt
from ..db.models import User
from ..db.session import get_db
from ..providers import google_oauth, microsoft_oauth
from ..repositories import accounts as accounts_repo
from ..repositories import users as users_repo
from ..schemas import AccountOut
from ..services.background import SyncManager
from .deps import current_user, get_sync_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


@router.get("", response_model=list[AccountOut])
def list_accounts(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    manager: SyncManager = Depends(get_sync_manager),
):
    accounts = accounts_repo.list_for_user(db, user.id)
    last_synced = accounts_repo.last_synced_map(db, [a.id for a in accounts])
    return [
        AccountOut(
            id=a.id,
            provider=a.provider,
            email_address=a.email_address,
            status=a.status,
            last_error=a.last_error,
            last_synced_at=last_synced.get(a.id),
            syncing=manager.is_syncing(a.id),
        )
        for a in accounts
    ]


def _finish_connect(
    request: Request,
    db: Session,
    manager: SyncManager,
    *,
    provider: str,
    session_prefix: str,
    finish: Callable[[str, str, str], tuple[str, str]],
    code: str | None,
    state: str | None,
    error: str | None,
) -> RedirectResponse:
    """Shared OAuth callback: checks the session state, stores the mailbox and starts its first sync.

    Always ends in a redirect back to the app (with ?connected= or ?connect_error=).
    """
    remember_origin(request)  # the token exchange must send the same redirect address as the sign-in did
    expected_state = request.session.pop(f"{session_prefix}oauth_state", None)
    verifier = request.session.pop(f"{session_prefix}oauth_verifier", None)

    user_id = request.session.get("uid")
    user = users_repo.get(db, user_id) if user_id else None
    if user is None:
        return RedirectResponse("/?connect_error=login_required")
    if error:
        return RedirectResponse("/?connect_error=access_denied")
    if not code or not state or state != expected_state or not verifier:
        return RedirectResponse("/?connect_error=invalid_state")

    try:
        credentials_json, address = finish(code, state, verifier)
    except Exception:
        logger.exception("%s OAuth callback failed", provider)
        return RedirectResponse("/?connect_error=failed")

    account = accounts_repo.upsert(db, user.id, provider, address, credentials_json)
    manager.trigger(account.id, DEFAULT_TIMEFRAME)
    return RedirectResponse("/?connected=" + address)


@router.get("/oauth-redirects")
def oauth_redirects(request: Request, user: User = Depends(current_user)):
    """The exact redirect addresses this site sends to Google and Microsoft. Register these in their consoles."""
    settings = get_settings()
    origin = request_origin(request)
    return {
        "google": resolve(settings.google_redirect_uri, GOOGLE_PATH, origin),
        "microsoft": resolve(settings.microsoft_redirect_uri, MICROSOFT_PATH, origin),
    }


# --- Google --------------------------------------------------------------------------------------


@router.get("/connect/google")
def connect_google(request: Request, user: User = Depends(current_user)):
    """Sends the browser to Google's consent screen."""
    remember_origin(request)
    url, state, verifier = google_oauth.authorization_url()
    request.session["oauth_state"] = state
    request.session["oauth_verifier"] = verifier
    return RedirectResponse(url)


@router.get("/google/callback")
def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
    manager: SyncManager = Depends(get_sync_manager),
):
    """Google redirects here after consent."""
    return _finish_connect(
        request,
        db,
        manager,
        provider="google",
        session_prefix="",
        finish=google_oauth.finish,
        code=code,
        state=state,
        error=error,
    )


# --- Microsoft (Outlook / Microsoft 365) ---------------------------------------------------------


@router.get("/connect/microsoft")
def connect_microsoft(request: Request, user: User = Depends(current_user)):
    """Sends the browser to Microsoft's consent screen."""
    if not get_settings().microsoft_configured:
        return RedirectResponse("/?connect_error=not_configured")
    remember_origin(request)
    url, state, verifier = microsoft_oauth.authorization_url()
    request.session["ms_oauth_state"] = state
    request.session["ms_oauth_verifier"] = verifier
    return RedirectResponse(url)


@router.get("/microsoft/callback")
def microsoft_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
    manager: SyncManager = Depends(get_sync_manager),
):
    """Microsoft redirects here after consent."""
    return _finish_connect(
        request,
        db,
        manager,
        provider="microsoft",
        session_prefix="ms_",
        finish=microsoft_oauth.finish,
        code=code,
        state=state,
        error=error,
    )


# --- Disconnect ----------------------------------------------------------------------------------


@router.delete("/{account_id}", status_code=204)
def disconnect(account_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Disconnects a mailbox and deletes its stored mail and derived calendar items."""
    account = accounts_repo.get_for_user(db, user.id, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Mailbox not found")

    if account.provider == "google":
        try:
            google_oauth.revoke(decrypt(account.credentials))
        except Exception:
            logger.warning("Could not revoke Google token for account %s", account_id, exc_info=True)
    # Microsoft has no token-revocation call for this flow; the user can also remove the app at
    # account.microsoft.com/privacy/app-access to withdraw consent there.

    accounts_repo.delete(db, account)
    return Response(status_code=204)
