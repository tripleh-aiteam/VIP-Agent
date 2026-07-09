"""auto_trader.py — Phase 4: the AUTO-AGENT. The decision engine's hands.

Runs the boss's buy→sell→buy→sell loop AUTOMATICALLY on the paper desk (fake money):
  1. every tick (external cron ~5min + the desk page's poll) it asks the SAME decision
     engine the chatbot uses (intraday_setup.scan) for ACT_NOW setups;
  2. buys the best one on the paper desk (paper_desk.place_order — live prices, real
     fees), sized by AUTO_POS_PCT of desk equity;
  3. manages each open auto-position: SELL at the target band (first touch), SELL at
     the stop, SELL after the time-stop — the exact zone rules of the setup;
  4. every trade lands in the desk's history + its own auto_trades log, so the
     scorecard answers the ONLY question that matters: "does following the engine
     make money?" — with zero real won at risk.

Safety rails: OFF by default (auto_state.enabled, toggled from the Testing page);
max concurrent auto-positions; max trades/day; market-hours only; never touches
real money — the ONLY order path is the paper desk.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text

logger = logging.getLogger("vip.auto_trader")
KST = timezone(timedelta(hours=9))

AUTO_POS_PCT = 10.0          # % of desk equity per trade
MAX_OPEN = 2                 # concurrent auto-positions
MAX_TRADES_DAY = 6           # hard daily cap (boss trades "3-4 times a day")
MIN_CONF = 60                # only take setups the engine is reasonably sure about

_DDL = (
    "CREATE TABLE IF NOT EXISTS auto_state ("
    " id INT PRIMARY KEY DEFAULT 1, enabled BOOLEAN DEFAULT FALSE,"
    " updated_at TIMESTAMPTZ DEFAULT now())",
    "CREATE TABLE IF NOT EXISTS auto_trades ("
    " id SERIAL PRIMARY KEY, ticker TEXT, name TEXT, setup_type TEXT,"
    " qty BIGINT, entry DOUBLE PRECISION, target_lo DOUBLE PRECISION,"
    " target_hi DOUBLE PRECISION, stop DOUBLE PRECISION, time_min INT,"
    " confidence INT, status TEXT DEFAULT 'OPEN',"
    " exit_price DOUBLE PRECISION, exit_reason TEXT, net_pct DOUBLE PRECISION,"
    " opened_at TIMESTAMPTZ DEFAULT now(), closed_at TIMESTAMPTZ)",
)


def _ensure(db) -> None:
    for ddl in _DDL:
        db.execute(text(ddl))
    r = db.execute(text("SELECT 1 FROM auto_state WHERE id=1")).first()
    if not r:
        db.execute(text("INSERT INTO auto_state (id, enabled) VALUES (1, FALSE)"))
    db.commit()


def is_enabled(db) -> bool:
    _ensure(db)
    return bool(db.execute(text("SELECT enabled FROM auto_state WHERE id=1")).scalar())


def set_enabled(db, on: bool) -> dict:
    _ensure(db)
    db.execute(text("UPDATE auto_state SET enabled=:e, updated_at=now() WHERE id=1"),
               {"e": bool(on)})
    db.commit()
    return {"ok": True, "enabled": bool(on)}


def _market_open_now() -> bool:
    n = datetime.now(KST)
    return n.weekday() < 5 and (9 * 60) <= (n.hour * 60 + n.minute) <= (15 * 60 + 20)


def _live_px(code: str) -> Optional[float]:
    from services.paper_desk import _live_price
    px, _ = _live_price(code)
    return px


def tick(db, force: bool = False) -> dict[str, Any]:
    """One auto-agent pass: manage exits first (protect), then consider a new entry.
    Idempotent; safe to fire every few minutes. force=True ignores market hours
    (testing only — exits still honest, entries use live/last prices)."""
    _ensure(db)
    out: dict[str, Any] = {"enabled": is_enabled(db), "closed": [], "opened": None,
                           "reason": None}
    if not out["enabled"]:
        out["reason"] = "auto-trading is OFF"
        return out
    if not force and not _market_open_now():
        out["reason"] = "market closed"
        return out

    # ---- 1) MANAGE OPEN AUTO-POSITIONS (exits first — protection before opportunity) --
    from services.paper_desk import place_order
    open_rows = db.execute(text(
        "SELECT id, ticker, name, qty, entry, target_lo, stop, time_min, "
        "EXTRACT(EPOCH FROM (now()-opened_at))/60 AS age_min "
        "FROM auto_trades WHERE status='OPEN'")).fetchall()
    for oid, tk, name, qty, entry, tlo, stop, tmin, age in open_rows:
        px = _live_px(tk)
        if px is None:
            continue
        reason = None
        if px >= float(tlo):
            reason = "TARGET"
        elif px <= float(stop):
            reason = "STOP"
        elif age is not None and float(age) >= float(tmin):
            reason = "TIME"
        if not reason:
            continue
        r = place_order(db, tk, "SELL", int(qty), "market")
        if r.get("ok"):
            fill = float(r.get("fill_price") or px)
            net = (fill / float(entry) - 1) * 100 - 0.23
            db.execute(text(
                "UPDATE auto_trades SET status='CLOSED', exit_price=:x, exit_reason=:rr, "
                "net_pct=:n, closed_at=now() WHERE id=:i"),
                {"x": fill, "rr": reason, "n": round(net, 3), "i": oid})
            db.commit()
            out["closed"].append({"name": name, "reason": reason, "net_pct": round(net, 2)})
        else:
            logger.warning("auto_trader: SELL failed for %s: %s", tk, r.get("error"))

    # ---- 2) NEW ENTRY (one per tick, capped) ----
    n_open = db.execute(text(
        "SELECT count(*) FROM auto_trades WHERE status='OPEN'")).scalar() or 0
    if int(n_open) >= MAX_OPEN:
        out["reason"] = f"max open positions ({MAX_OPEN})"
        return out
    n_today = db.execute(text(
        "SELECT count(*) FROM auto_trades WHERE opened_at::date = (now() AT TIME ZONE 'Asia/Seoul')::date")).scalar() or 0
    if int(n_today) >= MAX_TRADES_DAY:
        out["reason"] = f"daily trade cap ({MAX_TRADES_DAY})"
        return out

    from services.intraday_setup import scan
    setups = [s for s in (scan(db, use_cache=False).get("act_now") or [])
              if s.get("confidence", 0) >= MIN_CONF and s.get("price")]
    # skip tickers we already hold as auto-positions (no doubling)
    held = {r[0] for r in db.execute(text(
        "SELECT ticker FROM auto_trades WHERE status='OPEN'")).fetchall()}
    setups = [s for s in setups if s["code"] not in held]
    if not setups:
        out["reason"] = out["reason"] or "no qualifying setup"
        return out
    # DECISION-ENGINE VETO (boss 2026-07-09: "auto-trading must listen to the decision
    # engine"): before buying, ask the full 9-method fused verdict. SELL → forbidden
    # (skip to the next candidate). WATCH/HOLD = no objection — it's a 60-minute trade,
    # not an investment. Engine unavailable → fail-open: the scanner's own ML/news/regime
    # gates already passed, and a dead engine must not silently halt the whole agent.
    picked = None
    out["vetoed"] = []
    for cand in setups[:3]:
        try:
            from services.decision_agent import decide
            _d = decide(db, cand["code"]) or {}
            if _d.get("decision") == "SELL":
                out["vetoed"].append({"name": cand["name"], "code": cand["code"],
                                      "reason": "decision engine says SELL"})
                logger.info("auto_trader veto: %s — decision engine SELL", cand["name"])
                continue
        except Exception as e:
            logger.warning("auto_trader: decide() failed for %s (%s) — proceeding on scanner gates",
                           cand["code"], str(e)[:80])
            db.rollback()
        picked = cand
        break
    if not picked:
        out["reason"] = "all candidates vetoed by the decision engine (SELL)"
        return out
    s = picked                                     # best non-vetoed (scan sorts by AI prob + conf)
    # position size: AUTO_POS_PCT of desk equity
    from services.paper_desk import state as desk_state
    eq = float((desk_state(db) or {}).get("equity") or 0)
    budget = eq * AUTO_POS_PCT / 100.0
    qty = int(budget // float(s["price"]))
    if qty < 1:
        out["reason"] = "equity too small for 1 share of the setup"
        return out
    r = place_order(db, s["code"], "BUY", qty, "market")
    if not r.get("ok"):
        out["reason"] = f"BUY failed: {r.get('error') or r.get('reason')}"
        return out
    fill = float(r.get("fill_price") or s["price"])
    db.execute(text(
        "INSERT INTO auto_trades (ticker, name, setup_type, qty, entry, target_lo, "
        "target_hi, stop, time_min, confidence) VALUES (:t,:n,:st,:q,:e,:tl,:th,:s,:tm,:c)"),
        {"t": s["code"], "n": s["name"], "st": s.get("setup_type") or "dip", "q": qty,
         "e": fill, "tl": s["target_band"][0], "th": s["target_band"][1],
         "s": s["stop"], "tm": s["time_min"], "c": s["confidence"]})
    db.commit()
    out["opened"] = {"name": s["name"], "qty": qty, "entry": fill,
                     "target": s["target_band"], "stop": s["stop"],
                     "confidence": s["confidence"], "type": s.get("setup_type")}
    return out


def status(db) -> dict[str, Any]:
    """The auto-agent's own scorecard + open positions (for the Testing page)."""
    _ensure(db)
    open_rows = [dict(r._mapping) for r in db.execute(text(
        "SELECT ticker, name, qty, entry, target_lo, stop, time_min, confidence, opened_at "
        "FROM auto_trades WHERE status='OPEN' ORDER BY opened_at DESC"))]
    closed = db.execute(text(
        "SELECT count(*), count(*) FILTER (WHERE net_pct > 0), "
        "round(sum(net_pct)::numeric, 2), round(avg(net_pct)::numeric, 3) "
        "FROM auto_trades WHERE status='CLOSED'")).first()
    n, wins, tot, avg = (closed or (0, 0, None, None))
    recent = [dict(r._mapping) for r in db.execute(text(
        "SELECT name, exit_reason, net_pct, closed_at FROM auto_trades "
        "WHERE status='CLOSED' ORDER BY closed_at DESC LIMIT 10"))]
    return {"enabled": is_enabled(db), "open": open_rows,
            "record": {"trades": int(n or 0), "wins": int(wins or 0),
                       "win_rate": round(int(wins or 0) / int(n) * 100, 1) if n else None,
                       "total_net_pct": float(tot) if tot is not None else 0.0,
                       "avg_net_pct": float(avg) if avg is not None else None},
            "recent": recent,
            "limits": {"pos_pct": AUTO_POS_PCT, "max_open": MAX_OPEN,
                       "max_trades_day": MAX_TRADES_DAY, "min_conf": MIN_CONF}}
