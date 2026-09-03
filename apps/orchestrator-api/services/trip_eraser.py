# -*- coding: utf-8 -*-
"""trip_eraser — delete trading-history rows by chat (boss 2026-09-01: "since I
am using fake money... I wanna even delete trading history — 'remove SK하이닉스
which bought at 10:17' should delete it").

Display-level erasure, same philosophy as the KOSDAQ menu filter (2026-08-26):
the row vanishes from BOTH menus' history tables, the underlying order records
and desk accounting stay untouched — so cash/P&L stay honest and "복원해줘"
brings every hidden row back. Hidden keys are day-scoped.

data/hidden_trips.json: [{"day": "YYYYMMDD", "code": "005930", "t": "10:17"|""}]
(t empty = every trip of that stock that day)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

_FILE = Path(__file__).resolve().parent.parent / "data" / "hidden_trips.json"


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


def hide(day8: str, code: str, t: str = "") -> int:
    """Hide one trip (t='HH:MM' of its BUY) or all of a stock's trips that day.
    Returns how many hide-rules now exist for that day."""
    items = _load()
    key = {"day": day8, "code": code, "t": (t or "")[:5]}
    if key not in items:
        items.append(key)
    _save(items[-200:])
    return sum(1 for x in items if x.get("day") == day8)


def restore(day8: str, code: Optional[str] = None) -> int:
    """Un-hide — a specific stock's rules, or every rule for the day. Returns
    how many rules were removed."""
    items = _load()
    keep = [x for x in items
            if not (x.get("day") == day8 and (code is None or x.get("code") == code))]
    _save(keep)
    return len(items) - len(keep)


def filter_rows(rows: list, day8: str) -> list:
    """Drop hidden trips from a family-trades rows list (both menus call this)."""
    rules = [x for x in _load() if x.get("day") == day8]
    if not rules:
        return rows
    out = []
    for r in rows:
        code = str(r.get("code") or "")
        buy_t = str(r.get("buy_t") or "")[:5]
        hidden = any(x.get("code") == code and (not x.get("t") or x.get("t") == buy_t)
                     for x in rules)
        if not hidden:
            out.append(r)
    return out


def filter_holding(hold: list, day8: str) -> list:
    """Drop hidden HOLDING rows too (boss 2026-09-01: a pasted 'holding — not
    sold yet' row should also be hideable). Same matching as filter_rows."""
    return filter_rows(hold, day8)


def hidden_count(day8: str) -> int:
    return sum(1 for x in _load() if x.get("day") == day8)


def filter_m3_log(log: list, day8: str) -> list:
    """MENU 3's trading history obeys the eraser too (boss 2026-09-03 13:5x:
    'he said deleted and when I check it is not deleting' — the chat lane
    registered the hide but only menus 1/2 read the registry). A Menu 3 log
    row matches a rule by code + the trip's BUY time: for a SELL row that is
    buy_at, for a BUY row its own at/hhmm; t empty hides the whole stock."""
    rules = [x for x in _load() if x.get("day") == day8]
    if not rules:
        return log
    out = []
    for l in log:
        code = str(l.get("code") or "")
        bt = (str(l.get("buy_at") or "")[:5] if l.get("side") == "SELL"
              else str(l.get("at") or l.get("hhmm") or "")[:5])
        hidden = any(x.get("code") == code and (not x.get("t") or x.get("t") == bt)
                     for x in rules)
        if not hidden:
            out.append(l)
    return out


def filter_m3_held(held: list, day8: str) -> list:
    """Menu 3 holding rows: match by code + the lot's buy time (at)."""
    rules = [x for x in _load() if x.get("day") == day8]
    if not rules:
        return held
    out = []
    for h in held:
        code = str(h.get("code") or "")
        bt = str(h.get("at") or "")[:5]
        hidden = any(x.get("code") == code and (not x.get("t") or x.get("t") == bt)
                     for x in rules)
        if not hidden:
            out.append(h)
    return out
