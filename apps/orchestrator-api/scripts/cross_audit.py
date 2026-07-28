"""Cross-Check trade AUDIT — shows the exact 1-min candles that fired each entry and exit,
so every trade can be checked against the chart. R=red/up, B=blue/down, -=doji (with close).
A candle ENTRY should end in R R R (3 up); a CANDLE exit should end in B B B (3 down).
Only trades made AFTER the candle-audit deploy (2026-07-28) carry candle data. READ-ONLY.

Run:  python scripts/cross_audit.py [YYYY-MM-DD]   (default: today KST)
"""
import sys, io, os
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
os.chdir(ROOT); sys.path.insert(0, ROOT)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, "..", "..", ".env"))
load_dotenv(os.path.join(ROOT, "..", "..", ".env.supabase"), override=True)
from datetime import datetime, timezone, timedelta
from db.base import SessionLocal
from sqlalchemy import text

KST = timezone(timedelta(hours=9))
day = sys.argv[1] if len(sys.argv) > 1 else datetime.now(KST).strftime("%Y-%m-%d")
db = SessionLocal()
rows = db.execute(text("""
    SELECT to_char(opened_at AT TIME ZONE 'Asia/Seoul','HH24:MI:SS') o,
           to_char(closed_at AT TIME ZONE 'Asia/Seoul','HH24:MI:SS') c,
           name, exit_reason, net_pct, entry_candles, exit_candles
    FROM cross_trades
    WHERE (COALESCE(closed_at, opened_at) AT TIME ZONE 'Asia/Seoul')::date = :d
    ORDER BY opened_at"""), {"d": day}).fetchall()
db.close()

print(f"\n  CROSS-CHECK CANDLE AUDIT — {day} (KST)")
print(f"  legend: R=red/up  B=blue/down  -=doji   | entry wants R R R (3 up), CANDLE exit wants B B B (3 down)\n")
if not rows:
    print("  no trades this day.")
for o, c, nm, rs, net, ec, xc in rows:
    def verdict(pat, want):
        if not pat:
            return "(no candle data — pre-audit trade)"
        cols = [p[0] for p in pat.split()]
        last3 = "".join(cols[-3:])
        ok = last3 == want * 3
        return f"[{pat}]  ->  last3={last3}  {'✅ matches' if ok else '⚠️ check'}"
    print(f"  {nm}  {o} -> {c or '(open)'}  exit={rs or '-'}  net={net if net is not None else '-'}%")
    print(f"     ENTRY candles: {verdict(ec, 'R')}")
    if c:
        want = "B" if rs == "CANDLE" else None
        print(f"     EXIT  candles: {verdict(xc, 'B') if want else ('['+xc+']' if xc else '(no candle data)')}")
    print()
