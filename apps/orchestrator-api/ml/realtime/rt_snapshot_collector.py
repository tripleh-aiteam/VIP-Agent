"""rt_snapshot_collector — PC-side bridge: Kiwoom 실전 REST -> Supabase realtime_snapshot.

WHY: Kiwoom 실전 only allows the REGISTERED terminal/IP (your PC). Render runs on a
different IP, so it can't call Kiwoom directly. Solution: this runs on YOUR PC (the
registered IP), polls the live order book + intraday 수급 for the universe, and writes
a snapshot row per ticker to Supabase. The website (Render) then just READS that table
— live data everywhere, no Render-IP whitelisting, and the page stays fast.

Run on the PC during market hours (leave it on):
    cd apps/orchestrator-api
    python ml/realtime/rt_snapshot_collector.py            # ~every 30s, 09:00-15:30 KST
    python ml/realtime/rt_snapshot_collector.py --once     # single pass (test)
    python ml/realtime/rt_snapshot_collector.py --interval 20

Needs the 실전 KIWOOM_APP_KEY/SECRET in .env (already set) + the PC IP registered.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # orchestrator-api/
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ml"))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT.parent.parent / ".env")        # repo-root .env (KIWOOM keys)
except Exception:
    pass

from services import kiwoom_rest as kr               # noqa: E402
from services import orderbook_memory as obm          # noqa: E402
from services import minute_bars as mb                # noqa: E402
from _db import get_conn                             # noqa: E402
from universe import STARTER_KR                      # noqa: E402

KST = timezone(timedelta(hours=9))
UNIVERSE = [code for code, _ in STARTER_KR]

DDL = """
CREATE TABLE IF NOT EXISTS realtime_snapshot (
    ticker      TEXT PRIMARY KEY,
    ts          TIMESTAMPTZ DEFAULT now(),
    price       NUMERIC,
    imbalance   NUMERIC,
    best_bid    NUMERIC, best_ask NUMERIC,
    foreign_net BIGINT, inst_net BIGINT, fin_invest BIGINT, program_net BIGINT,
    env         TEXT
);
ALTER TABLE realtime_snapshot ADD COLUMN IF NOT EXISTS short_volume BIGINT;
ALTER TABLE realtime_snapshot ADD COLUMN IF NOT EXISTS short_ratio NUMERIC;
"""

# 공매도 (short-selling, ka10014) updates DAILY, not intraday — fetch every N passes
# (not every 30s) to avoid hammering Kiwoom. _ss_cache holds the last good value per code.
_ss_cache: dict = {}
_ss_pass = {"n": 0}


def _market_open() -> bool:
    n = datetime.now(KST)
    return n.weekday() < 5 and 540 <= (n.hour * 60 + n.minute) <= 931


def _ensure(conn):
    with conn.cursor() as cur:
        cur.execute(DDL)
    conn.commit()
    obm.ensure_tables(conn)             # orderbook_levels + orderbook_memory (deep book)
    mb.ensure_tables(conn)              # raw_minute_prices (intraday candles)


def _one_pass(conn) -> int:
    base = getattr(kr, "_active_base", "") or ""
    env = "모의" if "mockapi" in base else "실전"
    _ss_pass["n"] += 1
    refresh_ss = (_ss_pass["n"] == 1 or _ss_pass["n"] % 20 == 0)   # 공매도 ~every 10 min
    rows = []
    for tk in UNIVERSE:
        sig = kr.realtime_signals(tk)               # order_book + flows + program
        ob = sig.get("order_book") or {}
        fl = sig.get("flows") or {}
        pr = sig.get("program") or {}
        if not ob and not fl:
            continue
        if refresh_ss:
            try:
                ss = kr.short_selling(tk) or {}
                if ss.get("short_volume") is not None:
                    _ss_cache[tk] = (ss.get("short_volume"), ss.get("short_ratio"))
            except Exception:
                pass
        sv, sr = _ss_cache.get(tk, (None, None))
        # DEEP ORDER-BOOK MEMORY: persist the 10+10 levels + remember scrolled-out ones
        try:
            if ob.get("levels"):
                obm.record(conn, tk, ob["levels"], datetime.now(timezone.utc))
        except Exception as e:
            print(f"  obm {tk}: {str(e)[:80]}")
        # INTRADAY MINUTE CANDLES — only ~every 1 min (bars change once/min); the
        # volatility baseline for day-trade feasibility reads these.
        try:
            if _ss_pass["n"] % 2 == 0:
                bars = kr.minute_bars(tk, "1", count=200)
                if bars:
                    mb.record(conn, tk, bars)
        except Exception as e:
            print(f"  minbars {tk}: {str(e)[:80]}")
        rows.append((tk, fl.get("price") or ob.get("best_bid"), ob.get("imbalance"),
                     ob.get("best_bid"), ob.get("best_ask"), fl.get("foreign"),
                     fl.get("institution"), fl.get("fin_invest"), pr.get("net_amt"),
                     sv, sr, getattr(kr, "_active_base", "") and env))
    if not rows:
        return 0
    with conn.cursor() as cur:
        from psycopg2.extras import execute_values
        execute_values(cur,
            "INSERT INTO realtime_snapshot (ticker, price, imbalance, best_bid, best_ask, "
            "foreign_net, inst_net, fin_invest, program_net, short_volume, short_ratio, env) "
            "VALUES %s "
            "ON CONFLICT (ticker) DO UPDATE SET price=EXCLUDED.price, "
            "imbalance=EXCLUDED.imbalance, best_bid=EXCLUDED.best_bid, "
            "best_ask=EXCLUDED.best_ask, foreign_net=EXCLUDED.foreign_net, "
            "inst_net=EXCLUDED.inst_net, fin_invest=EXCLUDED.fin_invest, "
            "program_net=EXCLUDED.program_net, short_volume=EXCLUDED.short_volume, "
            "short_ratio=EXCLUDED.short_ratio, env=EXCLUDED.env, ts=now()",
            [(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], r[11]) for r in rows])
    conn.commit()
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=30)
    ap.add_argument("--always", action="store_true", help="ignore market hours (testing)")
    args = ap.parse_args()

    if kr._token() is None:
        print("ERROR: Kiwoom token failed — check 실전 keys + 지정단말기 IP.")
        return 2
    base = getattr(kr, "_active_base", "")
    print(f"[snapshot] env={'실전' if 'mockapi' not in base else '모의'} ({base}) · "
          f"{len(UNIVERSE)} tickers · every {args.interval}s")

    conn = get_conn()
    _ensure(conn)
    if args.once:
        print(f"[snapshot] wrote {_one_pass(conn)} rows"); return 0
    while True:
        if args.always or _market_open():
            try:
                n = _one_pass(conn)
                print(f"[snapshot] {datetime.now(KST):%H:%M:%S} wrote {n} rows", flush=True)
            except Exception as e:
                print(f"[snapshot] error: {str(e)[:90]}", flush=True)
                try:
                    conn.rollback()
                except Exception:
                    conn = get_conn()
        else:
            print(f"[snapshot] {datetime.now(KST):%H:%M} market closed — idle", flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
