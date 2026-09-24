# Email Prioritizer

A multi-user inbox assistant. Each user connects their own mailbox(es); the app ranks their mail with
rule-based NLP (spaCy), summarises the important messages with Gemini, and turns the tasks and
deadlines it finds into an editable calendar (the **Activity** tab) that can also be subscribed to
from Google, Outlook or Apple Calendar.

**Stack:** FastAPI · Pydantic · SQLAlchemy 2 + Alembic · PostgreSQL (SQLite for local dev) ·
Celery + Redis · spaCy · Gemini · vanilla JS modules + Tailwind + Plotly · Docker · pytest · ruff · mypy

## What it does

- **Accounts.** Email + password sign-up (Argon2 hashes, signed HttpOnly session cookie, login rate limiting).
- **Connect mailboxes.** "Connect Gmail" runs Google's OAuth consent flow (PKCE, read-only scope). Refresh
  tokens are stored encrypted (Fernet). Several mailboxes per user are merged into one inbox. Disconnecting
  revokes the token and deletes the mailbox's stored mail and derived activities.
  Providers are pluggable (`app/providers/`); Outlook (Microsoft Graph) and IMAP are the planned next ones.
- **Command Center.** Priority counts and charts, plus an action list ordered by deadline tier. Click an item to
  read the AI summary and original message.
- **Activity.** Every Urgent/Important email becomes a calendar item, dated from the deadline found in the
  text (or left in an "Unscheduled" list). Items are editable: drag to another day, edit, mark done, delete,
  or add your own. A private `.ics` link lets any calendar app subscribe (the link can be rotated).
- **Never blocks.** The API answers instantly from the database; mailboxes sync on background threads,
  new mail is shown as soon as it is classified and summaries fill in afterwards.
- **Broken logins are visible.** If Google revokes access, the mailbox is flagged "Reconnect" instead of failing silently.

## Architecture

```
Gmail ─► provider ─► sync_account ─► PostgreSQL ◄─ FastAPI /api/* ◄─ browser
                          │                              ▲
                          └─► activities (deadlines)     └─ /calendar/<secret>.ics  (calendar apps)
       periodic: in-process thread (dev) or Celery beat + worker (Docker)
```

- Stored email ids are `<account_id>:<provider message id>`, so a message is fetched, classified and
  summarised once. Every query is scoped to the signed-in user.
- A sync downloads only unseen messages, stores them classified, then summarises 5 at a time.
  Summaries that failed are retried on the next sync.

## Layout

```
app/
  main.py                  app factory (sessions, routers)
  api/                     auth, accounts, inbox, activities, calendar, pages
  core/                    settings, security (hashing, encryption, rate limiter), logging
  db/                      engine/session, SQLAlchemy models
  repositories/            database access (users, accounts, emails, activities)
  providers/               MailProvider interface, Gmail provider, Google OAuth flow
  services/                sync, background sync manager, NLP (classifier, temporal.py dates, actions.py tasks), activities, ICS, AI summaries
  worker/                  Celery app + periodic sync task
  templates/  static/js/   the UI (ES modules)
alembic/                   migrations
scripts/                   generate_keys.py, list_models.py
tests/{unit,integration}/
```

## Run locally (SQLite)

```
pip install -e ".[dev]"
python -m spacy download en_core_web_sm
cp .env.example .env
python -m scripts.generate_keys --write      # fills SECRET_KEY and ENCRYPTION_KEY in .env
#   also set GEMINI_API_KEY in .env, and keep the Google OAuth client file as ./credentials.json
alembic upgrade head
uvicorn app.main:app --reload                # http://localhost:8000  (API docs: /docs)
```

The periodic sync runs inside the API process by default. Then: create an account, click **Connect Gmail**.

### Dashboard and settings
The home screen leads with a "Do this first" card, then key numbers (needs action, overdue, due today, next 7 days,
on-time completion, emails received), the **Inbox explorer**, top senders and a per-mailbox breakdown. Numbers are
colour-coded by threshold (red overdue, orange due today, blue upcoming, green fine). Everything is computed in
the browser from data the app already stores.

The **Inbox explorer** is one interactive card: priority chips with live counts (and a mix bar that is a control
too), a clickable stacked-bar timeline (hours for 24h, days for 7 / 30 days), search, a Smart / Newest sort and the
email list. Every control narrows the same list and the others update to match. Tapping a sender in *Top senders*
or the *Needs action* number drills into it, and any email can become a task with the **+ Task** button.

**Settings** (gear icon, saved per device in the browser): theme (System / Light / Dark; System follows the
device live), accent colour, text size, animations, default timeframe, and switches for every dashboard number
and widget (10 numbers and 4 widgets to choose from). The calendar picks Day / Week / Month from the screen size,
and the toolbar overrides it for the current visit.

