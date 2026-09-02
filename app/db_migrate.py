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
import re
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


def _column_is_nullable(engine: Engine, table: str, column: str) -> bool | None:
    """Return a column's nullability, or None when it cannot be inspected."""
    try:
        insp = inspect(engine)
        for candidate in insp.get_columns(table):
            if candidate["name"] == column:
                return bool(candidate.get("nullable", True))
    except Exception:
        pass
    return None


def _quote_sqlite_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _sqlite_nullable_table_sql(
    create_sql: str,
    *,
    temporary_table: str,
    column: str,
) -> str:
    """Rewrite one SQLite column definition without its NOT NULL constraint."""
    opening = create_sql.find("(")
    if opening < 0:
        raise ValueError("SQLite table definition has no column list")

    # Locate the matching closing parenthesis while respecting quoted strings
    # and nested type/default expressions.
    closing = -1
    depth = 0
    quote: str | None = None
    bracket_quote = False
    index = opening
    while index < len(create_sql):
        char = create_sql[index]
        if bracket_quote:
            if char == "]":
                bracket_quote = False
        elif quote:
            if char == quote:
                if index + 1 < len(create_sql) and create_sql[index + 1] == quote:
                    index += 1
                else:
                    quote = None
        elif char == "[":
            bracket_quote = True
        elif char in ('"', "'", "`"):
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                closing = index
                break
        index += 1

    if closing < 0:
        raise ValueError("SQLite table definition has an unmatched column list")

    body = create_sql[opening + 1 : closing]
    segments: list[str] = []
    segment_start = 0
    depth = 0
    quote = None
    bracket_quote = False
    index = 0
    while index < len(body):
        char = body[index]
        if bracket_quote:
            if char == "]":
                bracket_quote = False
        elif quote:
            if char == quote:
                if index + 1 < len(body) and body[index + 1] == quote:
                    index += 1
                else:
                    quote = None
        elif char == "[":
            bracket_quote = True
        elif char in ('"', "'", "`"):
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            segments.append(body[segment_start:index])
            segment_start = index + 1
        index += 1
    segments.append(body[segment_start:])

    escaped_column = re.escape(column)
    column_prefix = re.compile(
        rf"^\s*(?:{escaped_column}|\"{escaped_column}\"|`{escaped_column}`|"
        rf"\[{escaped_column}\]|'{escaped_column}')(?=\s)",
        re.IGNORECASE,
    )
    not_null = re.compile(
        r"\bNOT\s+NULL\b(?:\s+ON\s+CONFLICT\s+(?:ROLLBACK|ABORT|FAIL|IGNORE|REPLACE))?",
        re.IGNORECASE,
    )

    changed = False
    for segment_index, segment in enumerate(segments):
        if not column_prefix.search(segment):
            continue
        rewritten, count = not_null.subn("", segment, count=1)
        if count != 1:
            raise ValueError(f"SQLite column {column!r} has no explicit NOT NULL constraint")
        segments[segment_index] = rewritten
        changed = True
        break

    if not changed:
        raise ValueError(f"SQLite column {column!r} was not found in the table definition")

    return (
        f"CREATE TABLE {_quote_sqlite_identifier(temporary_table)} "
        f"({','.join(segments)}){create_sql[closing + 1:]}"
    )


