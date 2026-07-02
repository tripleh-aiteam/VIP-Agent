"""snapshot_bank.py — B2 prep: bank intraday order-flow snapshots as HISTORY.

The PC collector keeps ONE current row per ticker in realtime_snapshot (it upserts),
so no time series survives the session — and the future "next 30 minutes" model
needs exactly that series (imbalance, program flow, 수급) aligned with minute bars.
This copies fresh snapshot rows into an append-only history table every few minutes
(in-process scheduler + an endpoint for the external cron). Idempotent: PK
(ticker, ts) + ON CONFLICT DO NOTHING, so double-firing never duplicates.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

from services.logger import log

_DDL = """
CREATE TABLE IF NOT EXISTS intraday_snapshot_history (
    ticker       TEXT NOT NULL,
    ts           TIMESTAMPTZ NOT NULL,
    price        NUMERIC,
    imbalance    NUMERIC,
    best_bid     NUMERIC,
    best_ask     NUMERIC,
    foreign_net  BIGINT,
    inst_net     BIGINT,
    fin_invest   BIGINT,
    program_net  BIGINT,
    short_volume BIGINT,
    short_ratio  NUMERIC,
    banked_at    TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (ticker, ts)
)
"""


def bank(db) -> dict[str, Any]:
    """Copy fresh (≤10 min) realtime_snapshot rows into the history table."""
    try:
        db.execute(text(_DDL))
        r = db.execute(text(
            "INSERT INTO intraday_snapshot_history "
            "(ticker, ts, price, imbalance, best_bid, best_ask, foreign_net, inst_net, "
            " fin_invest, program_net, short_volume, short_ratio) "
            "SELECT ticker, ts, price, imbalance, best_bid, best_ask, foreign_net, inst_net, "
            "       fin_invest, program_net, short_volume, short_ratio "
            "FROM realtime_snapshot WHERE ts > now() - interval '10 minutes' "
            "ON CONFLICT (ticker, ts) DO NOTHING"))
        db.commit()
        total = db.execute(text("SELECT count(*) FROM intraday_snapshot_history")).scalar()
        days = db.execute(text(
            "SELECT count(DISTINCT ts::date) FROM intraday_snapshot_history")).scalar()
        return {"banked_now": r.rowcount or 0, "total_rows": int(total or 0),
                "distinct_days": int(days or 0)}
    except Exception as e:
        db.rollback()
        log.warning(f"snapshot_bank: {str(e)[:120]}")
        return {"banked_now": 0, "error": str(e)[:120]}
