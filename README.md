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
