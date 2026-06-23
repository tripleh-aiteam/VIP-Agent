"""flow_features.py — add 수급 / investor-flow features to stock_features_daily.

The YouTube day-trader's method is built on WHO is buying: foreign + institution
"smart money" accumulating at lows precedes the bounce. We turn raw_investor_flows
(daily 외국인/기관 net shares from Naver) into stationary, cross-stock-comparable
features by normalizing net shares against traded volume:

    flow_for_net1       foreign net today      / 20d avg volume   (today's intensity)
    flow_inst_net1      institution net today  / 20d avg volume
    flow_for_net5       foreign 5d net         / 5d volume        (sustained accumulation)
    flow_inst_net5      institution 5d net     / 5d volume
    flow_smart_net5     (foreign+inst) 5d net  / 5d volume        (combined smart money)
    flow_for_hold_chg20 foreign holding % change over 20d         (slow accumulation trend)

Point-in-time safe: every feature uses only flows up to `date`. Where Naver has no
history (older rows), flows are imputed 0 (neutral) so the bake-off keeps those rows
instead of collapsing the training set to the flow-available window.

Usage: python ml/features/flow_features.py    (then add FLOW_COLS to bakeoff.FEATURES_X)
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

FLOW_COLS = ["flow_for_net1", "flow_inst_net1", "flow_for_net5", "flow_inst_net5",
             "flow_smart_net5", "flow_for_hold_chg20"]
CLIP = 3.0  # net/volume ratios beyond ±3 are data errors; bound them


def _add_columns(conn):
    with conn.cursor() as cur:
        for c in FLOW_COLS:
            cur.execute(f"ALTER TABLE stock_features_daily ADD COLUMN IF NOT EXISTS {c} NUMERIC")
    conn.commit()


def _compute(df: pd.DataFrame) -> pd.DataFrame:
    """df: date-sorted with inst_net, foreign_net, foreign_hold_pct, volume."""
    v = df["volume"].astype(float)
    fn, inn = df["foreign_net"].astype(float), df["inst_net"].astype(float)
    avg_v20 = v.rolling(20, min_periods=5).mean().replace(0, np.nan)
    vol5 = v.rolling(5, min_periods=2).sum().replace(0, np.nan)

    out = pd.DataFrame(index=df.index)
    out["flow_for_net1"] = fn / avg_v20
    out["flow_inst_net1"] = inn / avg_v20
    out["flow_for_net5"] = fn.rolling(5, min_periods=2).sum() / vol5
    out["flow_inst_net5"] = inn.rolling(5, min_periods=2).sum() / vol5
    out["flow_smart_net5"] = (fn + inn).rolling(5, min_periods=2).sum() / vol5
    out["flow_for_hold_chg20"] = df["foreign_hold_pct"].astype(float).diff(20)
    return out.replace([np.inf, -np.inf], np.nan).clip(-CLIP, CLIP)


def main() -> int:
    conn = get_conn()
    _add_columns(conn)
    cur = conn.cursor()

    cur.execute("SELECT DISTINCT ticker FROM raw_investor_flows ORDER BY ticker")
    tickers = [r[0] for r in cur.fetchall()]
    print(f"[flows-feat] {len(tickers)} tickers with flow data")

    from psycopg2.extras import execute_batch
    set_clause = ", ".join(f"{c}=%s" for c in FLOW_COLS)
    updated = 0
    for i, t in enumerate(tickers, 1):
        cur.execute("SELECT date, inst_net, foreign_net, foreign_hold_pct, volume "
                    "FROM raw_investor_flows WHERE ticker=%s ORDER BY date", (t,))
        recs = cur.fetchall()
        if len(recs) < 6:
            continue
        df = pd.DataFrame(recs, columns=["date", "inst_net", "foreign_net",
                                         "foreign_hold_pct", "volume"]).set_index("date")
        feat = _compute(df)
        rows = []
        for d, r in feat.iterrows():
            vals = [None if pd.isna(r[c]) else float(r[c]) for c in FLOW_COLS]
            rows.append((*vals, t, d))
        execute_batch(cur, f"UPDATE stock_features_daily SET {set_clause} "
                           "WHERE ticker=%s AND date=%s", rows, page_size=500)
        updated += len(rows)
        if i % 10 == 0:
            print(f"[flows-feat] {i}/{len(tickers)} rows updated={updated}")
    conn.commit()

    # Impute neutral 0 for the pre-flow-history period so bake-off keeps those rows.
    coalesce = ", ".join(f"{c}=COALESCE({c},0)" for c in FLOW_COLS)
    where = " OR ".join(f"{c} IS NULL" for c in FLOW_COLS)
    cur.execute(f"UPDATE stock_features_daily SET {coalesce} WHERE {where}")
    conn.commit()

    cur.execute("SELECT count(*) FROM stock_features_daily WHERE flow_for_net5 <> 0")
    nz = cur.fetchone()[0]
    print(f"[flows-feat] DONE — updated {updated} rows; {nz:,} have non-zero flow signal.")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
