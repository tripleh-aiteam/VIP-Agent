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


def hidden_count(day8: str) -> int:
    return sum(1 for x in _load() if x.get("day") == day8)
