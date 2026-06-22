"""Shared DB helpers for the ML pipeline (loads the Supabase URL, gives a conn).

Used by every ml/ script so connection + upsert logic lives in one place.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Sequence


def load_env() -> None:
    try:
        from dotenv import load_dotenv
    except Exception:
        return
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        for name in (".env", ".env.supabase"):
            f = parent / name
            if f.exists():
                load_dotenv(f, override=(name == ".env.supabase"))


def get_conn():
    """psycopg2 connection to the Supabase Postgres. Refuses local DB."""
    load_env()
    url = os.getenv("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL not set (check .env.supabase).")
    if "localhost" in url or "127.0.0.1" in url:
        raise RuntimeError(f"DATABASE_URL points at local Postgres — export the Supabase URL first.")
    import psycopg2
    return psycopg2.connect(url)


def upsert(conn, table: str, cols: Sequence[str], rows: Iterable[Sequence],
           conflict_cols: Sequence[str], update_cols: Sequence[str] | None = None,
           page_size: int = 1000) -> int:
    """Bulk INSERT ... ON CONFLICT DO UPDATE. Returns row count attempted."""
    from psycopg2.extras import execute_values
    rows = list(rows)
    if not rows:
        return 0
    collist = ", ".join(cols)
    conflict = ", ".join(conflict_cols)
    upd = update_cols if update_cols is not None else [c for c in cols if c not in conflict_cols]
    set_clause = ", ".join(f"{c}=EXCLUDED.{c}" for c in upd) if upd else None
    action = f"DO UPDATE SET {set_clause}" if set_clause else "DO NOTHING"
    sql = (f"INSERT INTO {table} ({collist}) VALUES %s "
           f"ON CONFLICT ({conflict}) {action}")
    with conn.cursor() as cur:
        execute_values(cur, sql, rows, page_size=page_size)
    conn.commit()
    return len(rows)
