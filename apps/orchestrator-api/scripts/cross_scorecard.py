"""Cross-Check (Algorithm 4) end-of-day SCORECARD — the day's REAL trades (READ-ONLY).

Answers the questions that actually matter after a session:
  • how many trades, and what's the real win rate?
  • how big were the winners vs the losers (reward:risk)?
  • did the −1% stop behave, or did anything bleed past it? (the safety check)
  • what's still open?

Run from apps/orchestrator-api:
    python scripts/cross_scorecard.py            # today (KST)
    python scripts/cross_scorecard.py 2026-07-27 # a specific day
"""
import sys, io, os
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                       # apps/orchestrator-api
os.chdir(ROOT)
sys.path.insert(0, ROOT)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, "..", "..", ".env"))
load_dotenv(os.path.join(ROOT, "..", "..", ".env.supabase"), override=True)
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from db.base import SessionLocal
from sqlalchemy import text

KST = timezone(timedelta(hours=9))
day = sys.argv[1] if len(sys.argv) > 1 else datetime.now(KST).strftime("%Y-%m-%d")

db = SessionLocal()
rows = db.execute(text("""
    SELECT ticker, name, qty, entry, exit_price, exit_reason, net_pct,
           (closed_at AT TIME ZONE 'Asia/Seoul') c
    FROM cross_trades
    WHERE status='CLOSED' AND (closed_at AT TIME ZONE 'Asia/Seoul')::date = :d
    ORDER BY closed_at"""), {"d": day}).fetchall()
opens = db.execute(text("""
    SELECT ticker, name, qty, entry, (opened_at AT TIME ZONE 'Asia/Seoul') o
    FROM cross_trades WHERE status='OPEN' ORDER BY opened_at""")).fetchall()
cfg = db.execute(text("SELECT mode, rule, stop_pct, take_pct FROM cross_state WHERE id=1")).first()
db.close()

BAR = "=" * 58
print(f"\n{BAR}\n  CROSS-CHECK SCORECARD  —  {day} (KST)")
print(f"  config: mode={cfg[0]}  rule={cfg[1]}  stop=-{cfg[2]:.1f}%  take=+{cfg[3]:.1f}%\n{BAR}")

if not rows:
    print("\n  No closed trades this day.")
else:
    nets = [float(r[6]) for r in rows]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    n = len(nets)
    won_amt = sum(float(r[2]) * float(r[3]) * float(r[6]) / 100 for r in rows)   # net won (after fee)
    aw = sum(wins) / len(wins) if wins else 0.0
    al = sum(losses) / len(losses) if losses else 0.0
    rr = aw / abs(al) if al else float("nan")
    print(f"\n  Trades: {n}    Wins: {len(wins)}    Losses: {len(losses)}    "
          f"WIN RATE: {100*len(wins)/n:.0f}%")
    print(f"  Avg winner: +{aw:.2f}%    Avg loser: {al:.2f}%    R:R: {rr:.2f}")
    print(f"  Net (sum %): {sum(nets):+.2f}%    Net won: {won_amt:+,.0f} KRW")
    worst = min(rows, key=lambda r: float(r[6]))
    best = max(rows, key=lambda r: float(r[6]))
    print(f"  Best:  {float(best[6]):+.2f}%  ({best[1]} {best[5]})")
    print(f"  Worst: {float(worst[6]):+.2f}%  ({worst[1]} {worst[5]})")

    by = defaultdict(list)
    for r in rows:
        by[r[5]].append(float(r[6]))
    print("\n  Exit reasons:")
    for reason, xs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        print(f"    {reason:9} x{len(xs):<3}  avg {sum(xs)/len(xs):+.2f}%")

    stops = [(r[1], float(r[6])) for r in rows if r[5] == "STOP"]
    print(f"\n  SAFETY CHECK (a -{cfg[2]:.0f}% stop should read about -1.2% net):")
    if not stops:
        print("    no STOP exits today.")
    else:
        bad = 0
        for nm, x in stops:
            ok = x >= -1.5
            bad += 0 if ok else 1
            print(f"    {nm}: {x:+.2f}%   {'OK' if ok else '<<< BLED PAST STOP — tell Claude'}")
        print("    " + ("all stops behaved." if not bad else f"{bad} stop(s) bled past -1.5% !!"))

print(f"\n  Still holding: {len(opens)}")
for tk, nm, qty, entry, o in opens:
    print(f"    {nm} ({tk}) x{qty} @ {float(entry):,.0f}   (opened {str(o)[11:16]})")
print(f"\n{BAR}\n")
