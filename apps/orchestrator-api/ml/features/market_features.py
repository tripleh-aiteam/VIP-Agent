"""market_features.py — add MARKET/MACRO context to stock_features_daily.

The forward test showed the per-stock models are blind to market-wide moves (they
missed a broad crash). This adds macro features that are the same across stocks on a
given date but give each model regime awareness:
    mkt_kospi_ret5     — KOSPI 5-day return
    mkt_kospi_vs_sma20 — KOSPI vs its 20d average (above/below trend)
    mkt_usdkrw_ret5    — USD/KRW 5-day change (won weakness = risk-off)
    mkt_breadth        — fraction of our universe up that day (market breadth)

Adds the columns (idempotent) and backfills them by date. Then add these 4 names to
bakeoff.FEATURES_X and re-run the bake-off.

Usage: python ml/features/market_features.py
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _db import get_conn  # noqa: E402

NEW_COLS = ["mkt_kospi_ret5", "mkt_kospi_vs_sma20", "mkt_usdkrw_ret5", "mkt_breadth"]


def _add_columns(conn):
    with conn.cursor() as cur:
        for c in NEW_COLS:
            cur.execute(f"ALTER TABLE stock_features_daily ADD COLUMN IF NOT EXISTS {c} NUMERIC")
    conn.commit()


def _market_frame() -> pd.DataFrame:
    import FinanceDataReader as fdr
    kospi = fdr.DataReader("KS11", "2014-06-01")["Close"].rename("kospi")
    fx = fdr.DataReader("USD/KRW", "2014-06-01")["Close"].rename("usdkrw")
    m = pd.concat([kospi, fx], axis=1).ffill()
    m["mkt_kospi_ret5"] = m["kospi"].pct_change(5)
    m["mkt_kospi_vs_sma20"] = m["kospi"] / m["kospi"].rolling(20).mean() - 1
    m["mkt_usdkrw_ret5"] = m["usdkrw"].pct_change(5)
    m.index = pd.to_datetime(m.index).date
    return m[["mkt_kospi_ret5", "mkt_kospi_vs_sma20", "mkt_usdkrw_ret5"]]


def main() -> int:
    conn = get_conn()
    _add_columns(conn)

    # breadth from our own universe: fraction of stocks with ret_1d > 0 per date
    breadth = pd.read_sql(
        "SELECT date, AVG(CASE WHEN ret_1d > 0 THEN 1.0 ELSE 0.0 END) AS mkt_breadth "
        "FROM stock_features_daily WHERE ret_1d IS NOT NULL GROUP BY date", conn)
    breadth["date"] = pd.to_datetime(breadth["date"]).dt.date
    breadth = breadth.set_index("date")["mkt_breadth"]

    mkt = _market_frame()
    feat = mkt.join(breadth, how="outer")
    print(f"[market] computed market features for {len(feat)} dates; updating rows...")

    rows = [(float(r.mkt_kospi_ret5) if pd.notna(r.mkt_kospi_ret5) else None,
             float(r.mkt_kospi_vs_sma20) if pd.notna(r.mkt_kospi_vs_sma20) else None,
             float(r.mkt_usdkrw_ret5) if pd.notna(r.mkt_usdkrw_ret5) else None,
             float(r.mkt_breadth) if pd.notna(r.mkt_breadth) else None,
             d) for d, r in feat.iterrows()]
    from psycopg2.extras import execute_batch
    with conn.cursor() as cur:
        execute_batch(cur,
            "UPDATE stock_features_daily SET mkt_kospi_ret5=%s, mkt_kospi_vs_sma20=%s, "
            "mkt_usdkrw_ret5=%s, mkt_breadth=%s WHERE date=%s", rows, page_size=500)
    conn.commit()
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM stock_features_daily WHERE mkt_kospi_ret5 IS NOT NULL")
    print(f"[market] DONE — {cur.fetchone()[0]:,} rows now have market features.")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
