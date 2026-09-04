# -*- coding: utf-8 -*-
"""Repair approval_desk.json after the 2026-09-04 11:24 duplication flood.

The desk-flat closer re-ran every scan (the write-merge re-added closed lots
from disk), appending the same 4 SELL rows ~50x each until the 200-row log
held nothing else. Real fills live in paper_desk_orders (source='semi'), so
the log is rebuilt from there; reasons/check_items are left empty for the
scanner's enricher (_rv gate) to regenerate.

RUN ONLY WITH :8000 STOPPED.
"""
import json, re, time
from datetime import timedelta, timezone
from pathlib import Path

ROOT = Path(r"C:\Users\A\Desktop\VIP")
FILE = ROOT / "apps" / "orchestrator-api" / "data" / "approval_desk.json"
KST = timezone(timedelta(hours=9))

url = None
for ln in (ROOT / ".env.supabase").read_text(encoding="utf-8").splitlines():
    m = re.match(r"DATABASE_URL=(.+)", ln.strip())
    if m:
        url = m.group(1).strip()
if not url:
    raise SystemExit("no DATABASE_URL in .env.supabase")
url = url.replace(":5432", ":6543")  # txn pooler

from sqlalchemy import create_engine, text
eng = create_engine(url, pool_pre_ping=True)

st = json.loads(FILE.read_text(encoding="utf-8"))
old_log = st.get("log") or []

# 1. keep ONE copy of each flooded desk-close row (legit history)
seen, close_rows = set(), []
for l in old_log:
    k = (str(l.get("code")), str(l.get("side")), str(l.get("at")), l.get("fill"))
    if k in seen:
        continue
    seen.add(k)
    close_rows.append(l)
print("dedup:", len(old_log), "->", len(close_rows))

# 2. the real orders, last 5 days
with eng.connect() as cx:
    rows = cx.execute(text(
        "SELECT id, ticker, name, side, qty, order_type, limit_price, status, "
        "fill_price, note, source, "
        "to_char(filled_at AT TIME ZONE 'Asia/Seoul','HH24:MI') fhm, "
        "extract(epoch from COALESCE(filled_at, created_at)) ep "
        "FROM paper_desk_orders WHERE COALESCE(source,'')='semi' "
        "AND created_at >= CURRENT_DATE - INTERVAL '5 days' "
        "ORDER BY COALESCE(filled_at, created_at)")).fetchall()
print("semi orders (5d):", len(rows))

books: dict = {}
new_log = []
for (oid, tk, nm, side, qty, otype, lpx, status, fpx, note, src, fhm, ep) in rows:
    side = str(side).upper()
    status = str(status)
    at = str(fhm or "")[:5]
    ts = float(ep or time.time())
    base = {"id": int(oid) % 10**9, "ts": ts, "code": str(tk), "name": str(nm or tk),
            "side": side, "qty": int(qty or 0), "score": None,
            "price": float(fpx or lpx or 0), "decision": "승인",
            "hhmm": at, "at": at or time.strftime("%H:%M", time.gmtime(ts + 9 * 3600)),
            "reasons": [], "reasons_en": []}
    if status == "FILLED" and fpx:
        base["dealt"] = True
        base["fill"] = float(fpx)
        if "전환" in str(note or ""):
            base["converted"] = True
            base["conv_note"] = str(note)
        b = books.setdefault(str(tk), [])
        if side == "BUY":
            b.append([float(fpx), int(qty or 0), at])
        else:
            if b:
                bp, q0, bat = b[0]
                base["buy_at"], base["buy_price"] = bat, bp
                base["pnl_pct"] = round(float(fpx) / bp * 100 - 100, 2)
                base["pnl_won"] = round((float(fpx) - bp) * int(qty or 0))
                left = int(qty or 0)
                while left > 0 and b:
                    p0, qq, aa = b[0]
                    take = min(left, qq)
                    left -= take
                    if take >= qq:
                        b.pop(0)
                    else:
                        b[0][1] = qq - take
    elif status == "CANCELLED":
        base["dealt"] = False
        if "포기" in str(note or ""):
            base["gave_up"] = True
            base["giveup_note"] = str(note)
        else:
            continue
    elif status in ("OPEN", "PENDING"):
        base["dealt"] = False
        base["oid"] = int(oid)
    else:
        continue
    new_log.append(base)

# 3. merge, dropping rebuilt rows the deduped close rows already cover
have = {(str(l.get("code")), str(l.get("side")), str(l.get("at")), l.get("fill"))
        for l in close_rows}
new_log = [l for l in new_log
           if (str(l["code"]), str(l["side"]), str(l.get("at")), l.get("fill")) not in have]
log = sorted(new_log + close_rows, key=lambda l: float(l.get("ts") or 0))[-200:]
st["log"] = log
print("rebuilt log:", len(log))

# 4. held: drop the 4 codes the desk already closed (flat)
FLAT = {"000270", "035420", "329180", "009540"}
before = [(h.get("code"), h.get("qty")) for h in st.get("held") or []]
st["held"] = [h for h in st.get("held") or [] if str(h.get("code")) not in FLAT]
print("held:", before, "->", [(h.get("code"), h.get("qty")) for h in st["held"]])
print("FIFO open lots per code:", {k: v for k, v in books.items() if v})

st.pop("_desk_closed", None)
FILE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
print("saved", FILE)
