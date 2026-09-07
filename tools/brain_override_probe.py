# -*- coding: utf-8 -*-
"""Offline: warm the whynot cache, run _brain_compute, inspect 207940."""
import sys
sys.path.insert(0, r"C:\Users\A\Desktop\VIP\apps\orchestrator-api")
from dotenv import load_dotenv
load_dotenv(r"C:\Users\A\Desktop\VIP\.env", override=False)
load_dotenv(r"C:\Users\A\Desktop\VIP\.env.supabase", override=True)

from db.base import SessionLocal
from routers import approval as AP

db = SessionLocal()
w = AP.whynot(db)
rows = {str(x.get("code")): x.get("stopped_at") for x in (w.get("rows") or [])}
print("whynot cache warm — 207940 stopped_at:", rows.get("207940"))
print("_WHYNOT9 cached:", bool(AP._WHYNOT9.get("v")))
d = AP._brain_compute()
ent = next((e for e in (d.get("six") or []) + (d.get("universe") or [])
            if e.get("code") == "207940"), None)
if ent:
    print("brain 207940: pass", ent.get("pass"), "| lane", ent.get("lane"))
    print("lane_why_en:", str(ent.get("lane_why_en"))[:160])
    print("no_buy_en:", str(ent.get("no_buy_en"))[:120])
else:
    print("207940 not in brain output; universe n =", len(d.get("universe") or []))
db.close()
