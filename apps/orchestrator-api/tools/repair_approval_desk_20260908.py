# -*- coding: utf-8 -*-
"""REBUILD TODAY'S MENU 3 HISTORY FROM THE LEDGER (boss 2026-09-08 13:1x:
"오늘 매수를 한 다음에 매도한 기록이 있어서 보여야 하는데 안 보이는거 같다").

He was right. Three Menu 3 round trips closed today and the board showed none
of them. Nothing was mis-recorded: the rows were WRITTEN and then pushed out.
The send-time guard appends a 보류 row every scan cycle for every stock it
refuses, so stocks it blocked all morning left 197 of the log's 200 rows
(한화오션 67, 한화시스템 54, 한화에어로 46) and the plain `log[-200:]` trim
threw the trades away oldest-first. A note about money NOT moving evicted the
record of money moving.

approval_desk.py now folds repeated refusals into one row each and trims the
informational rows before the decided ones, so it cannot happen again. This
repairs the damage already done, from the only source that still has the
truth: paper_desk_orders. Every Menu 3 (source='semi') fill of today is
replayed, and for each one the desk's own closing sell is found the way
_reconcile_positions finds it — replay today's fills in order, and the SELL
that takes the running position to zero at or after our buy is the one that
closed us.

RUN IT WITH :8000 STOPPED. The scanner's _save_scan merges from disk and would
race a writer it does not know about.

    python tools/repair_approval_desk_20260908.py [--apply]

Without --apply it only reports. A timestamped backup is written before any
change.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv                                    # noqa: E402

load_dotenv(ROOT.parent.parent / ".env", override=False)

import services.approval_desk as ad                               # noqa: E402

try:                       # the Windows console is cp949; ₩ and 한글 must print
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _hhmm(ts: float) -> str:
    return time.strftime("%H:%M", time.gmtime(ts + 9 * 3600))


def _day(ts: float) -> str:
    return time.strftime("%Y%m%d", time.gmtime(ts + 9 * 3600))


def ledger():
    """Today's fills, and today's semi (Menu 3) fills, from the ledger."""
    from sqlalchemy import create_engine, text
    eng = create_engine(os.environ["DATABASE_URL"])
    with eng.connect() as c:
        rows = c.execute(text(
            "SELECT id, ticker, name, side, qty, fill_price, COALESCE(source,''), "
            "       EXTRACT(EPOCH FROM filled_at) "
            "  FROM paper_desk_orders "
            " WHERE filled_at >= CURRENT_DATE AND status='FILLED' "
            " ORDER BY filled_at")).fetchall()
    return [{"oid": r[0], "code": str(r[1]), "name": r[2], "side": str(r[3]),
             "qty": int(r[4] or 0), "fill": float(r[5] or 0),
             "src": str(r[6]), "ts": float(r[7] or 0)} for r in rows]


def closing_sell(fills, code: str, buy_ts: float):
    """The sell that took our position out, or None while we still hold it."""
    net, seen_mine = 0, False
    for f in fills:
        if f["code"] != code:
            continue
        net += f["qty"] if f["side"] == "BUY" else -f["qty"]
        if f["ts"] >= buy_ts:
            seen_mine = True
        if seen_mine and net <= 0 and f["side"] == "SELL" and f["fill"]:
            return f
    return None


def main(apply: bool) -> int:
    fills = ledger()
    mine = [f for f in fills if f["src"] == "semi"]
    if not mine:
        print("no Menu 3 fills today — nothing to repair")
        return 0

    st = ad._load()
    log = st.get("log") or []
    today = _day(time.time())

    def has(code, side, at, qty):
        return any(str(l.get("code")) == code and l.get("side") == side
                   and str(l.get("at") or "")[:5] == at
                   and int(l.get("qty") or 0) == int(qty)
                   and ad._row_day(l) == today for l in log)

    added, seen = [], set()
    for b in mine:
        if b["side"] != "BUY":
            continue
        key = (b["code"], b["ts"])
        if key in seen:
            continue
        seen.add(key)
        bat = _hhmm(b["ts"])
        if not has(b["code"], "BUY", bat, b["qty"]):
            added.append({
                "id": int(b["ts"] * 1000) % 10 ** 9, "ts": b["ts"], "hhmm": bat,
                "code": b["code"], "name": b["name"], "side": "BUY",
                "price": b["fill"], "qty": b["qty"], "score": None,
                "reasons": ["🧾 원장에서 복구한 기록입니다 — 이 매수는 실제로 "
                            "체결되었고, 기록만 화면에서 밀려나 있었습니다."],
                "reasons_en": ["🧾 Restored from the order ledger — this buy really "
                               "filled; only its row had been pushed off the board."],
                "decision": "승인", "at": bat, "dealt": True, "fill": b["fill"],
                "oid": b["oid"], "via": "repair"})

        s = closing_sell(fills, b["code"], b["ts"])
        if not s:
            continue
        sat = _hhmm(s["ts"])
        if has(b["code"], "SELL", sat, b["qty"]):
            continue
        pnl = round((s["fill"] / b["fill"] - 1) * 100, 2) if b["fill"] else None
        won = round((s["fill"] - b["fill"]) * b["qty"]) if b["fill"] else None
        added.append({
            "id": int(s["ts"] * 1000) % 10 ** 9 + 1, "ts": s["ts"], "hhmm": sat,
            "code": b["code"], "name": b["name"], "side": "SELL",
            "price": s["fill"], "qty": b["qty"], "score": None,
            "reasons": [f"🤖 데스크가 정리했습니다 ({s['src']}) — 매수 {bat} "
                        f"₩{b['fill']:,.0f} → 매도 {sat} ₩{s['fill']:,.0f} "
                        f"({pnl:+.2f}%, {won:+,}원).",
                        "🧾 원장에서 복구한 기록입니다 — 매매는 실제로 있었고, "
                        "기록만 화면에서 밀려나 있었습니다."],
            "reasons_en": [f"🤖 Closed by the desk ({s['src']}) — bought {bat} "
                           f"₩{b['fill']:,.0f} → sold {sat} ₩{s['fill']:,.0f} "
                           f"({pnl:+.2f}%, {won:+,} KRW).",
                           "🧾 Restored from the order ledger — the trade really "
                           "happened; only its row had been pushed off the board."],
            "decision": "승인", "at": sat, "dealt": True, "fill": s["fill"],
            "buy_at": bat, "buy_price": b["fill"],
            "pnl_pct": pnl, "pnl_won": won,
            "oid": s["oid"], "via": "repair"})

    print(f"Menu 3 fills today: {len(mine)}   rows to restore: {len(added)}")
    for r in added:
        extra = (f"  {r['pnl_pct']:+.2f}%" if r.get("pnl_pct") is not None else "")
        print(f"  + {r['at']} {r['code']} {str(r['name'])[:12]:14s} {r['side']:4s} "
              f"{r['qty']:>6,} @ ₩{r['fill']:,.0f}{extra}")

    st["log"] = sorted(log + added, key=lambda l: float(l.get("ts") or 0))
    before = len(st["log"])
    ad._trim_log(st)
    print(f"\nlog: {len(log)} rows → {before} with restores → {len(st['log'])} after fold+trim")
    kept = sum(1 for l in st["log"] if l.get("decision") in ("승인", "취소"))
    print(f"decided rows kept: {kept}")

    if not apply:
        print("\n(dry run — pass --apply to write)")
        return 0

    src = Path(ad._FILE)
    if src and src.exists():
        bak = src.with_suffix(f".bak-{time.strftime('%Y%m%d-%H%M%S')}.json")
        shutil.copy2(src, bak)
        print(f"backup: {bak}")
    ad._save(st)
    print("written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--apply" in sys.argv))
