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
