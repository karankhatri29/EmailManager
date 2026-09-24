"""Booking extraction. The sample mails below are written to resemble typical confirmation emails; they are not
copies of real ones, so they show the parser handles those shapes, not that every real mail parses."""

from datetime import datetime, timezone

import pytest

from app.services.bookings import booking_to_ics, extract_booking, extract_bookings
from app.services.html_text import html_to_text

SENT = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)


def mail(subject, body, sender, id="1:m1", category="Important"):
    return {"id": id, "subject": subject, "body": body, "sender": sender, "date": SENT, "category": category}


FLIGHT = mail(
    "Your IndiGo booking is confirmed - PNR X7K2QP",
    """Booking Confirmed
PNR: X7K2QP
Flight: 6E 2341
Delhi (DEL) -> Mumbai (BOM)
Departure: Sat, 12 Oct 2026 19:30
Arrival: 12 Oct 2026 21:45
Passenger: Mr Rahul Sharma
Seat: 14A
Terminal: T2
Booked on 24 Sep 2026""",
    "IndiGo <noreply@goindigo.in>",
)

TRAIN = mail(
    "IRCTC e-Ticket: Booking Confirmed, PNR 8412345678",
    """Booking Confirmed
PNR No: 8412345678
Train No./Name: 12951 / MUMBAI RAJDHANI
Date of Journey: 15-Oct-2026
From: MUMBAI CENTRAL (MMCT)
To: NEW DELHI (NDLS)
Class: 2A
Boarding: MMCT
Passenger: 1. ANITA SHARMA 34 F CNF/B2/23/LB""",
    "IRCTC <ticketadmin@irctc.co.in>",
)

MOVIE_HTML = mail(
    "Your BookMyShow tickets for Kalki 2898 AD (Telugu)",
    """<html><body><table><tr><td>Booking ID</td><td>BMS9X4R21K</td></tr>
<tr><td>Movie</td><td>Kalki 2898 AD (Telugu)</td></tr>
<tr><td>Cinema</td><td>PVR: Phoenix Marketcity, Kurla</td></tr>
<tr><td>Date &amp; Time</td><td>Sun, 13 Oct 2026, 07:30 PM</td></tr>
<tr><td>Screen</td><td>AUDI 4</td></tr>
<tr><td>Seats</td><td>H12, H13</td></tr></table>
<p>Enjoy the show!</p></body></html>""",
    "BookMyShow <tickets@bookmyshow.com>",
)

BUS = mail(
    "redBus Ticket Confirmation - TIN 5T8R2K9Q",
    """Your ticket is confirmed
TIN: 5T8R2K9Q
Operator: Orange Travels
From: Bengaluru
To: Hyderabad
Date of Journey: 20 Oct 2026
Boarding Time: 21:30
Boarding Point: Madiwala
Seat Numbers: L12, L13""",
    "redBus <no-reply@redbus.in>",
)

HOTEL = mail(
    "Booking confirmed: stay at Taj Lands End, Mumbai",
    """Confirmation Number: 88213377
Hotel: Taj Lands End, Mumbai
Check-in: 21 Oct 2026
Check-out: 23 Oct 2026
Guests: 2 adults
Room Type: Deluxe Sea View""",
    "Booking.com <noreply@booking.com>",
)

EVENT = mail(
    "Your tickets for Zakir Khan Live",
    """Order ID: INS4471902
Event: Zakir Khan Live
Venue: Jio World Convention Centre, BKC
Date & Time: 25 Oct 2026, 8:00 PM
Tickets: 2""",
    "Paytm Insider <no-reply@insider.in>",
)


def test_flight():
    b = extract_booking(FLIGHT)
    assert b["kind"] == "flight" and b["provider"] == "IndiGo" and b["status"] == "confirmed"
    assert b["title"] == "Delhi (DEL) → Mumbai (BOM)" and b["subtitle"] == "IndiGo · 6E 2341"
    assert b["reference"] == "X7K2QP"
    assert b["start_at"] == "2026-10-12T19:30:00" and b["all_day"] is False
    details = {d["label"]: d["value"] for d in b["details"]}
    assert (
        details["Passenger"] == "Mr Rahul Sharma" and details["Seat"] == "14A" and details["Terminal"] == "T2"
    )


