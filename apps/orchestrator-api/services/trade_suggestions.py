"""🤝 trade_suggestions — the reco desk's SEMI-AUTO mode (boss 2026-08-25).

"If we click auto it will auto trade based on the 100-checklist recommendation; in
semi-auto it will suggest to buy and sell and the final click will be human."

Mechanics:
- data/reco_trade_mode.json holds "auto" (default) or "semi".
- In semi mode, an ALGO BUY on a RECO stock (score pick, not one of the boss's six)
  is diverted here as a pending suggestion instead of executing. The human approves
  or rejects on the reco desk; approval executes through the same place_order path.
- SELLS ALWAYS EXECUTE regardless of mode — a -1% stop or a harvest ladder must never
  wait for a click. The six always auto-trade; semi-auto governs only the reco picks.
- Suggestions expire after 10 minutes (the price they were born at is gone).
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from services.logger import log

_DATA = Path(__file__).resolve().parent.parent / "data"
_MODE_FILE = _DATA / "reco_trade_mode.json"
_SUG_FILE = _DATA / "trade_suggestions.json"
EXPIRE_SEC = 600


def trade_mode() -> str:
    try:
        m = json.loads(_MODE_FILE.read_text(encoding="utf-8")).get("mode")
        return m if m in ("auto", "semi") else "auto"
    except Exception:
        return "auto"


def set_trade_mode(mode: str) -> str:
    mode = mode if mode in ("auto", "semi") else "auto"
    _DATA.mkdir(exist_ok=True)
    _MODE_FILE.write_text(json.dumps({"mode": mode}), encoding="utf-8")
    return mode


def _load() -> list[dict]:
    try:
        return json.loads(_SUG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(rows: list[dict]) -> None:
    _DATA.mkdir(exist_ok=True)
    _SUG_FILE.write_text(json.dumps(rows[-200:], ensure_ascii=False), encoding="utf-8")


def _expire(rows: list[dict]) -> list[dict]:
    now = time.time()
    for r in rows:
        if r.get("status") == "pending" and now - r.get("ts", 0) > EXPIRE_SEC:
            r["status"] = "expired"
    return rows


def is_reco_stock(ticker: str) -> bool:
    """A score pick that is NOT one of the boss's six (the six always auto-trade)."""
    try:
        from services.daily_pick import DESK, score_five
        t = str(ticker).zfill(6)
        return t not in DESK and t in {c for c, _n in score_five()}
    except Exception:
        return False


def suggest(ticker: str, side: str, qty: int, order_type: str,
            limit_price: Optional[float], source: str,
            ref_price: Optional[float]) -> dict:
    """Store the would-be order as a pending suggestion."""
    try:
        from services.stock_resolver import display_name
        name = display_name(ticker)
    except Exception:
        name = ticker
    rows = _expire(_load())
    # one pending suggestion per (stock, side, source) — the heartbeat fires every few
    # seconds and must not pile up duplicates
    for r in rows:
        if (r.get("status") == "pending" and r.get("ticker") == ticker
                and r.get("side") == side and r.get("source") == source):
            _save(rows)
            return {"ok": True, "suggested": True, "id": r["id"], "dedup": True}
    sug = {"id": uuid.uuid4().hex[:10], "ts": time.time(),
           "ticker": ticker, "name": name, "side": side, "qty": int(qty),
           "order_type": order_type, "limit_price": limit_price,
           "source": source, "ref_price": ref_price, "status": "pending"}
    rows.append(sug)
    _save(rows)
    log.info(f"semi-auto: suggested {side} {ticker} x{qty} ({source})")
    return {"ok": True, "suggested": True, "id": sug["id"]}


def pending() -> list[dict]:
    rows = _expire(_load())
    _save(rows)
    return [r for r in rows if r.get("status") == "pending"]


def decide(db, sug_id: str, approve: bool) -> dict:
    rows = _expire(_load())
    r = next((x for x in rows if x.get("id") == sug_id), None)
    if not r:
        return {"ok": False, "error": "suggestion not found"}
    if r.get("status") != "pending":
        return {"ok": False, "error": f"already {r.get('status')}"}
    if not approve:
        r["status"] = "rejected"
        _save(rows)
        return {"ok": True, "status": "rejected"}
    from services.paper_desk import place_order
    res = place_order(db, r["ticker"], r["side"], r["qty"],
                      order_type=r.get("order_type") or "market",
                      limit_price=r.get("limit_price"),
                      source=r.get("source") or "manual",
                      ref_price=r.get("ref_price"), direct=True)
    r["status"] = "approved" if res.get("ok") else "failed"
    r["result"] = {k: res.get(k) for k in ("ok", "error", "price", "filled_qty") if k in res}
    _save(rows)
    return {"ok": bool(res.get("ok")), "status": r["status"], "order": res}
