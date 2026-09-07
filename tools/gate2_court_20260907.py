# -*- coding: utf-8 -*-
"""GATE-2 COURT (boss 2026-09-07: "gate 2 is very hard, it is not allowing
to buy - we have to make it weaker; tell me your idea").

Replays 알고3 (D3) over EVERY stored day, changing ONLY the gate-2 ruler:

  deployed   range blend <= 35   (price between window low and high, 4 windows)
  range<=40 / <=45              simply loosening the deployed bar
  whole<=35 / <=40              the all-days ruler: recency-weighted share of
                                the last 120 daily closes below today
  either<=35                    pass if EITHER ruler says cheap

Everything else in the book is identical, and the replay uses the SAME
fill function and merged event clock as the live board (the first cut of
this court forgot fill_fn/events, which is why every ruler read 0 trips)."""
import copy, sys, time
sys.path.insert(0, r"C:\Users\A\Desktop\VIP\apps\orchestrator-api")
from dotenv import load_dotenv
load_dotenv(r"C:\Users\A\Desktop\VIP\.env", override=False)

from services import kiwoom_rules as KR
from services import proof_lab as PL
from services.kiwoom_tape import load_book as _lb

# ── the all-days ruler, patched into gate 2 ──────────────────────────────
def _whole(code, day, px):
    cl = KR.closes120(code, day)
    if not cl:
        return None
    wsum = bsum = 0.0
    for i, x in enumerate(cl):
        wt = 0.5 ** (i / 20.0)
        wsum += wt
        if x < px:
            bsum += wt
    return (bsum / wsum * 100) if wsum else None

_orig = PL._pos_ok
def _patched(s, c, v):
    m = str(v.get("pos_mode") or "")
    tol = float(v.get("pos_tol") or 35)
    if m in ("whole", "either"):
        w = _whole(s.get("code"), s.get("d8") or "", c)
        if m == "whole":
            return False if w is None else (w <= tol)
        base = _orig(s, c, dict(v, pos_mode="hz_score", pos_tol=35))
        return bool(base or (w is not None and w <= tol))
    return _orig(s, c, v)
PL._pos_ok = _patched

D3 = next(v for v in PL.VARIANTS if v["id"] == "D3")
MODES = [
    ("deployed  range<=35", {}),
    ("range<=40", {"pos_tol": 40}),
    ("range<=45", {"pos_tol": 45}),
    ("whole-read<=35", {"pos_mode": "whole", "pos_tol": 35}),
    ("whole-read<=40", {"pos_mode": "whole", "pos_tol": 40}),
    ("either passes<=35", {"pos_mode": "either", "pos_tol": 35}),
]

days = KR.stored_days()
print(f"replaying {len(days)} stored days · 알고3 book · gate-2 ruler swapped\n")
res = {lbl: [] for lbl, _ in MODES}
t0 = time.time()
for d in days:
    tapes = {}
    for code, name in KR.WATCH:
        cs = KR._bars_for(code, 5, 0, d, "", "")
        if len(cs) >= 10:
            tapes[code] = {"name": name, "cs": cs, "tk": KR.krx_tick(cs[-1]["close"]) or 1}
    if not tapes:
        continue
    base = []
    for code, tp in tapes.items():
        base.append({"code": code, "_dipc": {}, "book": _lb(code, d), "d8": d,
                     "closes": [c["close"] for c in tp["cs"]],
                     "highs": [c["high"] for c in tp["cs"]],
                     "lows": [c["low"] for c in tp["cs"]],
                     "open_px": KR._open_official(code, d, tp["cs"][0].get("open"))
                                or tp["cs"][0].get("open"),
                     "news_hits": [], "tick": tp["tk"], "seed": 1,
                     "times": [c.get("hhmm") for c in tp["cs"]],
                     "vols": [c.get("vol") for c in tp["cs"]],
                     "vol_day_avg": KR._vol5(code, d),
                     "prev_close": KR._gap_ref(code, d),
                     "hz": KR._hz_stats(code, d),
                     "low5": KR._week_stats(code, d)[0],
                     "high5": KR._week_stats(code, d)[1],
                     "avg5": KR._week_stats(code, d)[2],
                     "gate_ok": True, "name": tp["name"]})
    events = sorted((sk["times"][i], si, i)
                    for si, sk in enumerate(base)
                    for i in range(1, len(sk["closes"])))
    for lbl, mut in MODES:
        v = copy.deepcopy(D3)
        v.update(mut)
        stks = [dict(copy.deepcopy(s), ml_bundle=None) for s in base]
        try:
            res[lbl] += PL.run_desk(stks, v, fill_fn=KR._fill, events=events)
        except Exception as e:
            print(f"  {d} {lbl}: {str(e)[:70]}")

print(f"({time.time() - t0:.0f}s)\n")
print(f"{'gate-2 ruler':<22} {'trips':>5} {'win%':>5} {'net%':>9} {'per trade':>10} {'worst':>8}")
for lbl, _ in MODES:
    tr = res[lbl]
    w = sum(1 for t in tr if t["gross_pct"] > 0)
    l = sum(1 for t in tr if t["gross_pct"] < 0)
    net = sum(t["net_pct"] for t in tr)
    worst = min((t["net_pct"] for t in tr), default=0.0)
    wp = round(w / (w + l) * 100) if (w + l) else 0
    per = (net / len(tr)) if tr else 0.0
    print(f"{lbl:<22} {len(tr):>5} {wp:>4}% {net:>+9.2f} {per:>+10.3f} {worst:>+8.2f}")
