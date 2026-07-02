"""call_grader.py — Milestone 1.2: measure every chatbot advice answer.

Logs each advice/decision the chatbot gives (ticker, action, ref price, target/stop,
horizon) to `chatbot_calls`, then grades it after the horizon by comparing the live
price move to the advised direction. Gives an HONEST, fast-maturing (default 60-min)
hit-rate per intent/action — the foundation for trusting (or not trusting) the bot.

Advisory-measurement only; no orders. Grading horizon is in MINUTES so short-term
calls mature quickly (unlike signal_log which is day-based).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import text

from services.logger import log

_KST = ZoneInfo("Asia/Seoul")

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


def _bars_in_window(db, ticker: str, ts_utc: datetime, horizon_min: int) -> list:
    """1-min bars (KST-naive table) covering [call time, call time + horizon].
    Starts at the call's own minute (a stop touched seconds after the call must count)."""
    try:
        start = ts_utc.astimezone(_KST).replace(tzinfo=None)
        end = start + timedelta(minutes=int(horizon_min or 60))
        return db.execute(text(
            "SELECT ts, high, low, close FROM raw_minute_prices "
            "WHERE ticker=:t AND ts >= :s AND ts <= :e ORDER BY ts"),
            {"t": ticker, "s": start.replace(second=0, microsecond=0), "e": end}).fetchall()
    except Exception:
        db.rollback()          # a failed SELECT must not abort the grading transaction
        return []


def _daily_close_after(db, ticker: str, ts_utc: datetime) -> Optional[float]:
    """Fallback exit for LATE grading with no minute data: the call day's daily close —
    or the NEXT session's close when the call itself came after the 15:30 close (an
    evening call must not be graded against a price from before it was made)."""
    try:
        kst = ts_utc.astimezone(_KST)
        d = kst.date()
        op = ">" if (kst.hour, kst.minute) >= (15, 30) else ">="
        row = db.execute(text(
            f"SELECT close FROM raw_daily_prices WHERE ticker=:t AND date {op} :d "
            "ORDER BY date LIMIT 1"), {"t": ticker, "d": d}).fetchone()
        return float(row.close) if row and row.close else None
    except Exception:
        db.rollback()
        return None


def grade_open(db) -> dict[str, Any]:
    """Grade calls whose horizon has elapsed.

    Scalp-honest grading: with minute bars, a directional call is graded on the PATH —
    did the advised target or stop get TOUCHED first inside the window (the user's real
    trade: sell at +1% / cut at stop)? Stop is checked before target within a bar
    (conservative). Only without bars do we fall back to endpoint grading — the live
    price if grading is on time, or the call day's daily close if we woke up late
    (never "price right now" hours after the window, which graded everything flat)."""
    _ensure(db)
    now = datetime.now(timezone.utc)
    rows = db.execute(text(
        "SELECT id, ts, intent, ticker, action, ref_price, target, stop, horizon_min "
        "FROM chatbot_calls WHERE status='open' "
        "AND now() >= ts + make_interval(mins => horizon_min) ORDER BY ts LIMIT 200")).fetchall()
    graded = 0
    for r in rows:
        ref = float(r.ref_price) if r.ref_price else None
        tgt = float(r.target) if r.target else None
        stp = float(r.stop) if r.stop else None
        act = (r.action or "").upper()
        horizon = int(r.horizon_min or 60)
        on_time = now <= r.ts + timedelta(minutes=horizon + 30)

        bars = _bars_in_window(db, r.ticker, r.ts, horizon)
        outcome = None
        exit_px = None
        if bars:
            # Path grading only when the levels are COHERENT vs the price at call time
            # (stop below ref below target). A WAIT whose buy-zone target already sits
            # under the live price would otherwise auto-grade on the first bar.
            if act == "BUY" and tgt and stp and ref and stp < ref < tgt:
                for b in bars:                       # path: stop-before-target per bar
                    if b.low and float(b.low) <= stp:
                        outcome, exit_px = "loss", stp
                        break
                    if b.high and float(b.high) >= tgt:
                        outcome, exit_px = "win", tgt
                        break
            elif (act in ("HOLD", "WATCH") and tgt and ref and tgt > ref
                  and (r.intent or "") == "scalp"):
                # scalp WAIT/SKIP — punish a missed move (the target the bot itself named
                # got hit while it said wait). Scalp-only: a decision HOLD's wave target
                # is a multi-day level and must not grade a 60-min window.
                hit = any(b.high and float(b.high) >= tgt for b in bars)
                outcome = "loss" if hit else "win"
            if exit_px is None and bars[-1].close:
                exit_px = float(bars[-1].close)      # exit at the window's true end
        elif on_time:
            exit_px = _live_price(r.ticker)
        else:
            exit_px = _daily_close_after(db, r.ticker, r.ts)
            if exit_px is None:
                if (now - r.ts).days >= 7:           # ungradable — retire, excluded from stats
                    db.execute(text(
                        "UPDATE chatbot_calls SET status='graded', graded_ts=now(), "
                        "outcome='void' WHERE id=:id"), {"id": r.id})
                    graded += 1
                continue                              # else wait for data, stay open

        ref = ref or exit_px
        if not ref or not exit_px:
            continue
        ret = (exit_px - ref) / ref * 100.0
        if outcome is None:
            if act == "BUY":
                # timeout exit: profitable after ~0.25% costs = win
                outcome = "win" if ret >= 0.3 else "loss" if ret <= -0.3 else "flat"
            elif act in ("SELL", "AVOID"):
                outcome = "win" if ret <= -0.3 else "loss" if ret >= 0.3 else "flat"
            else:  # HOLD/WATCH without a target — right if it stayed flat, wrong if it ran
                outcome = "win" if abs(ret) <= _FLAT else "loss" if abs(ret) >= 1.0 else "flat"
        db.execute(text(
            "UPDATE chatbot_calls SET status='graded', graded_ts=now(), exit_price=:x, "
            "actual_ret=:r, outcome=:o WHERE id=:id"),
            {"x": exit_px, "r": round(ret, 2), "o": outcome, "id": r.id})
        graded += 1
    db.commit()
    return {"graded": graded}


def track_record_line(db, intent: str, lang: Optional[str] = None, days: int = 30) -> Optional[str]:
    """One appendable line showing this answer type's REAL graded record — the user
    should see measured trust, not vibes. Hidden until a minimal sample exists."""
    try:
        st = (scoreboard(db, days=days).get("by_intent") or {}).get(intent) or {}
        if (st.get("graded") or 0) < 3:
            return None
        en = str(lang or "").lower().startswith("en")
        w, l, f = st.get("win", 0), st.get("loss", 0), st.get("flat", 0)
        wr = st.get("win_rate")
        if en:
            return (f"\n\n📊 Track record (this answer type, last {days}d): {w}W·{l}L"
                    + (f"·{f} flat" if f else "")
                    + (f" · win rate {wr}%" if wr is not None else "")
                    + " — measured on real graded calls, not estimates.")
        return (f"\n\n📊 실측 성적 (이 유형 답변, 최근 {days}일): {w}승·{l}패"
                + (f"·무승부 {f}" if f else "")
                + (f" · 승률 {wr}%" if wr is not None else "")
                + " — 추정이 아니라 실제 채점된 결과입니다.")
    except Exception:
        return None


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
