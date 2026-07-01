"""call_grader.py — Milestone 1.2: measure every chatbot advice answer.

Logs each advice/decision the chatbot gives (ticker, action, ref price, target/stop,
horizon) to `chatbot_calls`, then grades it after the horizon by comparing the live
price move to the advised direction. Gives an HONEST, fast-maturing (default 60-min)
hit-rate per intent/action — the foundation for trusting (or not trusting) the bot.

Advisory-measurement only; no orders. Grading horizon is in MINUTES so short-term
calls mature quickly (unlike signal_log which is day-based).
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import text

from services.logger import log

_DDL = """
CREATE TABLE IF NOT EXISTS chatbot_calls (
    id           BIGSERIAL PRIMARY KEY,
    ts           TIMESTAMPTZ DEFAULT now(),
    agent_id     TEXT,
    lang         TEXT,
    intent       TEXT,                     -- advice | decision | scalp | position
    ticker       TEXT NOT NULL,
    name         TEXT,
    action       TEXT,                     -- BUY | SELL | HOLD | WATCH | AVOID
    ref_price    NUMERIC,
    target       NUMERIC,
    stop         NUMERIC,
    horizon_min  INTEGER DEFAULT 60,
    status       TEXT DEFAULT 'open',      -- open | graded
    graded_ts    TIMESTAMPTZ,
    exit_price   NUMERIC,
    actual_ret   NUMERIC,                  -- % move over the window
    outcome      TEXT                      -- win | loss | flat
);
CREATE INDEX IF NOT EXISTS idx_chatbot_calls_status ON chatbot_calls (status, ts);
"""

_FLAT = 0.30      # ±0.30% = "flat" for a HOLD/WATCH to be correct


def _ensure(db) -> None:
    try:
        for stmt in _DDL.strip().split(";"):
            if stmt.strip():
                db.execute(text(stmt))
        db.commit()
    except Exception as e:
        db.rollback(); log.warning(f"call_grader ensure: {str(e)[:120]}")


def log_call(db, *, ticker: str, action: str, intent: str = "advice",
             ref_price: Optional[float] = None, target: Optional[float] = None,
             stop: Optional[float] = None, horizon_min: int = 60,
             name: Optional[str] = None, agent_id: Optional[str] = None,
             lang: Optional[str] = None) -> None:
    """Record one advice answer for later grading. Best-effort (never breaks the reply)."""
    try:
        _ensure(db)
        db.execute(text(
            "INSERT INTO chatbot_calls (agent_id, lang, intent, ticker, name, action, "
            "ref_price, target, stop, horizon_min) VALUES "
            "(:a,:l,:i,:t,:n,:ac,:rp,:tg,:st,:h)"),
            {"a": agent_id, "l": lang, "i": intent, "t": str(ticker).zfill(6), "n": name,
             "ac": (action or "").upper(), "rp": ref_price, "tg": target, "st": stop,
             "h": int(horizon_min or 60)})
        db.commit()
    except Exception as e:
        db.rollback(); log.warning(f"call_grader log: {str(e)[:120]}")


def _live_price(ticker: str) -> Optional[float]:
    try:
        from services.assistant_agent import _live_price_for_code
        from services.stock_resolver import display_name
        q = _live_price_for_code(ticker, display_name(ticker))
        if q and q.get("price"):
            return float(q["price"])
    except Exception:
        pass
    return None


def grade_open(db) -> dict[str, Any]:
    """Grade calls whose horizon has elapsed. win/loss/flat vs the advised direction."""
    _ensure(db)
    rows = db.execute(text(
        "SELECT id, ticker, action, ref_price FROM chatbot_calls WHERE status='open' "
        "AND now() >= ts + make_interval(mins => horizon_min) ORDER BY ts LIMIT 200")).fetchall()
    graded = 0
    for r in rows:
        ref = float(r.ref_price) if r.ref_price else _live_price(r.ticker)
        exit_px = _live_price(r.ticker)
        if not ref or not exit_px:
            continue
        ret = (exit_px - ref) / ref * 100.0
        act = (r.action or "").upper()
        if act == "BUY":
            outcome = "win" if ret > 0.1 else "loss" if ret < -0.1 else "flat"
        elif act in ("SELL", "AVOID"):
            outcome = "win" if ret < -0.1 else "loss" if ret > 0.1 else "flat"
        else:  # HOLD / WATCH — "correct" if it stayed roughly flat
            outcome = "win" if abs(ret) <= _FLAT else "flat"
        db.execute(text(
            "UPDATE chatbot_calls SET status='graded', graded_ts=now(), exit_price=:x, "
            "actual_ret=:r, outcome=:o WHERE id=:id"),
            {"x": exit_px, "r": round(ret, 2), "o": outcome, "id": r.id})
        graded += 1
    db.commit()
    return {"graded": graded}


def scoreboard(db, days: int = 30) -> dict[str, Any]:
    """Chatbot hit-rate — overall + per intent + per action (graded, decisive only)."""
    _ensure(db)
    def _wr(where: str, params: dict) -> dict:
        rows = db.execute(text(
            "SELECT outcome, count(*) c FROM chatbot_calls WHERE status='graded' "
            f"AND ts >= now() - make_interval(days => :d) {where} GROUP BY outcome"),
            {**params, "d": days}).fetchall()
        d = {o: c for o, c in rows}
        w, l, f = d.get("win", 0), d.get("loss", 0), d.get("flat", 0)
        dec = w + l
        return {"win": w, "loss": l, "flat": f, "graded": w + l + f,
                "win_rate": round(w / dec * 100, 1) if dec else None}
    by_intent = {}
    for (intent,) in db.execute(text("SELECT DISTINCT intent FROM chatbot_calls WHERE status='graded'")).fetchall():
        by_intent[intent] = _wr("AND intent=:i", {"i": intent})
    return {"overall": _wr("", {}), "by_intent": by_intent,
            "open": db.execute(text("SELECT count(*) FROM chatbot_calls WHERE status='open'")).scalar()}
