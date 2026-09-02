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


def edit(day8: str, code: str, field: str, frm: str, to: str) -> None:
    """Register one time override. field: buy_t | sell_t. frm may be '' =
    the stock's first matching trip that day."""
    items = [x for x in _load()
             if not (x.get("day") == day8 and x.get("code") == code
                     and x.get("field") == field and x.get("frm") == (frm or "")[:5])]
    items.append({"day": day8, "code": code, "field": field,
                  "frm": (frm or "")[:5], "to": (to or "")[:5]})
    _save(items[-200:])


def restore(day8: str, code: Optional[str] = None) -> int:
    items = _load()
    keep = [x for x in items
            if not (x.get("day") == day8 and (code is None or x.get("code") == code))]
    _save(keep)
    return len(items) - len(keep)


def apply_rows(rows: list, day8: str) -> list:
    """Rewrite buy_t/sell_t on matching rows (completed trips AND holdings)."""
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
            if not cur:
                continue
            if e.get("frm") and cur[:5] != e["frm"]:
                continue
            r2 = {**r2, f: e["to"] + cur[5:]}
        out.append(r2)
    return out


def edited_count(day8: str) -> int:
    return sum(1 for x in _load() if x.get("day") == day8)
