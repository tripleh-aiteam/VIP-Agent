# -*- coding: utf-8 -*-
"""GATE-2 COURT v2 (boss 2026-09-07: "the formula cares only about max and
min price — should we consider other information? Check and tell me").

Uses the REAL replay pipeline (kiwoom_rules.rank) — the same stks builder the
live board runs — with proof_lab.VARIANTS temporarily reduced to 알고3 (D3)
copies whose ONLY difference is the gate-2 ruler:
  deployed  hz_score<=35 : blended (price-low)/(high-low) of 4 windows
  hz_rank<=N            : blended PERCENTILE — price ranked against EVERY
                          daily close in the window ('cheaper than N% of
                          that window's days'), N swept 25/30/35/40."""
import copy, sys, time
sys.path.insert(0, r"C:\Users\A\Desktop\VIP\apps\orchestrator-api")
from dotenv import load_dotenv
load_dotenv(r"C:\Users\A\Desktop\VIP\.env", override=False)

from services import kiwoom_rules as KR
from services import proof_lab as PL

_CLOSES_CACHE: dict = {}
def _closes(code, day):
    key = (code, day)
    if key in _CLOSES_CACHE:
        return _CLOSES_CACHE[key]
    cl = []
    try:
        from services.daily_pick import _conn
        cn = _conn(); cu = cn.cursor()
        cu.execute("""SELECT close FROM raw_daily_prices
                      WHERE ticker=%s AND date < %s AND close IS NOT NULL
                      ORDER BY date DESC LIMIT 120""",
                   (code, f"{day[:4]}-{day[4:6]}-{day[6:8]}"))
        cl = [float(r[0]) for r in cu.fetchall()]
        cn.close()
    except Exception:
        pass
    if not cl:                      # official-record fallback, like _hz_stats
        try:
            from services.naver_stock import daily_history
            rows = daily_history(code, days=130) or []
            d_iso = f"{day[:4]}-{day[4:6]}-{day[6:8]}"
            cl = [float(r["close"]) for r in rows
                  if r.get("close") and str(r.get("date"))[:10] < d_iso]
        except Exception:
            pass
    _CLOSES_CACHE[key] = cl
    return cl

_orig_pos_ok = PL._pos_ok
def _pos_ok_patched(s, c, v):
    if str(v.get("pos_mode") or "") == "hz_rank":
        cl = _closes(s.get("code"), s.get("d8") or "")
        if not cl:
            return False            # no data is not a pass (the 09-07 law)
        ps = []
        for n in (5, 20, 60, 120):
            w = cl[:n]
            if len(w) >= max(3, n // 4):
                ps.append(sum(1 for x in w if x < c) / len(w) * 100)
        return (sum(ps) / len(ps) <= float(v.get("pos_tol") or 0)) if ps else False
    return _orig_pos_ok(s, c, v)
PL._pos_ok = _pos_ok_patched

D3 = next(v for v in PL.VARIANTS if v["id"] == "D3")
MODES = [
    ("deployed hz_score<=35 (min/max)", "D3", {}),
    ("percentile<=25 (all closes)", "D3r25", {"pos_mode": "hz_rank", "pos_tol": 25}),
    ("percentile<=30", "D3r30", {"pos_mode": "hz_rank", "pos_tol": 30}),
    ("percentile<=35", "D3r35", {"pos_mode": "hz_rank", "pos_tol": 35}),
    ("percentile<=40", "D3r40", {"pos_mode": "hz_rank", "pos_tol": 40}),
]
variants = []
for lbl, vid, mut in MODES:
    v = copy.deepcopy(D3)
    v["id"] = vid
    v.update(mut)
    variants.append(v)
PL.VARIANTS = variants              # rank() reads this list
try:
    KR.VARIANTS = variants          # in case kiwoom_rules re-exported it
except Exception:
    pass

t0 = time.time()
r = KR.rank(tick=5, period=0, day="all", use_gate=True, allow_fallback=False)
print(f"replay took {time.time() - t0:.0f}s over {len(r.get('days') or [])} stored days\n")
print(f"{'ruler':<34} {'trips':>5} {'win%':>5} {'net%':>8} {'per-trade':>9}")
rows = {x["id"]: x for x in r.get("variants") or []}
for lbl, vid, _ in MODES:
    x = rows.get(vid) or {}
    print(f"{lbl:<34} {x.get('trips', 0):>5} {x.get('win_pct', 0):>4}% "
          f"{x.get('net', 0.0):>+8.2f} {x.get('per_trade', 0.0):>+9.3f}")
