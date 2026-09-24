"""Trips and tickets: flights, trains, buses, movies, events and hotels found in the user's mail."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..db.models import Email, User
from ..db.session import get_db
from ..repositories import accounts as accounts_repo
from ..repositories import emails as emails_repo
from ..schemas import EmailOut
from ..services import booking_scan
from ..services.bookings import KINDS, booking_to_ics, extract_booking, extract_bookings
from .deps import current_user

router = APIRouter(prefix="/api/bookings", tags=["bookings"])


def get_scanner() -> booking_scan.BookingScanner:
    return booking_scan.scanner


class BookingDetail(BaseModel):
    label: str
    value: str


class BookingOut(BaseModel):
    email_id: str
    kind: str
    provider: str
    title: str
    subtitle: str | None = None
    start_at: str | None = None  # local wall-clock time as written on the ticket (no time zone)
    all_day: bool = False
    end_at: str | None = None
    reference: str | None = None
    status: str  # confirmed | changed | cancelled
    details: list[BookingDetail] = []
    email_subject: str
    email_date: str | None = None
    confidence: float


class BookingsOut(BaseModel):
    items: list[BookingOut]
    scanning: bool
    last_scan: datetime | None = None
    error: str | None = None


class ScanStatus(BaseModel):
    scanning: bool
    last_scan: datetime | None = None
    error: str | None = None


def _as_dict(email: Email) -> dict:
    return {
        "id": email.id,
        "subject": email.subject,
        "body": email.body,
        "sender": email.sender,
        "date": email.date,
        "category": email.category,
    }


def _scan_error(db: Session, user: User, scanner: booking_scan.BookingScanner) -> str | None:
    """A failed scan is reported even when it flagged the mailbox as needing reconnection (no longer 'active')."""
    return scanner.error([a.id for a in accounts_repo.list_for_user(db, user.id)])


def _last_scan(db: Session, account_ids: list[int]) -> datetime | None:
    times = [emails_repo.get_synced_at(db, i, booking_scan.SCAN_KEY) for i in account_ids]
    return max((t for t in times if t), default=None)


@router.get("", response_model=BookingsOut)
def list_bookings(
    kind: str | None = Query(default=None, description=f"one of {', '.join(KINDS)}"),
    days: int = Query(default=365, ge=1, le=730),
    refresh: bool = False,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    scanner: booking_scan.BookingScanner = Depends(get_scanner),
):
    """Bookings found in the user's mail, soonest first. Starts a background scan of mailboxes that have not been
    scanned recently (or all of them with refresh=true); poll /api/bookings/status and call this again when done."""
    accounts = accounts_repo.list_active_for_user(db, user.id)
    ids = [a.id for a in accounts]
    for account in accounts:
        # Serverless hosting scans inside this request, so only when asked (not on every page load)
        if refresh or (booking_scan.is_stale(db, account.id) and not get_settings().is_serverless):
            scanner.trigger(account.id)

    if kind is not None and kind not in KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of: {', '.join(KINDS)}")
    bookings = extract_bookings(_as_dict(e) for e in emails_repo.list_in_window(db, user.id, days))
    if kind:
        bookings = [b for b in bookings if b["kind"] == kind]
    return {
        "items": bookings,
        "scanning": scanner.is_scanning(ids),
        "last_scan": _last_scan(db, ids),
        "error": _scan_error(db, user, scanner),
    }


@router.get("/status", response_model=ScanStatus)
def scan_status(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    scanner: booking_scan.BookingScanner = Depends(get_scanner),
):
    ids = [a.id for a in accounts_repo.list_active_for_user(db, user.id)]
    return {
        "scanning": scanner.is_scanning(ids),
        "last_scan": _last_scan(db, ids),
        "error": _scan_error(db, user, scanner),
    }


@router.post("/scan", response_model=ScanStatus, status_code=202)
def start_scan(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    scanner: booking_scan.BookingScanner = Depends(get_scanner),
):
    """Searches every connected mailbox for tickets and bookings now (mailboxes already scanning are skipped)."""
    ids = [a.id for a in accounts_repo.list_active_for_user(db, user.id)]
    for account_id in ids:
        scanner.trigger(account_id)
    return {"scanning": scanner.is_scanning(ids), "last_scan": _last_scan(db, ids), "error": None}


@router.get("/email", response_model=EmailOut)
def booking_email(email_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The stored mail behind a booking, for the reader. Ticket mails are often older than the inbox window."""
    email = db.get(Email, email_id)
    if email is None or email.user_id != user.id:
        raise HTTPException(status_code=404, detail="Email not found")
    return email


@router.get("/calendar")
def booking_calendar(
    email_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """One booking as an .ics file, to add to any calendar app."""
    email = db.get(Email, email_id)
    if email is None or email.user_id != user.id:
        raise HTTPException(status_code=404, detail="Booking not found")
    booking = extract_booking(_as_dict(email))
    if booking is None:
        raise HTTPException(status_code=404, detail="No booking found in that email")
    ics = booking_to_ics(booking)
    if ics is None:
        raise HTTPException(status_code=404, detail="That booking has no date to put in a calendar")
    return Response(
        content=ics,
        media_type="text/calendar",
        headers={"Content-Disposition": f'attachment; filename="{booking["kind"]}-booking.ics"'},
    )
