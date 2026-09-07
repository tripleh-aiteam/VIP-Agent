# -*- coding: utf-8 -*-
"""Q1: with the ALL-DAYS ruler, would today (from 09:00) have traded anyone?
For every watched stock: the deployed min/max blend vs the percentile rank
(price vs EVERY daily close), and what the cascade verdict would become."""
import json, sys, urllib.request
sys.path.insert(0, r"C:\Users\A\Desktop\VIP\apps\orchestrator-api")
from dotenv import load_dotenv
load_dotenv(r"C:\Users\A\Desktop\VIP\.env", override=False)
from services import kiwoom_rules as KR

day = KR._kd0()
w = json.load(urllib.request.urlopen("http://127.0.0.1:8000/approval/whynot", timeout=280))
rows = {r["code"]: r for r in (w.get("rows") or [])}

def closes(code):
    cl = []
    try:
        from services.daily_pick import _conn
        cn = _conn(); cu = cn.cursor()
        cu.execute("""SELECT close FROM raw_daily_prices WHERE ticker=%s AND date < %s
                      AND close IS NOT NULL ORDER BY date DESC LIMIT 120""",
                   (code, f"{day[:4]}-{day[4:6]}-{day[6:8]}"))
        cl = [float(r[0]) for r in cu.fetchall()]; cn.close()
    except Exception:
        pass
    if not cl:
        try:
            from services.naver_stock import daily_history
            d_iso = f"{day[:4]}-{day[4:6]}-{day[6:8]}"
            cl = [float(r["close"]) for r in (daily_history(code, days=130) or [])
                  if r.get("close") and str(r.get("date"))[:10] < d_iso]
        except Exception:
            pass
    return cl

print(f"{'stock':<12} {'price':>10} {'blend%':>7} {'rank%':>6} {'days':>5} | now stopped_at -> with rank ruler")
flips = []
for code, r in rows.items():
    px = r.get("px")
    if not px:
        continue
    hz = KR._hz_stats(code, day) or {}
    bl = []
    for h in ("w", "m", "q", "h"):
        lo, hi = hz.get(h + "_low"), hz.get(h + "_hi")
        if lo and hi and hi > lo:
            bl.append(max(0.0, min(100.0, (px - lo) / (hi - lo) * 100)))
    cl = closes(code)
    rk = []
    for n in (5, 20, 60, 120):
        ww = cl[:n]
        if len(ww) >= max(3, n // 4):
            rk.append(sum(1 for x in ww if x < px) / len(ww) * 100)
    b = sum(bl) / len(bl) if bl else None
    rr = sum(rk) / len(rk) if rk else None
    st = r.get("stopped_at")
    verdict = "-"
    if b is not None and rr is not None:
        was2 = (st == 2)
        now2 = rr > 35
        if was2 and not now2:
            verdict = "gate2 OPENS (was blocked)"
            flips.append((r["name"], "opens"))
        elif (st is None or st > 2) and now2:
            verdict = "gate2 BLOCKS (was open)"
            flips.append((r["name"], "blocks"))
        else:
            verdict = "same"
    print(f"{r['name']:<12} {px:>10,.0f} {('%.0f'%b) if b is not None else '-':>7} "
          f"{('%.0f'%rr) if rr is not None else '-':>6} {len(cl):>5} | gate {st} -> {verdict}")
print("\nFLIPS:", flips or "none")
