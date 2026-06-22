"""Apply ml/schema.sql to the Supabase Postgres used by the orchestrator.

Idempotent (every object is IF NOT EXISTS). Run once locally:

    python ml/apply_schema.py

DATABASE_URL is read from the environment, preferring .env.supabase (the prod
Supabase pooler URL) over .env, mirroring how main.py loads them. We never print
the password — only the masked host so you can confirm the right DB.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except Exception:
        return
    here = Path(__file__).resolve()
    # search upward for the repo root that holds .env / .env.supabase
    for parent in [here.parent, *here.parents]:
        for name in (".env", ".env.supabase"):
            f = parent / name
            if f.exists():
                # .env.supabase should win for DATABASE_URL
                load_dotenv(f, override=(name == ".env.supabase"))


def _mask(url: str) -> str:
    # postgresql://user:pass@host:port/db  ->  host:port/db
    try:
        tail = url.split("@", 1)[1]
        return tail
    except Exception:
        return "<unpar;>"


def main() -> int:
    _load_env()
    url = os.getenv("DATABASE_URL", "")
    if not url:
        print("ERROR: DATABASE_URL not set (check .env.supabase).")
        return 2
    if "localhost" in url or "127.0.0.1" in url:
        print(f"REFUSING: DATABASE_URL points at local Postgres ({_mask(url)}).")
        print("Export the Supabase URL from .env.supabase first.")
        return 2

    sql = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    print(f"[apply_schema] target DB: {_mask(url)}")
    print(f"[apply_schema] applying schema.sql ({len(sql)} chars)...")

    import psycopg2
    conn = psycopg2.connect(url)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name IN "
                "('raw_daily_prices','raw_intraday_bars','raw_news','raw_youtube',"
                "'raw_disclosures','stock_features_daily','model_registry','model_predictions') "
                "ORDER BY table_name"
            )
            tables = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()

    print(f"[apply_schema] OK - ML tables present ({len(tables)}/8):")
    for t in tables:
        print(f"    [ok] {t}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
