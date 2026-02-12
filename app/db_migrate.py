"""Lightweight schema migrations.

This project intentionally avoids a full migration framework to keep deployments simple.
For new optional columns, we run idempotent ALTER TABLE statements on startup.

Supported:
  - SQLite
  - Postgres
"""

from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.config import settings


logger = logging.getLogger(__name__)


def _column_exists(engine: Engine, table: str, column: str) -> bool:
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns(table)}
    return column in cols


def _add_columns(engine: Engine, table: str, statements: Iterable[str]):
    """Execute ALTER TABLE statements best-effort and idempotently."""
    with engine.begin() as conn:
        for stmt in statements:
            try:
                conn.execute(text(stmt))
            except Exception as e:
                # Many DBs error if the column already exists; treat as safe.
                msg = str(e).lower()
                if "duplicate" in msg or "already exists" in msg or "exists" in msg:
                    continue
                logger.warning("Migration statement failed", extra={"stmt": stmt, "error": str(e)})


def migrate(engine: Engine) -> None:
    """Run lightweight migrations."""
    # ThreadTicket AI columns (added 2026-01)
    table = "thread_tickets"
    try:
        # If table doesn't exist yet, create_all will handle it.
        insp = inspect(engine)
        if table not in insp.get_table_names():
            return
    except Exception:
        return

    # SQLite and Postgres both accept: ALTER TABLE <t> ADD COLUMN <col> <type>
    # We keep types conservative.
    stmts = []

    def ensure(col: str, ddl: str):
        if not _column_exists(engine, table, col):
            stmts.append(f"ALTER TABLE {table} ADD COLUMN {ddl}")

    ensure("ai_category", "ai_category VARCHAR")
    ensure("ai_urgency", "ai_urgency INTEGER")
    ensure("ai_confidence", "ai_confidence INTEGER")
    ensure("ai_reasons", "ai_reasons TEXT")
    ensure("ai_summary", "ai_summary TEXT")
    ensure("ai_source_hash", "ai_source_hash VARCHAR")
    ensure("ai_last_scored_at", "ai_last_scored_at TIMESTAMP")
    ensure("ai_draft_subject", "ai_draft_subject VARCHAR")
    ensure("ai_draft_body", "ai_draft_body TEXT")
    ensure("ai_draft_updated_at", "ai_draft_updated_at TIMESTAMP")

    if stmts:
        logger.info("Applying DB migrations", extra={"count": len(stmts)})
        _add_columns(engine, table, stmts)
# Mailbox multi-inbox support (added 2026-02)
default_mb = (settings.monitored_mailboxes_list() or ["admin@donspremier.com.au"])[0].lower()

# --- thread_tickets: add mailbox + gmail_thread_id and namespace thread_id ---
tt = "thread_tickets"
try:
    insp = inspect(engine)
    if tt in insp.get_table_names():
        tt_stmts = []
        if not _column_exists(engine, tt, "gmail_thread_id"):
            tt_stmts.append(f"ALTER TABLE {tt} ADD COLUMN gmail_thread_id VARCHAR")
        if not _column_exists(engine, tt, "mailbox"):
            tt_stmts.append(f"ALTER TABLE {tt} ADD COLUMN mailbox VARCHAR")
        _add_columns(engine, tt, tt_stmts)

        # Backfill + namespace in a single transaction
        with engine.begin() as conn:
            # Backfill mailbox and gmail_thread_id for legacy rows
            conn.execute(text(f"UPDATE {tt} SET mailbox = COALESCE(NULLIF(mailbox,''), :mb)"), {"mb": default_mb})
            conn.execute(text(f"UPDATE {tt} SET gmail_thread_id = COALESCE(NULLIF(gmail_thread_id,''), thread_id)"))

            # Namespace primary key thread_id if not already namespaced
            conn.execute(text(f"""
                UPDATE {tt}
                SET thread_id = mailbox || ':' || gmail_thread_id
                WHERE thread_id NOT LIKE '%:%'
            """))

            # Helpful indexes (idempotent)
            try:
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_thread_tickets_mailbox ON {tt}(mailbox)"))
            except Exception:
                pass
            try:
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_thread_tickets_gmail_thread_id ON {tt}(gmail_thread_id)"))
            except Exception:
                pass
            try:
                conn.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS uq_thread_tickets_mb_gmail ON {tt}(mailbox, gmail_thread_id)"))
            except Exception:
                pass
except Exception:
    pass

# --- thread_ticket_notes / audit: add mailbox and namespace thread_id ---
for tname in ("thread_ticket_notes", "thread_ticket_audit"):
    try:
        insp = inspect(engine)
        if tname in insp.get_table_names():
            st = []
            if not _column_exists(engine, tname, "mailbox"):
                st.append(f"ALTER TABLE {tname} ADD COLUMN mailbox VARCHAR")
            _add_columns(engine, tname, st)
            with engine.begin() as conn:
                conn.execute(text(f"UPDATE {tname} SET mailbox = COALESCE(NULLIF(mailbox,''), :mb)"), {"mb": default_mb})
                conn.execute(text(f"""
                    UPDATE {tname}
                    SET thread_id = mailbox || ':' || thread_id
                    WHERE thread_id NOT LIKE '%:%'
                """))
                try:
                    conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{tname}_mailbox ON {tname}(mailbox)"))
                except Exception:
                    pass
    except Exception:
        pass

# --- blacklisted_senders: add mailbox and create unique index per mailbox ---
bl = "blacklisted_senders"
try:
    insp = inspect(engine)
    if bl in insp.get_table_names():
        st = []
        if not _column_exists(engine, bl, "mailbox"):
            st.append(f"ALTER TABLE {bl} ADD COLUMN mailbox VARCHAR")
        _add_columns(engine, bl, st)
        with engine.begin() as conn:
            conn.execute(text(f"UPDATE {bl} SET mailbox = COALESCE(NULLIF(mailbox,''), :mb)"), {"mb": default_mb})
            # Drop legacy unique constraint (Postgres) if present; ignore failures.
            try:
                conn.execute(text(f"ALTER TABLE {bl} DROP CONSTRAINT IF EXISTS blacklisted_senders_email_key"))
            except Exception:
                pass
            # SQLite uses indexes for UNIQUE(email); ignore if not present.
            try:
                conn.execute(text(f"DROP INDEX IF EXISTS ix_blacklisted_senders_email"))
            except Exception:
                pass
            try:
                conn.execute(text(f"DROP INDEX IF EXISTS blacklisted_senders_email_key"))
            except Exception:
                pass
            try:
                conn.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS uq_blacklisted_senders_mb_email ON {bl}(mailbox, email)"))
            except Exception:
                pass
            try:
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_blacklisted_senders_mailbox ON {bl}(mailbox)"))
            except Exception:
                pass
except Exception:
    pass

