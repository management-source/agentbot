# AgentBot (Render manual sync)

This version is optimized for **Render Web Service** deployment without a background worker.

## Key behavior

- **Fetch Now** (manual): pulls Gmail INBOX threads and stores ticket metadata in the database.
- **Incremental sync** (no date range): uses Gmail `historyId` to pull only changes since the last sync (accurate, no misses).
- **Date range sync** (with start/end): uses Gmail search with pagination; increase **Max** to fetch more threads.

## Critical: database persistence

If you use SQLite (`app.db`) on Render, you will lose data on restarts/redeploys because the filesystem is ephemeral.

For production-like accuracy, use a persistent Postgres database and set:

```
DATABASE_URL=postgresql+psycopg2://...
```

Render provides managed Postgres (paid). Alternatively use a hosted Postgres provider (Neon, Supabase, etc.).

## Required environment variables

Set these in Render (Environment tab):

- `DATABASE_URL`
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REDIRECT_URI` = `https://<your-service>.onrender.com/auth/google/callback`

Recommended:

- `MY_EMAILS` = comma-separated list of your own addresses (used to determine "from me")
- `ENABLE_SCHEDULER=false` (manual sync mode)

## Later: automatic sync

When you want continuous syncing, set `ENABLE_SCHEDULER=true` and deploy on an always-on plan, or schedule a periodic HTTP call to `/autopilot/fetch-now`.

## Production (Render paid) – Persistent Database

1) Create a Render Postgres database (paid).
2) Set `DATABASE_URL` on the Web Service.

Notes:
- If Render provides `postgres://...`, this app will normalize it to SQLAlchemy-compatible `postgresql+psycopg2://...`.
- On startup, the app creates tables automatically.

## Gmail Delegated Mailbox (no password sharing)

If you authenticate as one account (e.g., management@...) but need to read/send as another mailbox (e.g., admin@...):

1) In the *target mailbox* (admin@...), enable Gmail delegation to the authenticated user (management@...).
2) Set env var:
   - `DELEGATED_MAILBOX=admin@donspremier.com.au`

All Gmail API calls will then target that mailbox.

## Flush database (Danger zone)

In Settings, use **Flush database** to delete all tickets + sync state.
This does **not** disconnect Google.

## Security (recommended)

For production, protect the UI/API with HTTP Basic Auth:

- `UI_BASIC_AUTH_USER`
- `UI_BASIC_AUTH_PASSWORD`

This is especially important if you deploy on a public Render URL.

## Email HTML images / icons

The thread viewer now supports:
- Inline `cid:` images (existing)
- Remote images proxied through `/threads/proxy-image` (default in UI settings)
- Attachment download links
