# -*- coding: utf-8 -*-
"""Which window is actually doing the blocking? (boss 2026-09-07: "gate 2 is
very hard, it is not allowing to buy - what should we do?")

For every watched stock today: the deployed 4-window average, the same
average WITHOUT the 1-week window, and the all-days weighted read - and how
many stocks each version would let through the position gate."""
import sys
sys.path.insert(0, r"C:\Users\A\Desktop\VIP\apps\orchestrator-api")
from dotenv import load_dotenv
load_dotenv(r"C:\Users\A\Desktop\VIP\.env", override=False)
from services import kiwoom_rules as KR
from services.paper_desk import fast_price

day = KR._kd0()
print(f"{'stock':<12} {'price':>10} {'wk':>4} {'1m':>4} {'3m':>4} {'6m':>4} "
      f"{'avg4':>6} {'avg3':>6} {'whole':>6}")
n4 = n3 = nw = 0
rows = []
for code, name in KR.WATCH:
    try:
        px, _c, _t, _s = fast_price(code)
        px = float(px or 0)
    except Exception:
        px = 0
    if not px:
        continue
    hz = KR._hz_stats(code, day) or {}
    p = {}
    for k in ("w", "m", "q", "h"):
        lo, hi = hz.get(k + "_low"), hz.get(k + "_hi")
        if lo and hi and hi > lo:
            p[k] = max(0.0, min(100.0, (px - lo) / (hi - lo) * 100))
    if len(p) < 4:
        continue
    avg4 = sum(p.values()) / 4
    avg3 = (p["m"] + p["q"] + p["h"]) / 3          # week dropped
    cl = KR.closes120(code, day)
    ws = bs = 0.0
    for i, x in enumerate(cl):
        wt = 0.5 ** (i / 20.0)
        ws += wt
        if x < px:
            bs += wt
    whole = (bs / ws * 100) if ws else 0
    n4 += avg4 <= 35
    n3 += avg3 <= 35
    nw += whole <= 35
    rows.append((name, px, p, avg4, avg3, whole))
    print(f"{name:<12} {px:>10,.0f} {p['w']:>4.0f} {p['m']:>4.0f} {p['q']:>4.0f} "
          f"{p['h']:>4.0f} {avg4:>6.1f} {avg3:>6.1f} {whole:>6.1f}")
print(f"\nstocks passing the 35% bar:  4-window avg {n4}  |  without the week {n3}  "
      f"|  all-days read {nw}   (of {len(rows)})")
# how often is the WEEK the highest of the four?
worst = sum(1 for _n, _p, p, *_ in rows if p["w"] == max(p.values()))
print(f"the 1-week window is the HIGHEST (most blocking) of the four on "
      f"{worst} of {len(rows)} stocks")
