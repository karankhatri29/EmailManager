"""A labelled corpus of ordinary emails for the deadline / task extractor.

Nothing here is specific to one sender or one format: it mixes work, school, bills, travel, shopping,
newsletters and replies, plus traps that look like dates but are not (version numbers, prices, times,
ratios, years, month names inside other words) and dates that are not deadlines (sent dates, quoted
history, footers, past events).

Reference: the mail below was sent on Thursday 2026-09-24, so "tomorrow" is 2026-09-25 and
"next Wednesday" is 2026-09-30.
"""

from datetime import datetime, timezone

SENT = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)


def case(name, subject, body, expect=None, order="DMY", **extra):
    """expect: None (no date should be shown) or (kind, 'YYYY-MM-DD' | None)."""
    return {"name": name, "subject": subject, "body": body, "expect": expect, "order": order, **extra}


# --- real deadlines and events the user should see -----------------------------------------------------------

POSITIVES = [
    case(
        "submit by full date",
        "Quarterly report",
        "Hi team, please submit the report by 15 October 2026. Thanks!",
        ("deadline", "2026-10-15"),
    ),
    case(
        "invoice due on",
        "Invoice INV-204",
        "Your invoice INV-204 for 240.00 EUR is due on 3 Nov 2026.",
        ("deadline", "2026-11-03"),
    ),
    case(
        "return before",
        "Library reminder",
        "Reminder: your library book must be returned before Oct 2.",
        ("deadline", "2026-10-02"),
    ),
    case(
        "expires on numeric dmy",
        "Subscription",
        "Your subscription expires on 12/10/2026. Renew to keep access.",
        ("deadline", "2026-10-12"),
    ),
    case(
        "numeric us order", "Rent", "Rent is due 10/12/2026 by noon.", ("deadline", "2026-10-12"), order="MDY"
    ),
    case("unambiguous numeric", "Filing", "Please file the form by 10/25/2026.", ("deadline", "2026-10-25")),
    case(
        "reply by tomorrow",
        "Quick question",
        "Can you please reply by tomorrow? I need the numbers.",
        ("deadline", "2026-09-25"),
    ),
    case(
        "by eod",
        "Slides",
        "I need the final slides by EOD, please send them over.",
        ("deadline", "2026-09-24"),
    ),
    case("before weekday", "Slides", "Can you send the slides before Friday?", ("deadline", "2026-09-25")),
    case(
        "closes next weekday",
        "Registration",
        "Registration closes next Wednesday, so please sign up soon.",
        ("deadline", "2026-09-30"),
    ),
    case(
        "within n days",
        "Form",
        "Please complete the attached form within 3 days.",
        ("deadline", "2026-09-27"),
    ),
    case(
        "meeting scheduled",
        "Sync",
        "The project meeting is scheduled for 2 October at 3 pm in room 4.",
        ("event", "2026-10-02"),
    ),
    case(
        "webinar on",
        "Invitation",
        "Join our webinar on Oct 8th, 2026 to learn about the new features.",
        ("event", "2026-10-08"),
    ),
    case(
        "exam on weekday date",
        "Exam schedule",
        "Your exam is on Monday 28 September in hall B.",
        ("event", "2026-09-28"),
    ),
    case(
        "iso deadline label",
        "Grant",
        "Deadline: 2026-11-15\nPlease upload the signed agreement to the portal.",
        ("deadline", "2026-11-15"),
    ),
    case("by the ordinal", "Timesheets", "Submit your timesheet by the 30th.", ("deadline", "2026-09-30")),
    case("end of month", "Payment", "Payment is due by the end of the month.", ("deadline", "2026-09-30")),
    case(
        "date label event",
        "Workshop",
        "Workshop details\nDate: 15 October 2026\nVenue: Main hall",
        ("event", "2026-10-15"),
    ),
    case(
        "last date to apply",
        "Scholarship",
        "The last date to apply is 20/10/2026. Late applications are not accepted.",
        ("deadline", "2026-10-20"),
    ),
    case("time with date", "Renewal", "Please confirm by 5 pm on 15 October.", ("deadline", "2026-10-15")),
    case("next weekday event", "Lunch", "Are we still on for lunch next Friday?", ("event", "2026-10-02")),
    case(
        "in the subject",
        "Action needed by 30 Sep",
        "Hello, see the attached notice.",
        ("deadline", "2026-09-30"),
    ),
    case("tomorrow event", "Dinner", "Dinner with the Hendersons is tomorrow at 7.", ("event", "2026-09-25")),
    case("asap request", "Urgent request", "Please review the contract and reply ASAP.", ("asap", None)),
    case(
        "month name form",
        "Tickets",
        "You must collect your tickets before 5 December 2026.",
        ("deadline", "2026-12-05"),
    ),
    case(
        "closing date field",
        "Job posting",
        "Closing date: 12 October 2026\nSend your CV to jobs@example.com.",
        ("deadline", "2026-10-12"),
    ),
    case(
        "no later than",
        "Hand in",
        "Assignments must be handed in no later than 1 October.",
        ("deadline", "2026-10-01"),
    ),
    case(
        "valid until", "Voucher", "Your voucher is valid until 31 December 2026.", ("deadline", "2026-12-31")
    ),
    case(
        "multiple dates deadline wins",
        "Course",
        "Register by 10 October. The exam itself is on 25 October.",
        ("deadline", "2026-10-10"),
    ),
    case(
        "event plus rsvp",
        "Party",
        "The party is on 15 Oct. Please RSVP by 8 Oct.",
        ("deadline", "2026-10-08"),
    ),
    case(
        "day after tomorrow",
        "Delivery",
        "We will deliver the parcel the day after tomorrow.",
        ("event", "2026-09-26"),
    ),
    case(
        "weekday with must",
        "Dentist",
        "You must confirm your appointment by Monday.",
        ("deadline", "2026-09-28"),
    ),
]

