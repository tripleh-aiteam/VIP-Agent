# -*- coding: utf-8 -*-
"""Why does 알고2 (D2) make zero trades today? Build today's desk exactly as
the live board does, then run D2 with each suspect blocker toggled off."""
import copy, sys
sys.path.insert(0, r"C:\Users\A\Desktop\VIP\apps\orchestrator-api")
from dotenv import load_dotenv
load_dotenv(r"C:\Users\A\Desktop\VIP\.env", override=False)

from services import kiwoom_rules as KR
from services import proof_lab as PL

# today's stks, the same way rank() builds them
day = KR._kd0()
tick, period = 5, 0
tapes = {}
for code, name in KR.WATCH:
    cs = KR._bars_for(code, tick, period, day, "", "")
    if len(cs) >= 10:
        tapes[code] = {"name": name, "cs": cs, "tk": KR.krx_tick(cs[-1]["close"]) or 1}
print("stocks with bars:", len(tapes))
from services.kiwoom_tape import load_book as _lb
stks_base = []
for code, tp in tapes.items():
    stks_base.append({"code": code, "_dipc": {}, "book": _lb(code, day), "d8": day,
                      "closes": [c["close"] for c in tp["cs"]],
                      "highs": [c["high"] for c in tp["cs"]],
                      "lows": [c["low"] for c in tp["cs"]],
                      "open_px": KR._open_official(code, day, tp["cs"][0].get("open"))
                                 or tp["cs"][0].get("open"),
                      "news_hits": [], "tick": tp["tk"], "seed": 1,
                      "times": [c.get("hhmm") for c in tp["cs"]],
                      "vols": [c.get("vol") for c in tp["cs"]],
                      "vol_day_avg": KR._vol5(code, day),
                      "prev_close": KR._gap_ref(code, day),
                      "gate_ok": True,
                      "name": tp["name"]})

D2 = next(v for v in PL.VARIANTS if v["id"] == "D2")
print("D2 gap_guard:", D2.get("gap_guard"), "| week_low:", D2.get("week_low"),
      "| week_vol:", D2.get("week_vol"))

def run(label, mut=None):
    v = copy.deepcopy(D2)
    if mut:
        v.update(mut)
    stks = [dict(copy.deepcopy(s), ml_bundle=None) for s in stks_base]
    try:
        got = PL.run_desk(stks, v)
        print(f"{label:<28} trades: {len(got)}"
              + (f"  e.g. {[(t.get('code'), t.get('buy_t')) for t in got[:3]]}" if got else ""))
    except Exception as e:
        import traceback
        print(f"{label:<28} ERROR: {e}")
        traceback.print_exc()

run("baseline (deployed)")
run("ctx off", {"ctx": None})
run("spike_guard off", {"spike_guard": None})
run("us_habit off", {"us_habit": False})
run("vol_size off", {"vol_size": None})
run("dip drop 0.4", {"dip": dict(D2["dip"], drop=0.4)})
run("everything off", {"ctx": None, "spike_guard": None, "us_habit": False,
                       "vol_size": None, "m1_ban_day": None})
