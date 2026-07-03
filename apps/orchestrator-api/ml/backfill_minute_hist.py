"""backfill_minute_hist.py — PC-side: pull Kiwoom 5-min candles into minute_bars_hist.

WHY: the replay/self-tuning engine stood on ~2 days of banked snapshots; Kiwoom's
ka10080 serves ~7 trading days of 5-min bars per call (verified live). This backfills
and TOPS UP that history daily, so Autopilot B tunes on weeks of evidence, and the
future next-30-min model (M5.6) gets a price series aligned with the flow snapshots.

Run on the PC (registered Kiwoom IP), any time:
    cd apps/orchestrator-api
    python ml/backfill_minute_hist.py
Idempotent (PK ticker+ts, ON CONFLICT DO NOTHING) — safe to run daily via Task Scheduler.
~39 calls, paced 0.4s → about a minute per run.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ml"))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT.parent.parent / ".env")
except Exception:
    pass

from sqlalchemy import text  # noqa: E402

from db.base import SessionLocal  # noqa: E402
from services import kiwoom_rest as kr  # noqa: E402
from universe import STARTER_KR  # noqa: E402

DDL = """
CREATE TABLE IF NOT EXISTS minute_bars_hist (
    ticker  TEXT NOT NULL,
    ts      TIMESTAMPTZ NOT NULL,
    open    NUMERIC, high NUMERIC, low NUMERIC, close NUMERIC,
    volume  BIGINT,
    PRIMARY KEY (ticker, ts)
)
"""


def main() -> int:
    db = SessionLocal()
    total = 0
    try:
        db.execute(text(DDL))
        db.commit()
        for code, name in STARTER_KR:
            try:
                bars = kr.minute_bars(code, tic="5", count=900) or []
            except Exception as e:
                print(f"{code} {name}: ERR {str(e)[:80]}")
                continue
            n = 0
            for b in bars:
                if not b.get("close"):
                    continue
                db.execute(text(
                    "INSERT INTO minute_bars_hist (ticker, ts, open, high, low, close, volume) "
                    "VALUES (:t, (:ts)::timestamptz, :o, :h, :l, :c, :v) "
                    "ON CONFLICT (ticker, ts) DO NOTHING"),
                    {"t": code, "ts": b["ts"] + ":00+09", "o": b.get("open"), "h": b.get("high"),
                     "l": b.get("low"), "c": b["close"], "v": b.get("volume") or 0})
                n += 1
            db.commit()
            total += n
            print(f"{code} {name}: {len(bars)} bars fetched")
            time.sleep(0.4)
        rows = db.execute(text("SELECT count(*), count(DISTINCT ts::date) FROM minute_bars_hist")).first()
        print(f"DONE: upserted pass={total} · table rows={rows[0]} · days={rows[1]}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