def test_train():
    b = extract_booking(TRAIN)
    assert b["kind"] == "train" and b["provider"] == "IRCTC" and b["reference"] == "8412345678"
    assert b["title"] == "Mumbai Central → New Delhi"
    assert b["subtitle"] == "12951 Mumbai Rajdhani"
    assert b["start_at"] == "2026-10-15" and b["all_day"] is True  # no departure time in this mail
    details = {d["label"]: d["value"] for d in b["details"]}
    assert details["Class"] == "2A" and "CNF" in details["Status / berth"]


def test_movie_from_an_html_only_mail():
    b = extract_booking(MOVIE_HTML)
    assert b["kind"] == "movie" and b["provider"] == "BookMyShow" and b["reference"] == "BMS9X4R21K"
    assert b["title"] == "Kalki 2898 AD (Telugu)"
    assert b["subtitle"] == "PVR: Phoenix Marketcity, Kurla"
    assert b["start_at"] == "2026-10-13T19:30:00"
    details = {d["label"]: d["value"] for d in b["details"]}
    assert details["Seats"] == "H12, H13" and details["Screen"] == "AUDI 4"


def test_bus():
    b = extract_booking(BUS)
    assert b["kind"] == "bus" and b["provider"] == "redBus" and b["reference"] == "5T8R2K9Q"
    assert b["title"] == "Bengaluru → Hyderabad" and b["subtitle"] == "Orange Travels"
    assert b["start_at"] == "2026-10-20T21:30:00"
    assert {d["label"]: d["value"] for d in b["details"]}["Seats"] == "L12, L13"


def test_hotel_uses_check_in_and_check_out():
    b = extract_booking(HOTEL)
    assert b["kind"] == "hotel" and b["provider"] == "Booking.com" and b["reference"] == "88213377"
    assert b["title"] == "Taj Lands End, Mumbai"
    assert b["start_at"] == "2026-10-21" and b["end_at"] == "2026-10-23" and b["all_day"] is True


def test_event():
    b = extract_booking(EVENT)
    assert b["kind"] == "event" and b["provider"] == "Paytm Insider" and b["reference"] == "INS4471902"
    assert b["title"] == "Zakir Khan Live" and b["subtitle"] == "Jio World Convention Centre, BKC"
    assert b["start_at"] == "2026-10-25T20:00:00"


def test_a_ticket_seller_that_sells_everything_is_classified_by_content():
    mail_ = mail(
        "Flight e-ticket confirmed - Booking ID MMT2298811",
        "Booking ID: MMT2298811\nFlight: AI 864\nBLR to DEL\nDeparture: 02 Nov 2026 06:15\nPassenger: Priya Nair",
        "MakeMyTrip <bookings@makemytrip.com>",
    )
    b = extract_booking(mail_)
    assert b["kind"] == "flight" and b["provider"] == "MakeMyTrip"
    assert b["title"] == "Bengaluru (BLR) → Delhi (DEL)" and b["subtitle"] == "Air India · AI 864"
    assert b["start_at"] == "2026-11-02T06:15:00"


@pytest.mark.parametrize(
    ("subject", "body", "sender", "category"),
    [
        (
            "Flat 40% off on flights! Book now",
            "Fly to Goa from Rs 2999. Offer valid till Sunday.",
            "IndiGo <offers@goindigo.in>",
            "Promotional",
        ),
        (
            "Your OTP for IRCTC login",
            "Your one time password is 482913. Do not share.",
            "IRCTC <no-reply@irctc.co.in>",
            "General",
        ),
        (
            "How was your flight?",
            "Rate your trip and tell us how it went. Flight 6E 2341 on 12 Oct.",
            "IndiGo <hello@goindigo.in>",
            "General",
        ),
        (
            "Lunch tomorrow?",
            "Are we still on for lunch at PVR mall tomorrow? Let me know.",
            "Ravi <ravi@gmail.com>",
            "General",
        ),
        (
            "Weekly newsletter",
            "Top movies this week, deals on tickets and more.",
            "BookMyShow <news@bookmyshow.com>",
            "Promotional",
        ),
    ],
)
def test_promotions_otps_feedback_requests_and_chat_are_ignored(subject, body, sender, category):
    assert extract_booking(mail(subject, body, sender, category=category)) is None