def _sqlite_make_column_nullable(engine: Engine, table: str, column: str) -> None:
    """Rebuild a SQLite table atomically so one existing column is nullable."""
    temporary_table = f"__migration_{table}_{column}_nullable"
    quoted_table = _quote_sqlite_identifier(table)
    quoted_temporary_table = _quote_sqlite_identifier(temporary_table)
    raw_connection = engine.raw_connection()
    cursor = None
    foreign_keys_enabled = False

    try:
        cursor = raw_connection.cursor()
        foreign_key_row = cursor.execute("PRAGMA foreign_keys").fetchone()
        foreign_keys_enabled = bool(foreign_key_row and foreign_key_row[0])

        # SQLite only accepts a foreign_keys change outside a transaction.
        raw_connection.rollback()
        cursor.execute("PRAGMA foreign_keys = OFF")
        cursor.execute("BEGIN IMMEDIATE")

        columns = cursor.execute(f"PRAGMA table_xinfo({quoted_table})").fetchall()
        target = next((row for row in columns if row[1] == column), None)
        if target is None:
            raise ValueError(f"SQLite column {table}.{column} does not exist")
        if not bool(target[3]):
            raw_connection.commit()
            return

        table_row = cursor.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        if not table_row or not table_row[0]:
            raise ValueError(f"SQLite table definition for {table!r} was not found")

        related_objects = cursor.execute(
            """
            SELECT type, name, sql
            FROM sqlite_master
            WHERE tbl_name = ?
              AND type IN ('index', 'trigger')
              AND sql IS NOT NULL
            ORDER BY CASE type WHEN 'index' THEN 0 ELSE 1 END, name
            """,
            (table,),
        ).fetchall()

        temporary_exists = cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (temporary_table,),
        ).fetchone()
        if temporary_exists:
            raise ValueError(f"Temporary migration table {temporary_table!r} already exists")

        new_table_sql = _sqlite_nullable_table_sql(
            str(table_row[0]),
            temporary_table=temporary_table,
            column=column,
        )
        cursor.execute(new_table_sql)

        # Generated columns (table_xinfo hidden values 2 and 3) cannot be
        # inserted explicitly; all ordinary columns, including the PK, can.
        copied_columns = [
            str(row[1])
            for row in columns
            if len(row) < 7 or int(row[6] or 0) == 0
        ]
        if not copied_columns:
            raise ValueError(f"SQLite table {table!r} has no copyable columns")
        column_list = ", ".join(_quote_sqlite_identifier(name) for name in copied_columns)
        cursor.execute(
            f"INSERT INTO {quoted_temporary_table} ({column_list}) "
            f"SELECT {column_list} FROM {quoted_table}"
        )
        cursor.execute(f"DROP TABLE {quoted_table}")
        cursor.execute(f"ALTER TABLE {quoted_temporary_table} RENAME TO {quoted_table}")

        for _object_type, _object_name, object_sql in related_objects:
            cursor.execute(str(object_sql))

        raw_connection.commit()
    except Exception:
        raw_connection.rollback()
        raise
    finally:
        if cursor is not None:
            try:
                cursor.execute(f"PRAGMA foreign_keys = {1 if foreign_keys_enabled else 0}")
            except Exception:
                logger.warning("Could not restore SQLite foreign_keys setting after migration")
            cursor.close()
        raw_connection.close()


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
                f"CREATE INDEX IF NOT EXISTS ix_thread_tickets_mb_status_last_message ON {tt}(mailbox, status, last_message_at)",
                f"CREATE INDEX IF NOT EXISTS ix_thread_tickets_mb_reply_status ON {tt}(mailbox, is_not_replied, status)",
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
        if not _column_exists(engine, mp, "listing_status"):
            stmts.append(f"ALTER TABLE {mp} ADD COLUMN listing_status VARCHAR DEFAULT 'OPEN'")
        if not _column_exists(engine, mp, "keys_json"):
            stmts.append(f"ALTER TABLE {mp} ADD COLUMN keys_json TEXT")
        if not _column_exists(engine, mp, "social_media_history_json"):
            stmts.append(f"ALTER TABLE {mp} ADD COLUMN social_media_history_json TEXT")
        if not _column_exists(engine, mp, "listing_inspections_json"):
            stmts.append(f"ALTER TABLE {mp} ADD COLUMN listing_inspections_json TEXT")
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
            try:
                conn.execute(
                    text(
                        f"UPDATE {mp} SET listing_status = UPPER(TRIM(listing_status)) "
                        "WHERE UPPER(TRIM(listing_status)) IN ('OPEN', 'CLOSED')"
                    )
                )
                conn.execute(
                    text(
                        f"UPDATE {mp} SET listing_status = 'OPEN' "
                        "WHERE listing_status IS NULL OR TRIM(listing_status) = '' "
                        "OR listing_status NOT IN ('OPEN', 'CLOSED')"
                    )
                )
            except Exception:
                pass
            for stmt in (
                f"CREATE INDEX IF NOT EXISTS ix_managed_properties_crm_property_id ON {mp}(crm_property_id)",
                f"CREATE INDEX IF NOT EXISTS ix_managed_properties_tenancy_status ON {mp}(tenancy_status)",
                f"CREATE INDEX IF NOT EXISTS ix_managed_properties_listing_status ON {mp}(listing_status)",
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
        stmts = []
        if not _column_exists(engine, tenants, "preferred_contact_method"):
            stmts.append(f"ALTER TABLE {tenants} ADD COLUMN preferred_contact_method VARCHAR")
        _exec_statements(engine, stmts)

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

    tprt = "tenant_password_reset_tokens"
    if _table_exists(engine, tprt):
        with engine.begin() as conn:
            for stmt in (
                f"CREATE INDEX IF NOT EXISTS ix_tenant_password_reset_tokens_tenant_account_id ON {tprt}(tenant_account_id)",
                f"CREATE UNIQUE INDEX IF NOT EXISTS ix_tenant_password_reset_tokens_token_hash ON {tprt}(token_hash)",
                f"CREATE INDEX IF NOT EXISTS ix_tenant_password_reset_tokens_expires_at ON {tprt}(expires_at)",
                f"CREATE INDEX IF NOT EXISTS ix_tenant_password_reset_tokens_used_at ON {tprt}(used_at)",
                f"CREATE INDEX IF NOT EXISTS ix_tenant_password_reset_tokens_created_at ON {tprt}(created_at)",
            ):
                try:
                    conn.execute(text(stmt))
                except Exception:
                    pass

    # -------------------------------------------------------------------------
    # 9) Custom-address inspection visits (added 2026-07)
    # -------------------------------------------------------------------------
    inspection_visits = "inspection_visits"
    if (
        _table_exists(engine, inspection_visits)
        and _column_exists(engine, inspection_visits, "property_id")
        and _column_is_nullable(engine, inspection_visits, "property_id") is False
    ):
        dialect = engine.dialect.name
        if dialect == "postgresql":
            logger.info(
                "Applying DB migration (nullable inspection property)",
                extra={"table": inspection_visits, "column": "property_id"},
            )
            _exec_statements(
                engine,
                [
                    f"ALTER TABLE {inspection_visits} "
                    "ALTER COLUMN property_id DROP NOT NULL"
                ],
            )
        elif dialect == "sqlite":
            logger.info(
                "Applying DB migration (nullable inspection property)",
                extra={"table": inspection_visits, "column": "property_id"},
            )
            try:
                _sqlite_make_column_nullable(engine, inspection_visits, "property_id")
            except Exception as exc:
                # Match the best-effort behavior of the other startup migrations
                # while leaving the original table untouched on failure.
                logger.warning(
                    "SQLite nullable-column migration failed",
                    extra={
                        "table": inspection_visits,
                        "column": "property_id",
                        "error": str(exc),
                    },
                )
