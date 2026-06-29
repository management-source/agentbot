"""Lightweight schema migrations.

This project intentionally avoids a full migration framework (Alembic) to keep
deployments simple on Render.

Instead, we run a set of **idempotent** schema changes at startup, using
`ALTER TABLE ... ADD COLUMN ...` and safe index creation.

The goals:
1) Keep old databases working after code updates.
2) Support multi-mailbox isolation (admin@, lushan@, etc.) in **one** Postgres DB.

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


def _table_exists(engine: Engine, table: str) -> bool:
    try:
        insp = inspect(engine)
        return table in insp.get_table_names()
    except Exception:
        return False


def _column_exists(engine: Engine, table: str, column: str) -> bool:
    try:
        insp = inspect(engine)
        cols = {c["name"] for c in insp.get_columns(table)}
        return column in cols
    except Exception:
        return False


def _exec_statements(engine: Engine, statements: Iterable[str]) -> None:
    """Execute statements best-effort and idempotently."""
    stmts = [s for s in statements if s and s.strip()]
    if not stmts:
        return

    with engine.begin() as conn:
        for stmt in stmts:
            try:
                conn.execute(text(stmt))
            except Exception as e:
                # Many DBs error if a column/index already exists; treat as safe.
                msg = str(e).lower()
                if any(tok in msg for tok in ("duplicate", "already exists", "exists")):
                    continue
                logger.warning("Migration statement failed", extra={"stmt": stmt, "error": str(e)})


def migrate(engine: Engine) -> None:
    """Run lightweight migrations.

    Important: This must be **safe to run repeatedly**.
    """

    # If the main table isn't there yet, Base.metadata.create_all() will create it.
    # We still run the rest of migrations opportunistically.

    # -------------------------------------------------------------------------
    # 1) ThreadTicket AI columns (added 2026-01)
    # -------------------------------------------------------------------------
    tt = "thread_tickets"
    if _table_exists(engine, tt):
        stmts: list[str] = []

        def ensure(col: str, ddl: str):
            if not _column_exists(engine, tt, col):
                stmts.append(f"ALTER TABLE {tt} ADD COLUMN {ddl}")

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
        ensure("assignee_user_id", "assignee_user_id INTEGER")

        if stmts:
            logger.info("Applying DB migrations (ticket fields)", extra={"table": tt, "count": len(stmts)})
            _exec_statements(engine, stmts)

        with engine.begin() as conn:
            try:
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_thread_tickets_assignee_user_id ON {tt}(assignee_user_id)"))
            except Exception:
                pass

    # -------------------------------------------------------------------------
    # 2) Multi-mailbox support (added 2026-02)
    #    Make mailbox a partition key everywhere.
    # -------------------------------------------------------------------------
    default_mb = (settings.monitored_mailboxes_list() or ["admin@donspremier.com.au"])[0].lower()

    # --- thread_tickets: add mailbox + gmail_thread_id and namespace thread_id ---
    if _table_exists(engine, tt):
        stmts: list[str] = []
        if not _column_exists(engine, tt, "gmail_thread_id"):
            stmts.append(f"ALTER TABLE {tt} ADD COLUMN gmail_thread_id VARCHAR")
        if not _column_exists(engine, tt, "mailbox"):
            stmts.append(f"ALTER TABLE {tt} ADD COLUMN mailbox VARCHAR")
        _exec_statements(engine, stmts)

        # Backfill and namespace IDs in a single transaction.
        # NOTE: We keep these statements defensive so old DBs upgrade safely.
        with engine.begin() as conn:
            try:
                conn.execute(
                    text(f"UPDATE {tt} SET mailbox = COALESCE(NULLIF(mailbox,''), :mb)"),
                    {"mb": default_mb},
                )
            except Exception:
                pass

            try:
                # If gmail_thread_id is missing, assume legacy thread_id stored the gmail thread id.
                conn.execute(text(f"UPDATE {tt} SET gmail_thread_id = COALESCE(NULLIF(gmail_thread_id,''), thread_id)"))
            except Exception:
                pass

            try:
                # Ensure primary identifier is namespaced as mailbox:gmail_thread_id
                conn.execute(
                    text(
                        f"""
                        UPDATE {tt}
                        SET thread_id = mailbox || ':' || gmail_thread_id
                        WHERE thread_id NOT LIKE '%:%'
                        """
                    )
                )
            except Exception:
                pass

            # Helpful indexes; safe across DBs.
            for stmt in (
                f"CREATE INDEX IF NOT EXISTS ix_thread_tickets_mailbox ON {tt}(mailbox)",
                f"CREATE INDEX IF NOT EXISTS ix_thread_tickets_gmail_thread_id ON {tt}(gmail_thread_id)",
                f"CREATE UNIQUE INDEX IF NOT EXISTS uq_thread_tickets_mb_gmail ON {tt}(mailbox, gmail_thread_id)",
            ):
                try:
                    conn.execute(text(stmt))
                except Exception:
                    pass

    # --- thread_ticket_notes / thread_ticket_audit: add mailbox + namespace thread_id ---
    for tname in ("thread_ticket_notes", "thread_ticket_audit"):
        if not _table_exists(engine, tname):
            continue

        stmts: list[str] = []
        if not _column_exists(engine, tname, "mailbox"):
            stmts.append(f"ALTER TABLE {tname} ADD COLUMN mailbox VARCHAR")
        _exec_statements(engine, stmts)

        with engine.begin() as conn:
            try:
                conn.execute(
                    text(f"UPDATE {tname} SET mailbox = COALESCE(NULLIF(mailbox,''), :mb)"),
                    {"mb": default_mb},
                )
            except Exception:
                pass

            try:
                # If thread_id is legacy (not namespaced), namespace it.
                conn.execute(
                    text(
                        f"""
                        UPDATE {tname}
                        SET thread_id = mailbox || ':' || thread_id
                        WHERE thread_id NOT LIKE '%:%'
                        """
                    )
                )
            except Exception:
                pass

            try:
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{tname}_mailbox ON {tname}(mailbox)"))
            except Exception:
                pass

    # --- blacklisted_senders: add mailbox and create unique index per mailbox ---
    bl = "blacklisted_senders"
    if _table_exists(engine, bl):
        stmts: list[str] = []
        if not _column_exists(engine, bl, "mailbox"):
            stmts.append(f"ALTER TABLE {bl} ADD COLUMN mailbox VARCHAR")
        _exec_statements(engine, stmts)

        with engine.begin() as conn:
            try:
                conn.execute(
                    text(f"UPDATE {bl} SET mailbox = COALESCE(NULLIF(mailbox,''), :mb)"),
                    {"mb": default_mb},
                )
            except Exception:
                pass

            # Drop legacy unique constraint/index on email if present (best-effort)
            try:
                conn.execute(text(f"ALTER TABLE {bl} DROP CONSTRAINT IF EXISTS blacklisted_senders_email_key"))
            except Exception:
                pass
            for stmt in (
                "DROP INDEX IF EXISTS ix_blacklisted_senders_email",
                "DROP INDEX IF EXISTS blacklisted_senders_email_key",
            ):
                try:
                    conn.execute(text(stmt))
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

    # -------------------------------------------------------------------------
    # 3) User security/profile fields (added 2026-02)
    # -------------------------------------------------------------------------
    users = "users"
    if _table_exists(engine, users):
        stmts: list[str] = []
        if not _column_exists(engine, users, "avatar_url"):
            stmts.append(f"ALTER TABLE {users} ADD COLUMN avatar_url VARCHAR")
        if not _column_exists(engine, users, "phone"):
            stmts.append(f"ALTER TABLE {users} ADD COLUMN phone VARCHAR")
        if not _column_exists(engine, users, "password_changed_at"):
            stmts.append(f"ALTER TABLE {users} ADD COLUMN password_changed_at TIMESTAMP")
        if not _column_exists(engine, users, "last_login_at"):
            stmts.append(f"ALTER TABLE {users} ADD COLUMN last_login_at TIMESTAMP")
        if not _column_exists(engine, users, "failed_login_attempts"):
            stmts.append(f"ALTER TABLE {users} ADD COLUMN failed_login_attempts INTEGER DEFAULT 0")
        if not _column_exists(engine, users, "locked_until"):
            stmts.append(f"ALTER TABLE {users} ADD COLUMN locked_until TIMESTAMP")
        if not _column_exists(engine, users, "must_change_password"):
            stmts.append(f"ALTER TABLE {users} ADD COLUMN must_change_password BOOLEAN DEFAULT FALSE")
        _exec_statements(engine, stmts)

        with engine.begin() as conn:
            try:
                conn.execute(text(f"UPDATE {users} SET failed_login_attempts = COALESCE(failed_login_attempts, 0)"))
            except Exception:
                pass
            for stmt in (
                f"CREATE INDEX IF NOT EXISTS ix_users_last_login_at ON {users}(last_login_at)",
                f"CREATE INDEX IF NOT EXISTS ix_users_locked_until ON {users}(locked_until)",
                f"CREATE INDEX IF NOT EXISTS ix_users_password_changed_at ON {users}(password_changed_at)",
            ):
                try:
                    conn.execute(text(stmt))
                except Exception:
                    pass

    # -------------------------------------------------------------------------
    # 4) Rent tracker fields (added 2026-02)
    # -------------------------------------------------------------------------
    rt = "rent_due_tracker"
    if _table_exists(engine, rt):
        stmts: list[str] = []
        if not _column_exists(engine, rt, "partial_amount"):
            stmts.append(f"ALTER TABLE {rt} ADD COLUMN partial_amount FLOAT")
        _exec_statements(engine, stmts)

    # -------------------------------------------------------------------------
    # 5) My Space planner fields (added 2026-06)
    # -------------------------------------------------------------------------
    mst = "my_space_todos"
    if _table_exists(engine, mst):
        stmts: list[str] = []
        if not _column_exists(engine, mst, "bucket"):
            stmts.append(f"ALTER TABLE {mst} ADD COLUMN bucket VARCHAR DEFAULT 'today'")
        if not _column_exists(engine, mst, "item_type"):
            stmts.append(f"ALTER TABLE {mst} ADD COLUMN item_type VARCHAR DEFAULT 'task'")
        if not _column_exists(engine, mst, "follow_up_with"):
            stmts.append(f"ALTER TABLE {mst} ADD COLUMN follow_up_with VARCHAR")
        _exec_statements(engine, stmts)

        with engine.begin() as conn:
            try:
                conn.execute(text(f"UPDATE {mst} SET bucket = COALESCE(NULLIF(bucket,''), 'today')"))
            except Exception:
                pass
            try:
                conn.execute(text(f"UPDATE {mst} SET item_type = COALESCE(NULLIF(item_type,''), 'task')"))
            except Exception:
                pass

    # -------------------------------------------------------------------------
    # 6) Compliance dashboard fields (added 2026-06)
    # -------------------------------------------------------------------------
    cp = "compliance_properties"
    if _table_exists(engine, cp):
        stmts = []
        if not _column_exists(engine, cp, "overall_reason"):
            stmts.append(f"ALTER TABLE {cp} ADD COLUMN overall_reason TEXT")
        _exec_statements(engine, stmts)

    # -------------------------------------------------------------------------
    # 7) Managed properties / compliance records (added 2026-06)
    # -------------------------------------------------------------------------
    mp = "managed_properties"
    if _table_exists(engine, mp):
        stmts = []
        if not _column_exists(engine, mp, "source"):
            stmts.append(f"ALTER TABLE {mp} ADD COLUMN source VARCHAR")
        if not _column_exists(engine, mp, "crm_property_id"):
            stmts.append(f"ALTER TABLE {mp} ADD COLUMN crm_property_id VARCHAR")
        if not _column_exists(engine, mp, "property_type"):
            stmts.append(f"ALTER TABLE {mp} ADD COLUMN property_type VARCHAR")
        if not _column_exists(engine, mp, "rental_type"):
            stmts.append(f"ALTER TABLE {mp} ADD COLUMN rental_type VARCHAR")
        if not _column_exists(engine, mp, "key_number"):
            stmts.append(f"ALTER TABLE {mp} ADD COLUMN key_number VARCHAR")
        if not _column_exists(engine, mp, "owner_is_company"):
            stmts.append(f"ALTER TABLE {mp} ADD COLUMN owner_is_company BOOLEAN DEFAULT FALSE")
        if not _column_exists(engine, mp, "tenancy_status"):
            stmts.append(f"ALTER TABLE {mp} ADD COLUMN tenancy_status VARCHAR")
        if not _column_exists(engine, mp, "owners_json"):
            stmts.append(f"ALTER TABLE {mp} ADD COLUMN owners_json TEXT")
        if not _column_exists(engine, mp, "tenants_json"):
            stmts.append(f"ALTER TABLE {mp} ADD COLUMN tenants_json TEXT")
        _exec_statements(engine, stmts)

        with engine.begin() as conn:
            for stmt in (
                f"CREATE INDEX IF NOT EXISTS ix_managed_properties_crm_property_id ON {mp}(crm_property_id)",
                f"CREATE INDEX IF NOT EXISTS ix_managed_properties_tenancy_status ON {mp}(tenancy_status)",
            ):
                try:
                    conn.execute(text(stmt))
                except Exception:
                    pass

    # -------------------------------------------------------------------------
    # 8) Tenant portal accounts + maintenance source fields (added 2026-06)
    # -------------------------------------------------------------------------
    tenants = "tenant_accounts"
    if _table_exists(engine, tenants):
        with engine.begin() as conn:
            for stmt in (
                f"CREATE INDEX IF NOT EXISTS ix_tenant_accounts_mailbox ON {tenants}(mailbox)",
                f"CREATE UNIQUE INDEX IF NOT EXISTS ix_tenant_accounts_email ON {tenants}(email)",
                f"CREATE INDEX IF NOT EXISTS ix_tenant_accounts_property_id ON {tenants}(property_id)",
                f"CREATE INDEX IF NOT EXISTS ix_tenant_accounts_is_active ON {tenants}(is_active)",
                f"CREATE INDEX IF NOT EXISTS ix_tenant_accounts_is_verified ON {tenants}(is_verified)",
            ):
                try:
                    conn.execute(text(stmt))
                except Exception:
                    pass

    mo = "maintenance_orders"
    if _table_exists(engine, mo):
        stmts = []
        if not _column_exists(engine, mo, "source"):
            stmts.append(f"ALTER TABLE {mo} ADD COLUMN source VARCHAR DEFAULT 'staff'")
        if not _column_exists(engine, mo, "tenant_account_id"):
            stmts.append(f"ALTER TABLE {mo} ADD COLUMN tenant_account_id INTEGER")
        if not _column_exists(engine, mo, "tenant_submitted_at"):
            stmts.append(f"ALTER TABLE {mo} ADD COLUMN tenant_submitted_at TIMESTAMP")
        _exec_statements(engine, stmts)

        with engine.begin() as conn:
            try:
                conn.execute(text(f"UPDATE {mo} SET source = COALESCE(NULLIF(source,''), 'staff')"))
            except Exception:
                pass
            for stmt in (
                f"CREATE INDEX IF NOT EXISTS ix_maintenance_orders_source ON {mo}(source)",
                f"CREATE INDEX IF NOT EXISTS ix_maintenance_orders_tenant_account_id ON {mo}(tenant_account_id)",
                f"CREATE INDEX IF NOT EXISTS ix_maintenance_orders_tenant_submitted_at ON {mo}(tenant_submitted_at)",
            ):
                try:
                    conn.execute(text(stmt))
                except Exception:
                    pass

    ma = "maintenance_attachments"
    if _table_exists(engine, ma):
        stmts = []
        if not _column_exists(engine, ma, "storage_path"):
            stmts.append(f"ALTER TABLE {ma} ADD COLUMN storage_path TEXT")
        if not _column_exists(engine, ma, "file_size"):
            stmts.append(f"ALTER TABLE {ma} ADD COLUMN file_size INTEGER")
        if not _column_exists(engine, ma, "uploaded_by_tenant_id"):
            stmts.append(f"ALTER TABLE {ma} ADD COLUMN uploaded_by_tenant_id INTEGER")
        _exec_statements(engine, stmts)

        with engine.begin() as conn:
            for stmt in (
                f"CREATE INDEX IF NOT EXISTS ix_maintenance_attachments_uploaded_by_tenant_id ON {ma}(uploaded_by_tenant_id)",
            ):
                try:
                    conn.execute(text(stmt))
                except Exception:
                    pass

    mt = "maintenance_tradies"
    if _table_exists(engine, mt):
        with engine.begin() as conn:
            for stmt in (
                f"CREATE INDEX IF NOT EXISTS ix_maintenance_tradies_mailbox ON {mt}(mailbox)",
                f"CREATE INDEX IF NOT EXISTS ix_maintenance_tradies_company ON {mt}(company)",
                f"CREATE INDEX IF NOT EXISTS ix_maintenance_tradies_contact_name ON {mt}(contact_name)",
                f"CREATE INDEX IF NOT EXISTS ix_maintenance_tradies_trade_type ON {mt}(trade_type)",
                f"CREATE INDEX IF NOT EXISTS ix_maintenance_tradies_email ON {mt}(email)",
                f"CREATE INDEX IF NOT EXISTS ix_maintenance_tradies_is_active ON {mt}(is_active)",
            ):
                try:
                    conn.execute(text(stmt))
                except Exception:
                    pass