### Trips and tickets
Flights, trains, buses, movies (BookMyShow, PVR, INOX...), events and hotel stays are found in your mail and shown
as boarding-pass style cards on the **Trips** tab, with a "Coming up" card on the home screen. Each card shows the
route or title, the date and time with a countdown, the PNR / booking id (tap to copy), seats, class, passengers and
so on, and can be opened, added to a calendar app as an `.ics`, or added to the Activity calendar.

How it works (`services/bookings.py`, `services/booking_scan.py`, `api/bookings.py`):
- A rule-based parser reads the sender, subject and labelled lines ("PNR:", "Date of Journey:", "Seats:"). It
  ignores promotions, OTPs and "rate your trip" mails, notes cancellations and changes, keeps only the latest mail
  about a trip, and leaves a field out when it cannot find it instead of guessing.
- Ticket mails are usually older than the inbox window and long, so a separate background scan searches up to 180
  days back (Gmail search / Microsoft Graph search), reads each hit in full and stores it like any other email. It
  runs when the Trips tab is opened for a mailbox not scanned in the last 6 hours, or on demand with **Scan my mail**.
- Times on tickets have no time zone, so they are shown as written. Matching is tuned to typical confirmation
  layouts; a mail in an unusual layout may show with fewer fields or not be found, and **Open email** always shows
  the original.

### Layouts by screen size
The UI is arranged differently for each kind of screen (`static/css/adaptive.css`; `<html data-ui>` records which):

| Screen | Navigation | Dashboard |
|---|---|---|
| Phone (< 640px) | floating bottom tab bar | swipeable KPI carousel, timeframe chips, dialogs as bottom sheets |
| Tablet (640-1023px) | slim icon rail | 3-up KPI grid, stacked cards |
| Laptop (1024-1535px) | sidebar + top tabs | two columns (numbers/charts, up next) |
| Desktop (>= 1536px) | sidebar + top tabs | wide main area with six KPIs in a row, up next on the right |

### Google OAuth setup
Create an OAuth client in Google Cloud Console (APIs & Services → Credentials), enable the Gmail API, and either
put the downloaded JSON at `./credentials.json` or set `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`. For a web
client, register `<PUBLIC_BASE_URL>/api/accounts/google/callback` as an authorised redirect URI.

### Outlook / Microsoft 365 setup (optional)
Register an app in the Azure portal (Microsoft Entra ID → App registrations → New registration). Choose
"Accounts in any organizational directory and personal Microsoft accounts", add the redirect URI
`<PUBLIC_BASE_URL>/api/accounts/microsoft/callback` (platform: Web), add the delegated permissions `Mail.Read`,
`User.Read` and `offline_access`, then create a client secret. Set `MICROSOFT_CLIENT_ID` and
`MICROSOFT_CLIENT_SECRET`. Without them the **＋ Outlook** button shows a "not set up" message.

A user can connect any mix of Gmail and Outlook mailboxes. With two or more connected, the dashboard shows a
filter to view all of them together or one at a time (`?account_id=` on `/api/emails` and `/api/scheduler`).

> **Before selling this:** Gmail read access is a *restricted* scope. Google requires app verification and an
> annual security assessment before you can exceed 100 users; while the consent screen is in "Testing" mode,
> refresh tokens expire after 7 days. Also plan for a privacy policy, consent for sending mail text to Gemini,
> a data-retention policy and account deletion.

## Run with Docker (Postgres + Redis + API + worker + beat)

```
cp .env.example .env         # fill in the keys (see above) and Google client id/secret
docker compose up --build    # http://localhost:8000  (migrations run on start)
```

## Development

```
pytest --cov=app                     # Gmail, Gemini and Redis are mocked
ruff check app tests scripts alembic
ruff format app tests scripts alembic
mypy
pre-commit install
alembic revision --autogenerate -m "describe change"   # after editing app/db/models.py
```

CI (`.github/workflows/ci.yml`) runs lint, type-check and tests on Python 3.10 and 3.11. A test fails if
the models and migrations drift apart.

`.env`, `credentials.json`, `token.json` and `*.db` are gitignored.


## How dates and tasks are found

`app/services/temporal.py` finds the one date a message is about; `app/services/actions.py` names what it asks
you to do. Neither knows any sender, template or subject. Every date-like phrase is only a *candidate*: the
words around it are read (a deadline cue such as "by" or "due", an event word, an obligation such as "please",
a past-tense verb, "sent on"/"posted on"), quoted replies and footers are cut away first, and a candidate is
shown only when the evidence is strong enough. Version numbers, prices, clock times, ratios, bare years, past
events and habitual weekdays ("every Monday") never show up as dates; ambiguous numeric dates (03/04) follow
the region of the user's time zone. Results are stored on each email (`due_date`, `due_kind`, `due_text`) and
the Command Center shows the exact words the date came from.

`tests/nlp_corpus.py` holds labelled emails (real deadlines and events, look-alike traps, task titles) that
drive `tests/unit/test_temporal.py` and `test_actions.py`. After changing the extraction run
`python scripts/reanalyze.py` to refresh stored mail.
