# -*- coding: utf-8 -*-
"""trip_editor — modify a recorded trade's display by chat (boss 2026-09-02:
"my app late to sale or buy — can I modify already-trading case, like change
buying time from 09:09 to 09:07").

Display-level overrides, sister of trip_eraser: the row shown on BOTH menus
changes, the underlying order records and desk accounting stay untouched, and
"수정 복원 / restore edits" undoes everything. Day-scoped.

data/trip_edits.json: [{"day","code","field":"buy_t"|"sell_t","frm":"HH:MM","to":"HH:MM"}]
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

_FILE = Path(__file__).resolve().parent.parent / "data" / "trip_edits.json"


def _load() -> list[dict]:
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(items: list[dict]) -> None:
    try:
        _FILE.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _price_at(code: str, day8: str, hhmm: str):
    """The REAL market price of code at hhmm that day (boss 2026-09-03 13:5x:
    'according to time, price also must change automatically'). Sources, best
    first: the year minute bars (the six), today's tick tape, today's book tape.
    Returns a float or None."""
    key = day8 + hhmm.replace(":", "") + "00"
    try:                                     # 1) minute bars (the six)
        p = _FILE.parent / "minute1_hist" / f"{code}.json"
        if p.exists():
            bars = json.loads(p.read_text(encoding="utf-8"))
            after = [b for b in bars if b[0] >= key and b[0][:8] == day8]
            if after:
                return float(after[0][4])
    except Exception:
        pass
    try:                                     # 2) tick tape (desk stocks)
        p = _FILE.parent / "kiwoom_tape" / f"{code}_{day8}.jsonl"
        if p.exists():
            tkey = hhmm  # "HH:MM"
            with p.open(encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    if str(r.get("t") or "")[:5] >= tkey and r.get("px"):
                        return float(r["px"])
    except Exception:
        pass
    try:                                     # 3) book tape → mid of best bid/ask
        p = _FILE.parent / "kiwoom_tape" / f"book_{code}_{day8}.jsonl"
        if p.exists():
            tkey = hhmm.replace(":", "") + "00"
            with p.open(encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    if str(r.get("ts") or "") >= tkey and r.get("bids") and r.get("asks"):
                        return float((r["bids"][0][0] + r["asks"][0][0]) / 2)
    except Exception:
        pass
    return None


def edit(day8: str, code: str, field: str, frm: str, to: str) -> None:
    """Register one time override. field: buy_t | sell_t. frm may be '' =
    the stock's first matching trip that day. The price at the NEW time is
    resolved from that day's real data and rides along, so the displayed
    price moves with the displayed time."""
    items = [x for x in _load()
             if not (x.get("day") == day8 and x.get("code") == code
                     and x.get("field") == field and x.get("frm") == (frm or "")[:5])]
    items.append({"day": day8, "code": code, "field": field,
                  "frm": (frm or "")[:5], "to": (to or "")[:5],
                  "px": _price_at(code, day8, (to or "")[:5])})
    _save(items[-200:])


def restore(day8: str, code: Optional[str] = None) -> int:
    items = _load()
    keep = [x for x in items
            if not (x.get("day") == day8 and (code is None or x.get("code") == code))]
    _save(keep)
    return len(items) - len(keep)


def apply_rows(rows: list, day8: str) -> list:
    """Rewrite buy_t/sell_t on matching rows (completed trips AND holdings).
    The algo tables render their ▲/▼ lines from parts.buys/parts.sells
    [[price, qty, "HH:MM:SS", ...], ...], not from the header fields — so the
    override rewrites BOTH (boss 2026-09-03 13:3x: the 한국항공우주 10:15→09:25
    edit registered but the table still showed 10:15)."""
    eds = [x for x in _load() if x.get("day") == day8]
    if not eds:
        return rows
    out = []
    for r in rows:
        r2 = r
        for e in eds:
            if str(r.get("code") or "") != e.get("code"):
                continue
            f = e.get("field") or "buy_t"
            cur = str(r2.get(f) or "")
            if cur and (not e.get("frm") or cur[:5] == e["frm"]):
                r2 = {**r2, f: e["to"] + cur[5:]}
            # the ▲/▼ leg lines inside parts — time moves, and when the edit
            # carries the real price at the new time, the price moves WITH it
            # (boss 2026-09-03 13:5x: "according to time, price also must be
            # changed automatically"); sell bases follow so every ▼ % and the
            # money column recompute against the new price.
            p = r2.get("parts")
            key = "buys" if f == "buy_t" else "sells"
            arr = (p or {}).get(key) or []
            if arr:
                px9 = e.get("px")
                changed = False
                old_px = None
                new_arr = []
                for leg in arr:
                    leg2 = list(leg)
                    tt = str(leg2[2]) if len(leg2) > 2 and leg2[2] else ""
                    if tt and (not e.get("frm") or tt[:5] == e["frm"]):
                        leg2[2] = e["to"] + tt[5:]
                        if px9:
                            old_px = float(leg2[0] or 0) or None
                            leg2[0] = float(px9)
                        changed = True
                    new_arr.append(leg2)
                if changed:
                    p2 = {**p, key: new_arr}
                    r2 = {**r2, "parts": p2}
                    if px9 and old_px:
                        if f == "buy_t":
                            # entry + every sell base that pointed at the old
                            # buy price follow; realized money shifts by the
                            # per-share difference on the shares already sold
                            if float(r2.get("entry") or 0) == old_px:
                                r2["entry"] = float(px9)
                            sold_q = 0
                            sells2 = []
                            for s9 in (p2.get("sells") or []):
                                s2 = list(s9)
                                if len(s2) > 6 and s2[6] and float(s2[6]) == old_px:
                                    s2[6] = float(px9)
                                    sold_q += int(s2[1] or 0)
                                sells2.append(s2)
                            r2["parts"] = {**p2, "sells": sells2}
                            if r2.get("won") is not None and sold_q:
                                r2["won"] = round(float(r2["won"])
                                                  + (old_px - float(px9)) * sold_q)
                        else:
                            # the header exit price follows its own leg - it was
                            # left on the OLD price, so the table showed one
                            # price on the ▼ line and another in the header
                            if float(r2.get("exit") or 0) == old_px:
                                r2["exit"] = float(px9)
                            q9 = next((int(x[1] or 0) for x in new_arr
                                       if str(x[2] or "")[:5] == e["to"]), 0)
                            if r2.get("won") is not None and q9:
                                r2["won"] = round(float(r2["won"])
                                                  + (float(px9) - old_px) * q9)
        if r2 is not r:
            # THE PERCENTAGE MUST FOLLOW THE PRICE (audit 2026-09-03: the
            # 한국항공우주 10:15->09:25 edit moved the entry to 122,000 and the
            # money to +110,557,300 won, but net_pct kept the ORIGINAL trade's
            # -1.263 and result kept "loss" - so the board printed a red LOSS on
            # a row whose own two prices read +2.05%. The edit already decides
            # the prices; the percentage is not an independent fact and may not
            # disagree with them.)
            _prt = r2.get("parts") or {}
            _sl9 = _prt.get("sells") or []
            _g9 = None
            if r2.get("partial") and _sl9:
                _w9 = _c9 = 0.0
                for _s9 in _sl9:
                    if len(_s9) > 6 and _s9[6]:
                        _b9, _q9 = float(_s9[6]), int(_s9[1] or 0)
                        _w9 += (float(_s9[0]) - _b9) * _q9
                        _c9 += _b9 * _q9
                if _c9:
                    _g9 = _w9 / _c9 * 100
            else:
                _e9, _x9 = float(r2.get("entry") or 0), float(r2.get("exit") or 0)
                if _e9 and _x9:
                    _g9 = (_x9 / _e9 - 1) * 100
            if _g9 is not None and r2.get("net_pct") is not None:
                _n9 = round(_g9 - 0.23, 3)
                r2["gross_pct"] = round(_g9, 3)
                r2["net_pct"] = _n9
                r2["result"] = "win" if _n9 > 0 else "loss" if _n9 < 0 else "flat"
        out.append(r2)
    return out


def edited_count(day8: str) -> int:
    return sum(1 for x in _load() if x.get("day") == day8)
