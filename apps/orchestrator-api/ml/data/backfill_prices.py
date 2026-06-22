"""backfill_prices.py — historical daily OHLCV → raw_daily_prices (Supabase).

Port of the user's jarvis kr_daily.py, but writes to Postgres instead of parquet.
Idempotent: re-running upserts on (ticker, date), so it resumes/refreshes safely.

Usage:
    python ml/data/backfill_prices.py                 # STARTER_KR, from 2015-01-01
    python ml/data/backfill_prices.py --start 2010-01-01
    python ml/data/backfill_prices.py --all           # full KRX listing
    python ml/data/backfill_prices.py --tickers 005930 000660

Needs: pip install -r ml/requirements.txt  (FinanceDataReader, pandas, psycopg2)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # ml/ on path
from _db import get_conn, upsert            # noqa: E402
from universe import get_universe, STARTER_KR  # noqa: E402

COLS = ["ticker", "date", "open", "high", "low", "close", "volume", "change_pct", "source"]


def _rows_for(code: str, df) -> list[tuple]:
    rows = []
    for idx, r in df.iterrows():
        d = idx.date() if hasattr(idx, "date") else idx
        def g(*names):
            for n in names:
                if n in df.columns:
                    v = r[n]
                    return None if v != v else float(v)  # NaN -> None
            return None
        vol = g("Volume")
        rows.append((
            code, d, g("Open"), g("High"), g("Low"), g("Close"),
            int(vol) if vol is not None else None,
            g("Change"), "fdr",
        ))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2015-01-01")
    ap.add_argument("--all", action="store_true", help="full KRX instead of STARTER_KR")
    ap.add_argument("--tickers", nargs="+", help="explicit ticker codes")
    args = ap.parse_args()

    import FinanceDataReader as fdr

    if args.tickers:
        uni = [(t, "") for t in args.tickers]
    else:
        uni = get_universe("all" if args.all else "starter")
    print(f"[backfill] {len(uni)} tickers, start={args.start} -> raw_daily_prices")

    conn = get_conn()
    ok = empty = fail = total_rows = 0
    try:
        for i, (code, name) in enumerate(uni, 1):
            try:
                df = fdr.DataReader(code, args.start)
                if df is None or not len(df):
                    empty += 1
                    continue
                rows = _rows_for(code, df)
                n = upsert(conn, "raw_daily_prices", COLS, rows,
                           conflict_cols=["ticker", "date"])
                total_rows += n
                ok += 1
            except Exception as e:
                fail += 1
                print(f"[backfill] {code} {name} FAILED: {str(e)[:90]}")
            if i % 10 == 0:
                print(f"[backfill] {i}/{len(uni)}  ok={ok} empty={empty} fail={fail} rows={total_rows}")
    finally:
        conn.close()

    print(f"[backfill] DONE: ok={ok} empty={empty} fail={fail} total_rows={total_rows}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
