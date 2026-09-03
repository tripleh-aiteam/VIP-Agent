# -*- coding: utf-8 -*-
"""GIVE-UP STUDY v2 — the economic version. Comeback% alone is not enough:
a limit that fills AFTER a big runaway fills while price is crashing back
(falling knife). So for each runaway distance D measure:
  - comeback%: chance the order still fills today
  - fillPnL%: average (day close - offer)/offer of those late fills
  - EV of waiting = comeback% x fillPnL%  (what a share of patience earns)
GIVE UP at the smallest D where EV(waiting) <= 0 — beyond that, patience
buys losers on average."""
import io, sys, json, statistics
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

D = r"C:\Users\A\Desktop\VIP\apps\orchestrator-api\data\minute1_hist"
NAMES = {"000660": "SK하이닉스", "005930": "삼성전자", "035420": "NAVER",
         "017670": "SK텔레콤", "042660": "한화오션", "034020": "두산에너빌리티"}

def tick(p):
    if p < 2000: return 1
    if p < 5000: return 5
    if p < 20000: return 10
    if p < 50000: return 50
    if p < 200000: return 100
    if p < 500000: return 500
    return 1000

def study(code):
    bars = json.load(open(f"{D}/{code}.json", encoding="utf-8"))
    days = defaultdict(list)
    for b in bars:
        days[b[0][:8]].append(b)
    sims = []   # (filled, runaway_before_fill_or_max, close_pnl_pct_if_filled, tick)
    for d, rows in sorted(days.items()):
        rows.sort(key=lambda r: r[0])
        idx = {r[0][8:12]: i for i, r in enumerate(rows)}
        n = len(rows)
        if n < 60: continue
        day_close = rows[-1][4]
        for hh in range(9, 15):
            for mm in range(0, 60, 5):
                if hh == 9 and mm < 5: continue
                if hh == 14 and mm > 30: continue
                i0 = idx.get(f"{hh:02d}{mm:02d}")
                if i0 is None or i0 >= n - 5: continue
                c0 = rows[i0][4]
                tk = tick(c0)
                offer = c0 - tk
                runaway = 0.0; filled = False
                for j in range(i0 + 1, n):
                    if rows[j][3] <= offer:
                        filled = True
                        break
                    ra = rows[j][2] - offer
                    if ra > runaway: runaway = ra
                pnl = (day_close - offer) / offer * 100 if filled else None
                sims.append((filled, runaway, pnl, tk))
    return sims

out = {}
for code, name in NAMES.items():
    sims = study(code)
    tks = statistics.median(s[3] for s in sims)
    px0 = None
    rows_txt, chosen = [], None
    for nt in range(1, 41):
        Dw = nt * tks
        reached = [s for s in sims if s[1] >= Dw]
        if len(reached) < 40: break
        fills = [s for s in reached if s[0]]
        cb = len(fills) / len(reached)
        avg = statistics.mean(s[2] for s in fills) if fills else 0.0
        ev = cb * avg
        rows_txt.append(f"{nt:2d}t cb={cb*100:4.0f}% fillPnL={avg:+5.2f}% EV={ev:+5.2f}")
        if chosen is None and ev <= 0:
            chosen = (nt, Dw, cb, avg, ev, len(reached))
    print(f"== {name} (tick ₩{tks:,.0f}) ==")
    for r in rows_txt[:20]: print("  " + r)
    if chosen:
        nt, Dw, cb, avg, ev, nre = chosen
        print(f"  -> GIVE-UP {nt} ticks = ₩{Dw:,.0f} (comeback {cb*100:.0f}%, "
              f"late fills avg {avg:+.2f}% by close, n={nre})")
        out[code] = {"name": name, "ticks": nt, "won": Dw}
    else:
        print("  -> EV never negative in 40 ticks")
print()
print(json.dumps(out, ensure_ascii=False))
