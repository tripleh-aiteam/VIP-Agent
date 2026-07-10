"""position_guard.py — automatic protection for the boss's FOCUS holdings (2026-07-10).

His spec, verbatim: 삼성전자/SK하이닉스 positions he bought (semi/manual) must be
protected without him watching:
  1. price falls 1%+ below his average buy price       → SELL immediately (손절)
  2. after being up 1%+, price falls 1% from its PEAK  → SELL immediately (이익 보호)
  3. while it's up and the engine still sees more rise → tell him "don't sell — why"
     (the advice text is composed in auto_trader.focus_status from guard info here)

Runs from paper_desk.state() (every ~4s while his page is open) and from the
auto-trader's 5-min cron tick as the backstop. Positions currently managed by an
OPEN auto-trade are skipped — the auto's own target/stop/time doors handle those.
Paper money only.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import text

logger = logging.getLogger("vip.position_guard")

GUARD_CODES = ["005930", "000660"]   # the boss's focus stocks
STOP_PCT = 1.0                       # sell at -1% from his average buy price
ARM_PCT = 1.0                        # trailing activates once +1% above average…
TRAIL_PCT = 1.0                      # …then sell on a 1% drop from the peak

_DDL = ("CREATE TABLE IF NOT EXISTS guard_peaks ("
        " ticker TEXT PRIMARY KEY, avg_price DOUBLE PRECISION,"
        " peak DOUBLE PRECISION, updated_at TIMESTAMPTZ DEFAULT now())")


def _ensure(db) -> None:
    try:
        db.execute(text(_DDL))
        db.commit()
    except Exception:
        db.rollback()


def run(db) -> list[dict[str, Any]]:
    """One guard pass over the focus positions. Returns the sells it executed."""
    _ensure(db)
    actions: list[dict[str, Any]] = []
    try:
        rows = db.execute(text(
            "SELECT ticker, qty, avg_price FROM paper_desk_positions "
            "WHERE ticker = ANY(:t) AND qty > 0"), {"t": GUARD_CODES}).fetchall()
    except Exception:
        db.rollback()
        return actions
    if not rows:
        return actions
    auto_held = set()
    try:
        auto_held = {r[0] for r in db.execute(text(
            "SELECT ticker FROM auto_trades WHERE status='OPEN'")).fetchall()}
    except Exception:
        db.rollback()
    from services.paper_desk import _live_price, place_order
    for tk, qty, avg in rows:
        if tk in auto_held:            # the auto-trade's own doors manage this one
            continue
        px, _ = _live_price(tk)
        if not px or not avg:
            continue
        # peak tracking — reset when the lot changed (new avg = new position)
        peak = float(px)
        try:
            r = db.execute(text(
                "SELECT avg_price, peak FROM guard_peaks WHERE ticker=:t"), {"t": tk}).first()
            if r and r[0] is not None and abs(float(r[0]) - float(avg)) < 0.5:
                peak = max(float(r[1] or px), float(px))
            db.execute(text(
                "INSERT INTO guard_peaks (ticker, avg_price, peak) VALUES (:t,:a,:p) "
                "ON CONFLICT (ticker) DO UPDATE SET avg_price=:a, peak=:p, updated_at=now()"),
                {"t": tk, "a": float(avg), "p": peak})
            db.commit()
        except Exception:
            db.rollback()
        stop_line = float(avg) * (1 - STOP_PCT / 100)
        armed = peak >= float(avg) * (1 + ARM_PCT / 100)
        trail_line = peak * (1 - TRAIL_PCT / 100) if armed else None
        reason: Optional[str] = None
        if float(px) <= stop_line:
            reason = "GUARD_STOP"          # -1% below his buy price
        elif trail_line is not None and float(px) <= trail_line:
            reason = "GUARD_TRAIL"         # was +1%+, now -1% off the peak
        if not reason:
            continue
        r = place_order(db, tk, "SELL", int(qty), "market")
        if r.get("ok"):
            try:
                db.execute(text("DELETE FROM guard_peaks WHERE ticker=:t"), {"t": tk})
                db.commit()
            except Exception:
                db.rollback()
            actions.append({"ticker": tk, "qty": int(qty), "reason": reason,
                            "fill": r.get("fill_price"), "avg": float(avg),
                            "peak": round(peak)})
            logger.info("position_guard SELL %s x%s (%s) fill=%s", tk, qty, reason,
                        r.get("fill_price"))
    return actions


def info(db, code: str) -> Optional[dict[str, Any]]:
    """Guard status for one focus stock's panel: holding, lines, armed state."""
    _ensure(db)
    try:
        row = db.execute(text(
            "SELECT qty, avg_price FROM paper_desk_positions WHERE ticker=:t AND qty>0"),
            {"t": str(code).zfill(6)}).first()
    except Exception:
        db.rollback()
        return None
    if not row:
        return None
    qty, avg = int(row[0]), float(row[1])
    peak = None
    try:
        r = db.execute(text("SELECT peak FROM guard_peaks WHERE ticker=:t"),
                       {"t": str(code).zfill(6)}).first()
        peak = float(r[0]) if r and r[0] is not None else None
    except Exception:
        db.rollback()
    auto_managed = False
    try:
        auto_managed = bool(db.execute(text(
            "SELECT 1 FROM auto_trades WHERE status='OPEN' AND ticker=:t"),
            {"t": str(code).zfill(6)}).first())
    except Exception:
        db.rollback()
    armed = peak is not None and peak >= avg * (1 + ARM_PCT / 100)
    return {"qty": qty, "avg": avg, "peak": peak, "armed": armed,
            "auto_managed": auto_managed,
            "stop_line": round(avg * (1 - STOP_PCT / 100)),
            "trail_line": round(peak * (1 - TRAIL_PCT / 100)) if armed and peak else None}
