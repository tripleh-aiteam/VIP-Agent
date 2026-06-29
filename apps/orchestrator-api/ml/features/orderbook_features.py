"""orderbook_features.py — orderbook_levels (intraday 10-level snapshots) -> orderbook_features_daily.

Aggregates the high-frequency order-book snapshots the WebSocket collector writes into
DAILY per-(ticker, KST-date) MICROSTRUCTURE features — a genuinely NEW signal for the
ML decision engine (Method 1) that is NOT derivable from daily OHLCV:

  ob_imbalance_mean   mean over the day of order-flow imbalance (bid_qty-ask_qty)/(bid_qty+ask_qty)
  ob_imbalance_last   imbalance in the last snapshot of the day (near close)
  ob_spread_bps_mean  mean bid/ask spread in basis points (tightness/liquidity)
  ob_depth_ratio_mean mean total bid depth / total ask depth
  ob_bid_wall_rate    fraction of snapshots showing a LARGE bid wall (support pressure)
  ob_ask_wall_rate    fraction showing a LARGE ask wall (resistance pressure)
  ob_snapshots        # snapshots that day (data-quality / coverage)

⚠️ HISTORY: order-book capture started 2026-06-26, so this only has recent dates.
Historical rows (2015–2026-06-25) have NO order-book data → these columns must stay
OUT of the live FEATURES_X until enough days accumulate (a NaN feature would make the
bake-off's dropna() discard all of history). This script BANKS the signal daily so the
model can adopt it later. Run after close (e.g. in the 5 AM retrain).

Usage:  python ml/features/orderbook_features.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # ml/ on path
from _db import get_conn  # noqa: E402

DDL = """
CREATE TABLE IF NOT EXISTS orderbook_features_daily (
    ticker              text,
    date                date,
    ob_imbalance_mean   double precision,
    ob_imbalance_last   double precision,
    ob_spread_bps_mean  double precision,
    ob_depth_ratio_mean double precision,
    ob_bid_wall_rate    double precision,
    ob_ask_wall_rate    double precision,
    ob_snapshots        integer,
    PRIMARY KEY (ticker, date)
);
"""

# Per snapshot (ticker, ts): total bid/ask qty, best bid/ask, large-wall flags.
# Then per (ticker, KST date): averages + the last (near-close) imbalance.
AGG = """
WITH snap AS (
    SELECT ticker,
           (ts AT TIME ZONE 'Asia/Seoul')::date            AS d,
           ts,
           SUM(qty) FILTER (WHERE side='bid')              AS bid_qty,
           SUM(qty) FILTER (WHERE side='ask')              AS ask_qty,
           MAX(price) FILTER (WHERE side='bid')            AS best_bid,
           MIN(price) FILTER (WHERE side='ask')            AS best_ask,
           bool_or(is_large AND side='bid')                AS lg_bid,
           bool_or(is_large AND side='ask')                AS lg_ask
    FROM orderbook_levels
    GROUP BY ticker, d, ts
),
perq AS (
    SELECT ticker, d, ts,
        CASE WHEN (bid_qty+ask_qty)>0
             THEN (bid_qty-ask_qty)::float/(bid_qty+ask_qty) END           AS imb,
        CASE WHEN best_bid>0 AND best_ask>0
             THEN (best_ask-best_bid)::float/((best_ask+best_bid)/2.0)*10000 END AS spread_bps,
        CASE WHEN ask_qty>0 THEN bid_qty::float/ask_qty END                AS depth_ratio,
        lg_bid::int AS lgb, lg_ask::int AS lga,
        row_number() OVER (PARTITION BY ticker, d ORDER BY ts DESC)        AS rn
    FROM snap
)
SELECT ticker, d,
       avg(imb)                                AS ob_imbalance_mean,
       max(CASE WHEN rn=1 THEN imb END)        AS ob_imbalance_last,
       avg(spread_bps)                         AS ob_spread_bps_mean,
       avg(depth_ratio)                        AS ob_depth_ratio_mean,
       avg(lgb)                                AS ob_bid_wall_rate,
       avg(lga)                                AS ob_ask_wall_rate,
       count(*)                                AS ob_snapshots
FROM perq
GROUP BY ticker, d;
"""

UPSERT = """
INSERT INTO orderbook_features_daily
  (ticker, date, ob_imbalance_mean, ob_imbalance_last, ob_spread_bps_mean,
   ob_depth_ratio_mean, ob_bid_wall_rate, ob_ask_wall_rate, ob_snapshots)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (ticker, date) DO UPDATE SET
  ob_imbalance_mean=EXCLUDED.ob_imbalance_mean,
  ob_imbalance_last=EXCLUDED.ob_imbalance_last,
  ob_spread_bps_mean=EXCLUDED.ob_spread_bps_mean,
  ob_depth_ratio_mean=EXCLUDED.ob_depth_ratio_mean,
  ob_bid_wall_rate=EXCLUDED.ob_bid_wall_rate,
  ob_ask_wall_rate=EXCLUDED.ob_ask_wall_rate,
  ob_snapshots=EXCLUDED.ob_snapshots;
"""


def build() -> dict:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(DDL)
            cur.execute(AGG)
            rows = cur.fetchall()
            for r in rows:
                cur.execute(UPSERT, (
                    r[0], r[1],
                    r[2], r[3], r[4], r[5], r[6], r[7], int(r[8] or 0),
                ))
        conn.commit()
        return {"rows": len(rows),
                "dates": sorted({str(r[1]) for r in rows}),
                "tickers": len({r[0] for r in rows})}
    finally:
        conn.close()


if __name__ == "__main__":
    import sys as _sys
    for _s in (_sys.stdout, _sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    print("[orderbook_features]", build())