def test_cancellations_are_flagged():
    cancelled = mail(
        FLIGHT["subject"].replace("is confirmed", "has been cancelled"), FLIGHT["body"], FLIGHT["sender"]
    )
    assert extract_booking(cancelled)["status"] == "cancelled"
    changed = mail("Your flight has been rescheduled - PNR X7K2QP", FLIGHT["body"], FLIGHT["sender"])
    assert extract_booking(changed)["status"] == "changed"


def test_a_cancellation_policy_in_a_confirmation_does_not_flag_it_cancelled():
    body = FLIGHT["body"] + "\nCancellation policy: free cancellation up to 24 hours before departure."
    assert extract_booking(mail(FLIGHT["subject"], body, FLIGHT["sender"]))["status"] == "confirmed"


def test_a_year_less_date_is_read_as_the_next_occurrence():
    b = extract_booking(
        mail(
            "Booking confirmed PNR ABC123",
            "PNR: ABC123\nFlight: 6E 501\nDEL to BOM\nDeparture: 12 Oct 19:30",
            "IndiGo <a@goindigo.in>",
        )
    )
    assert b["start_at"] == "2026-10-12T19:30:00"  # mail sent 24 Sep 2026


def test_unknown_fields_are_left_out_not_guessed():
    b = extract_booking(
        mail(
            "Booking confirmed",
            "Booking ID: ZZ9911AA\nThank you for booking your flight with us. Boarding pass attached.",
            "Air India <x@airindia.in>",
        )
    )
    assert b["kind"] == "flight" and b["reference"] == "ZZ9911AA"
    assert b["start_at"] is None and b["subtitle"] == "Air India" or b["subtitle"] is None
    assert b["confidence"] < 0.6


def test_the_booking_date_is_not_mistaken_for_the_travel_date():
    b = extract_booking(
        mail(
            "E-ticket - PNR QQ11WW",
            "PNR: QQ11WW\nBooked on 01 Sep 2026\nFlight: UK 995\nDEL to BOM\nDeparture: 30 Sep 2026 08:00",
            "Vistara <a@vistara.com>",
        )
    )
    assert b["start_at"] == "2026-09-30T08:00:00"


def test_several_mails_about_one_trip_keep_only_the_latest():
    first = dict(FLIGHT, id="1:a")
    later = dict(
        FLIGHT,
        id="1:b",
        subject="Your flight has been rescheduled - PNR X7K2QP",
        date=datetime(2026, 9, 26, tzinfo=timezone.utc),
    )
    (only,) = extract_bookings([first, later])
    assert only["email_id"] == "1:b" and only["status"] == "changed"


def test_results_are_sorted_by_date_with_undated_last():
    undated = mail(
        "Booking confirmed",
        "Booking ID: ZZ9911AA\nBoarding pass attached for your flight.",
        "Air India <x@airindia.in>",
        id="1:z",
    )
    found = extract_bookings([HOTEL, undated, FLIGHT])
    assert [b["kind"] for b in found] == ["flight", "hotel", "flight"]


def test_html_is_converted_with_rows_and_entities_kept():
    text = html_to_text(
        "<div>Hello&nbsp;<b>there</b></div><table><tr><td>Seats</td><td>A1 &amp; A2</td></tr></table><script>x()</script>"
    )
    assert text == "Hello there\nSeats A1 & A2"
    assert html_to_text("plain text\n\n\n\nstays") == "plain text\n\nstays"
    assert html_to_text("") == ""


def test_calendar_file():
    ics = booking_to_ics(extract_booking(MOVIE_HTML)).decode()
    assert "SUMMARY:Movie: Kalki 2898 AD (Telugu)" in ics
    assert "DTSTART:20261013T193000" in ics and "LOCATION:PVR: Phoenix Marketcity" in ics
    assert "Reference: BMS9X4R21K" in ics
    hotel = booking_to_ics(extract_booking(HOTEL)).decode()
    assert "DTSTART;VALUE=DATE:20261021" in hotel and "DTEND;VALUE=DATE:20261023" in hotel
    assert booking_to_ics({"start_at": None}) is None
