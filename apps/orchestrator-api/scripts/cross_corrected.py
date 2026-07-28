"""Corrected Cross-Check P&L — recompute every trade at the REAL market price (READ-ONLY;
does NOT alter any record). For each trade, if the recorded fill sits inside the real 5-min
bar range at that minute it's kept; if it's outside (the buggy fills) it's replaced with the
real price. Auction sells (no live price 15:20-15:30) use the real day CLOSE. Shows recorded
vs corrected P&L so you can hand your boss the honest number.

Run:  python scripts/cross_corrected.py [YYYY-MM-DD]   (default: most recent trading day)
"""
import sys, io, os
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
os.chdir(ROOT); sys.path.insert(0, ROOT)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, "..", "..", ".env"))
load_dotenv(os.path.join(ROOT, "..", "..", ".env.supabase"), override=True)
from db.base import SessionLocal
from sqlalchemy import text

FEE = 0.23
db = SessionLocal()
day = sys.argv[1] if len(sys.argv) > 1 else db.execute(text(
    "SELECT max((closed_at AT TIME ZONE 'Asia/Seoul')::date)::text FROM cross_trades WHERE status='CLOSED'")).scalar()

# real 5-min bars for that day: (ticker, mod5) -> (low, high, close) + day close per ticker
bars, dayclose = {}, {}
for tk, mod, lo, hi, cl in db.execute(text("""
    SELECT ticker,
      (extract(hour from ts AT TIME ZONE 'Asia/Seoul')*60+extract(minute from ts AT TIME ZONE 'Asia/Seoul'))::int/5*5 m,
      low, high, close FROM minute_bars_hist
    WHERE (ts AT TIME ZONE 'Asia/Seoul')::date = :d"""), {"d": day}).fetchall():
    bars[(tk, int(mod))] = (float(lo), float(hi), float(cl))
    dayclose[tk] = float(cl)   # last one wins = day close

rows = db.execute(text("""
    SELECT id, ticker, name, qty, entry, exit_price, exit_reason,
      (extract(hour from opened_at AT TIME ZONE 'Asia/Seoul')*60+extract(minute from opened_at AT TIME ZONE 'Asia/Seoul'))::int bm,
      (extract(hour from closed_at AT TIME ZONE 'Asia/Seoul')*60+extract(minute from closed_at AT TIME ZONE 'Asia/Seoul'))::int sm,
      opened_at, closed_at
    FROM cross_trades WHERE status='CLOSED' AND (closed_at AT TIME ZONE 'Asia/Seoul')::date = :d
    ORDER BY closed_at"""), {"d": day}).fetchall()

def real_price(tk, minute, recorded):
    """Keep the recorded price if it's inside the real bar; else use the real bar close;
    for the auction/no-bar case fall back to the real day close."""
    b = bars.get((tk, (minute // 5) * 5))
    if b:
        lo, hi, cl = b
        return recorded if lo - 0.5 <= recorded <= hi + 0.5 else cl, (lo <= recorded <= hi)
    return dayclose.get(tk, recorded), (tk not in dayclose)  # no bar -> day close

# de-dup: collapse identical (ticker, buy-minute-second, entry, qty) rows to one
seen = set(); n_dup = 0
rec_won = cor_won = 0.0; corrected = []
for r in rows:
    tid, tk, name, qty, en, xp, rs, bm, sm, oa, ca = r
    key = (tk, str(oa)[:19], int(qty), round(float(en)))
    if key in seen:
        n_dup += 1; continue
    seen.add(key)
    en_r, en_ok = real_price(tk, bm, float(en))
    xp_r, xp_ok = real_price(tk, sm, float(xp))
    rec_net = (float(xp) / float(en) - 1) * 100 - FEE
    cor_net = (xp_r / en_r - 1) * 100 - FEE
    rec_won += float(qty) * float(en) * rec_net / 100
    cor_won += float(qty) * en_r * cor_net / 100
    if abs(float(xp) - xp_r) > 0.5 or abs(float(en) - en_r) > 0.5:
        corrected.append((name, rs, float(en), en_r, float(xp), xp_r, rec_net, cor_net))
db.close()

BAR = "=" * 66
print(f"\n{BAR}\n  CORRECTED CROSS-CHECK P&L  —  {day}  (real market prices, READ-ONLY)\n{BAR}")
print(f"\n  Trades: {len(seen)}   (duplicates removed: {n_dup})")
print(f"\n  RECORDED total (buggy fills):  {rec_won:+,.0f} KRW")
print(f"  CORRECTED total (real prices): {cor_won:+,.0f} KRW")
print(f"  >>> inflation from the bug:    {rec_won - cor_won:+,.0f} KRW\n")
if corrected:
    print(f"  Trades whose price was corrected ({len(corrected)}):")
    print(f"    {'stock':10} {'exit':6} {'entry rec->real':>22} {'exit rec->real':>22} {'net% rec->real':>16}")
    for nm, rs, e, er, x, xr, rn, cn in corrected[:25]:
        print(f"    {nm:10} {rs:6} {int(e):>10}->{int(er):<10} {int(x):>10}->{int(xr):<10} {rn:>+6.2f}->{cn:>+6.2f}")
print(f"\n{BAR}\n")
