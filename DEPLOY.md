# Deploying to Vercel (free, for you and some friends)

This app was built for a long-running server. On Vercel it runs as short-lived serverless functions, so a few
things behave differently (they are handled in code when the `VERCEL` variable is present):

| Local / Docker | On Vercel |
|---|---|
| A background thread syncs every 5 minutes | **Once a day** (Vercel Cron, Hobby limit) plus whenever someone presses **Sync** |
| Opening the app starts a sync if the mail is stale | It does not: the sync runs inside the **Sync** request, so it can take a while |
| Trips scan starts by itself | Runs when you press **Scan** in Trips |
| Reminders / briefings checked every minute | Checked when the daily cron runs and does not run more often |

> **Terms:** Vercel's free *Hobby* plan is for personal, non-commercial use. Fine for you and friends; charging
> users needs the paid plan (or another host).

## 1. Database (free, no card): Neon
1. Create a project at neon.tech, then copy the **pooled** connection string (the host contains `-pooler`).
   It ends with `?sslmode=require`. `postgres://` / `postgresql://` are converted automatically.
2. Create the tables from your own computer (Vercel does not run migrations):
   ```
   set DATABASE_URL=<the Neon connection string>      (PowerShell:  $env:DATABASE_URL="...")
   alembic upgrade head
   ```

## 2. Google (Gmail)
Create a **Web application** OAuth client (see the chat instructions): redirect URI
`https://<your-project>.vercel.app/api/accounts/google/callback`. Keep the consent screen in **Testing** and add
every Gmail address as a test user (they must reconnect about every 7 days).

## 3. Environment variables (Vercel: Project, Settings, Environment Variables)
Generate fresh keys: `python -m scripts.generate_keys` (prints values; do not reuse your local ones, and **back up
ENCRYPTION_KEY**: without it stored mail cannot be read).

| Name | Value |
|---|---|
| `DATABASE_URL` | Neon connection string |
| `SECRET_KEY`, `ENCRYPTION_KEY` | the generated values |
| `SESSION_HTTPS_ONLY` | `true` |
| `PUBLIC_BASE_URL` | `https://<your-project>.vercel.app` |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | from the Web client |
| `GOOGLE_REDIRECT_URI` | `https://<your-project>.vercel.app/api/accounts/google/callback` |
| `CRON_SECRET` | a long random string (Vercel Cron sends it automatically) |
| `ALLOWED_EMAILS` | `you@gmail.com,friend@gmail.com` (who may create an account) |
| `GEMINI_API_KEY` | optional; leave out to skip AI summaries |
| `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`, `MICROSOFT_REDIRECT_URI` | optional (Outlook) |

## 4. Deploy
Push `dev` to GitHub, import the repository at vercel.com/new (or `vercel` from the CLI), set the variables above,
and deploy. Then open `https://<your-project>.vercel.app/healthz`: it should answer `{"status":"ok"}`.

## If the build fails
- **"Total size exceeds 250 MB":** the spaCy stack is the big part. Tell me and I will slim the dependencies.
- **Function timeouts (10 s on Hobby without fluid compute):** in Project Settings, Functions, enable *Fluid
  Compute*; `vercel.json` asks for up to 60 s.
- **`redirect_uri_mismatch`:** the URI in Google's console differs from `GOOGLE_REDIRECT_URI` by a character.
- **Everything 500s right after deploy:** almost always a missing `SECRET_KEY` / `ENCRYPTION_KEY` / `DATABASE_URL`
  (see the function logs).
