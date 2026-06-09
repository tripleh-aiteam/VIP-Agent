"""
VIP AI Platform — Idempotent column migrator (v4 bootstrap)
SQLAlchemy's create_all() only creates NEW tables — it never alters
existing ones. The meeting-feature work (v1 -> v4) added columns to
existing tables (`meetings`, `meeting_participants`) which Postgres
won't pick up on a `create_all` run.

This module runs `ALTER TABLE ADD COLUMN IF NOT EXISTS` for every new
column. Safe to call on every startup — Postgres skips columns that
already exist.

For SQLite (dev mode), SQLite supports a subset of ALTER TABLE — we
gracefully no-op on any error so dev still boots.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

from services.logger import log


# (table_name, column_name, column_type_sql)
_NEW_COLUMNS: list[tuple[str, str, str]] = [
    # v1 — meetings
    ("meetings", "is_voice", "BOOLEAN DEFAULT FALSE NOT NULL"),
    ("meetings", "voice_call_id", "UUID"),
    ("meetings", "sip_call_id", "VARCHAR(120)"),

    # v1 — meeting_participants
    ("meeting_participants", "participant_type", "VARCHAR(20) DEFAULT 'twin' NOT NULL"),
    ("meeting_participants", "for_user_id", "UUID"),
    ("meeting_participants", "meeting_authority", "VARCHAR(30) DEFAULT 'answer_factual' NOT NULL"),
    ("meeting_participants", "authorized_by_user_id", "UUID"),
    ("meeting_participants", "authorized_at", "TIMESTAMP"),
    ("meeting_participants", "session_status", "VARCHAR(20) DEFAULT 'active' NOT NULL"),
    ("meeting_participants", "left_at", "TIMESTAMP"),
    ("meeting_participants", "escalation_count", "INTEGER DEFAULT 0 NOT NULL"),
    ("meeting_participants", "commitment_count", "INTEGER DEFAULT 0 NOT NULL"),

    # Multi-tenant / white-label — per-channel Kakao credentials stored in DB
    # (so buyers self-enter them instead of the owner setting env vars). All
    # optional; the webhook/kakao_client fall back to env vars when absent, so
    # existing agents (aiglass via env) are unaffected.
    ("chatbot_channel_mappings", "kakao_access_token", "TEXT"),
    ("chatbot_channel_mappings", "kakao_rest_api_key", "VARCHAR(200)"),
    ("chatbot_channel_mappings", "webhook_secret", "VARCHAR(200)"),

    # Link a chatbot tenant to the realty app's NextAuth tenant (per-buyer login)
    ("chatbot_tenants", "app_tenant_id", "VARCHAR(64)"),

    # Schedule run tracking — so the Workflows page shows real last-run/run-count.
    ("orch_schedule_rules", "last_run_at", "TIMESTAMP"),
    ("orch_schedule_rules", "last_run_status", "VARCHAR(20)"),
    ("orch_schedule_rules", "run_count", "INTEGER DEFAULT 0"),
]


def apply(engine: Engine) -> dict:
    """Run idempotent ALTER TABLE statements. Returns a report."""
    added = []
    skipped = []
    errors = []
    dialect = engine.dialect.name  # "postgresql" | "sqlite" | ...

    with engine.begin() as conn:
        for table, column, coltype in _NEW_COLUMNS:
            sql = _build_add_column_sql(dialect, table, column, coltype)
            try:
                conn.execute(text(sql))
                added.append(f"{table}.{column}")
            except Exception as e:
                msg = str(e).lower()
                if "already exists" in msg or "duplicate column" in msg:
                    skipped.append(f"{table}.{column}")
                else:
                    log.warning(f"db_migrate_v4: ALTER {table}.{column} failed: {e}")
                    errors.append(f"{table}.{column}: {e}")

    return {
        "dialect": dialect,
        "added": added,
        "already_present": skipped,
        "errors": errors,
    }


def _build_add_column_sql(dialect: str, table: str, column: str, coltype: str) -> str:
    # Postgres supports IF NOT EXISTS on ADD COLUMN (since 9.6).
    if dialect == "postgresql":
        return f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {coltype}'
    # SQLite + others: bare ADD COLUMN; the caller catches "already exists".
    return f'ALTER TABLE {table} ADD COLUMN {column} {coltype}'
