"""model_seen.py — track when each LLM model first appeared, to badge it 'NEW' for a while.

first_seen is persisted in Postgres (Render's filesystem is ephemeral, so an in-memory map
would reset every restart and mark everything 'new'). On the FIRST-ever population the current
models are seeded as already-old, so only models that show up LATER get the NEW badge.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

NEW_DAYS = 10   # how long a freshly-appeared model wears the NEW badge


def annotate_new(db, models: list[dict], days: int = NEW_DAYS) -> list[dict]:
    """Stamp first_seen for any unseen model id and set m['is_new'] for ones seen < `days` ago."""
    from sqlalchemy import text
    try:
        db.execute(text(
            "CREATE TABLE IF NOT EXISTS model_seen ("
            " model_id TEXT PRIMARY KEY, first_seen TIMESTAMPTZ DEFAULT now())"))
        first_run = (db.execute(text("SELECT count(*) FROM model_seen")).scalar() or 0) == 0
        # First run → seed current models as already-old so we don't badge the whole list.
        seed = f"now() - interval '{int(days) + 1} days'" if first_run else "now()"
        for m in models:
            db.execute(text(
                f"INSERT INTO model_seen (model_id, first_seen) VALUES (:i, {seed}) "
                "ON CONFLICT (model_id) DO NOTHING"), {"i": m["id"]})
        db.commit()
        rows = {r[0]: r[1] for r in db.execute(text("SELECT model_id, first_seen FROM model_seen")).fetchall()}
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        for m in models:
            m.setdefault("is_new", False)
        return models
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    for m in models:
        fs = rows.get(m["id"])
        m["is_new"] = bool(fs and fs >= cutoff)
    return models