# --- things that look like dates or deadlines but are not ------------------------------------------------------

TRAPS = [
    case(
        "version numbers", "Release notes", "Upgrade to v2.3.1 today. Version 1.5.2 fixes 12.4 known issues."
    ),
    case("prices", "Your plan", "The plan costs $12.50 per month, or 12.50 EUR, or 9.99 GBP billed yearly."),
    case("clock time only", "Call", "Call me at 10.30 am or at 4:15 pm, whichever works."),
    case("ratios and fractions", "Recipe", "Use 3/4 cup of sugar, 1/2 teaspoon of salt and 24/7 support."),
    case("scores and rooms", "Results", "You scored 3/5 in room 12/14 on chapter 4-6."),
    case("ids and phones", "Order", "Order #88-21-2026-99, call 555-1234 for help with ref 45-67."),
    case(
        "month names inside words",
        "Notes",
        "The market 5 items, a decision 3 of the board, mars 2 rover and sepia 4 prints.",
    ),
    case("modal may", "Info", "You may 5 times try again. You may find it useful. Decision may 10 people."),
    case("past delivery", "Shipped", "Your order was delivered on 12 September 2026. Thanks for shopping!"),
    case("past meeting", "Thanks", "Thanks for the meeting on 10 September, it was very helpful."),
    case(
        "bare years", "About us", "Copyright 2026 Acme Inc. Founded in 1998 and serving customers since 2015."
    ),
    case("durations", "Stats", "We have served customers for the last 3 years and 6 months, every day."),
    case(
        "habitual weekday",
        "Office",
        "Our office is closed on Mondays. We meet every Monday and every Friday.",
    ),
    case(
        "newsletter today",
        "Today's newsletter",
        "Today we announce a new feature. Today's top stories are below.",
    ),
    case("past weekday", "Update", "We launched the new site on Monday and the team celebrated on Tuesday."),
    case(
        "sent and posted dates",
        "News",
        "Posted on 20 Sep 2026 by admin. Last updated: 1 Sep 2026. Sent on 23 September.",
    ),
    case(
        "receipt dates",
        "Receipt",
        "Invoice date: 01 Sep 2026. Statement period 01 Aug - 31 Aug. Order date 03 Sep 2026.",
    ),
    case("far future", "Long term", "See you at the conference in 2030, or by 12 March 2031 at the latest."),
    case(
        "ordinal that is not a date",
        "Directions",
        "Meet me by the 3rd floor lift and turn left at the 2nd door.",
    ),
    case(
        "quoted reply history",
        "Re: Report",
        "Sounds good, thanks.\n\nOn Mon, 21 Sep 2026 at 10:00, Sam <sam@example.com> wrote:\n> Please submit the report by 15 October 2026.\n> Thanks",
    ),
    case(
        "original message block",
        "RE: Plans",
        "I will look at it.\n\n-----Original Message-----\nFrom: Sam\nSent: Tuesday, September 22, 2026 9:00 AM\nSubject: Plans\nPlease reply by 30 September.",
    ),
    case(
        "footer dates",
        "Weekly digest",
        "Here are this week's articles.\n\nRead more on our site.\n\n(c) 2026 Example Ltd. Unsubscribe | Privacy policy | Offer ends 31 Dec 2026\nSent on September 20",
    ),
    case("greeting only", "Hi", "Hi there, hope you are well. Best wishes."),
    case(
        "age and quantities", "Profile", "She is 25 years old and bought 3 items for 12 euros on the 3rd try."
    ),
    case("range in the past", "Report", "Sales rose 4.5% between 1.1 and 3.2 in the previous quarter."),
    case("negated deadline", "Policy", "There is no deadline for this and no action is required."),
    case(
        "historic date names",
        "History",
        "On 14 July 1789 the Bastille fell; the treaty of 1648 ended the war.",
    ),
]

# --- task titles ----------------------------------------------------------------------------------------------

TASKS = [
    (
        "Quarterly report",
        "Hi team, please submit the quarterly report by Friday. Thanks!",
        "Submit the quarterly report",
    ),
    ("Passport", "You need to renew your passport before it expires.", "Renew your passport"),
    ("Bill", "Reminder: pay the electricity bill.", "Pay the electricity bill"),
    ("Slides", "Could you please send the slides to Dana?", "Send the slides to Dana"),
    ("Form", "Kindly fill in the attached form and return it to the office.", "Fill in the attached form"),
    ("Contract", "We request you to sign the contract and upload a copy.", "Sign the contract"),
    ("Meeting", "Don't forget to bring your laptop to the workshop.", "Bring your laptop to the workshop"),
    ("Update", "Please make sure you confirm your attendance.", "Confirm your attendance"),
]
