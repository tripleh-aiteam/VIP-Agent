"""One-off migration: add Watch-&-Learn consent columns to digital_twins.

New columns on an EXISTING table aren't created by Base.metadata.create_all, so
this adds them idempotently (IF NOT EXISTS). Privacy-default OFF.

Run from apps/orchestrator-api with the Supabase URL loaded:
    python scripts/migrate_learning_consent.py
It reads ../../.env.supabase for DATABASE_URL if not already in the environment.
"""

import os
import sys
from pathlib import Path


def _load_supabase_url() -> str:
    url = os.getenv("DATABASE_URL", "")
    if url and "supabase" in url:
        return url
    env_file = Path(__file__).resolve().parents[3] / ".env.supabase"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return url


def main() -> int:
    url = _load_supabase_url()
    if not url:
        print("No DATABASE_URL (or .env.supabase) found.")
        return 1
    os.environ["DATABASE_URL"] = url
    from sqlalchemy import create_engine, text

    engine = create_engine(url, pool_pre_ping=True)
    stmts = [
        "ALTER TABLE digital_twins ADD COLUMN IF NOT EXISTS learning_consent BOOLEAN DEFAULT FALSE",
        "ALTER TABLE digital_twins ADD COLUMN IF NOT EXISTS learning_consent_at TIMESTAMP",
    ]
    with engine.begin() as conn:
        for s in stmts:
            conn.execute(text(s))
            print(f"  ✓ {s}")
        # Report current state so we can verify.
        rows = conn.execute(text(
            "SELECT count(*) AS n, count(*) FILTER (WHERE learning_consent) AS consented "
            "FROM digital_twins")).first()
        print(f"  digital_twins: {rows.n} twins, {rows.consented} with consent ON")
    print("Migration complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
