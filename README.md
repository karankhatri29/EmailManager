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
  services/                sync, background sync manager, NLP, deadlines -> dates, activities, ICS, AI summaries
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

### Google OAuth setup
Create an OAuth client in Google Cloud Console (APIs & Services → Credentials), enable the Gmail API, and either
put the downloaded JSON at `./credentials.json` or set `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`. For a web
client, register `<PUBLIC_BASE_URL>/api/accounts/google/callback` as an authorised redirect URI.

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
