"""scorekeeper_service — log every day's BUY/SELL calls (both methods) and grade them
against what actually happened. This is what turns "it works" into a real track record:
win rate, average return, hit rate — per method, accumulated over time.

Flow:
  log_today(db)    — snapshot today's actionable ML + Analysis signals -> signal_log
  grade_matured(db)— for signals older than their horizon, walk the actual price path and
                     mark win/loss/flat + actual return (entry->target before stop?)
  scoreboard(db)   — aggregate per method (what the UI / report reads)

Pure DB + arithmetic (no ML deps), so it runs on Render via the scheduler. The signal
levels come from trading_brief (same entry/target/stop the UI shows), so we grade
EXACTLY what the user saw.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import text

from services import prediction_service as ps
from services.prediction_service import NAMES

DDL = """
CREATE TABLE IF NOT EXISTS signal_log (
    id           BIGSERIAL PRIMARY KEY,
    logged_date  DATE NOT NULL,
    method       TEXT NOT NULL,            -- 'ml' | 'analysis'
    ticker       TEXT NOT NULL,
    signal       TEXT,                     -- BUY | SELL
    ref_price    NUMERIC,                  -- price when the call was made
    entry        NUMERIC, target NUMERIC, stop NUMERIC,
    horizon      SMALLINT DEFAULT 5,
    status       TEXT DEFAULT 'open',      -- open | graded
    graded_date  DATE,
    hit_target   BOOLEAN, hit_stop BOOLEAN,
    actual_ret   NUMERIC,                  -- realized trade P&L (%, positive = profit)
    outcome      TEXT,                     -- win | loss | flat
    created_at   TIMESTAMPTZ DEFAULT now(),
    UNIQUE (logged_date, method, ticker)
);
CREATE INDEX IF NOT EXISTS idx_signal_log_status ON signal_log (status, logged_date);
"""


def ensure(db):
    for stmt in DDL.strip().split(";"):
        if stmt.strip():
            db.execute(text(stmt))
    db.commit()


# --------------------------------------------------------------------------- #
# LOG — snapshot today's actionable signals (both methods)
# --------------------------------------------------------------------------- #
def log_today(db, horizon: int = 5) -> dict[str, Any]:
    ensure(db)
    from services import trading_brief as tb
    as_of = ps._latest_as_of(db, horizon)
    if not as_of:
        return {"ok": False, "reason": "no predictions yet"}
    tickers = list(NAMES.keys())
    n = 0

    def _store(method: str, tk: str, signal: str, lv: dict):
        nonlocal n
        if signal not in ("BUY", "SELL") or not lv.get("entry"):
            return
        db.execute(text(
            "INSERT INTO signal_log (logged_date, method, ticker, signal, ref_price, "
            "entry, target, stop, horizon) VALUES (:d,:m,:t,:s,:rp,:e,:tg,:st,:h) "
            "ON CONFLICT (logged_date, method, ticker) DO NOTHING"),
            {"d": as_of, "m": method, "t": tk, "s": signal, "rp": lv.get("close"),
             "e": lv.get("entry"), "tg": lv.get("target"), "st": lv.get("stop"), "h": horizon})
        n += 1

    # ML method — from the model's picks (BUY/SELL only)
    for tk in tickers:
        card = tb.stock_card(db, tk, horizon, as_of=as_of)
        _store("ml", tk, card.get("advice"), card.get("levels") or {})

    # Analysis method — its own signal
    an = tb.analysis_batch(db, tickers, horizon)
    for tk, r in an.items():
        _store("analysis", tk, r.get("signal"), r.get("levels") or {})

    # Consensus — stocks where BOTH methods agree (high conviction)
    for c in tb.consensus_picks(db, horizon):
        _store("consensus", c["ticker"], c["signal"], c.get("levels") or {})

    db.commit()
    return {"ok": True, "as_of": str(as_of), "logged": n}


# --------------------------------------------------------------------------- #
# BACKFILL — replay the Analysis method on past dates for an IMMEDIATE track record
# (deterministic: box + EOD 수급 as-of each date; no look-ahead). ML can't be cheaply
# replayed (needs per-date retraining) so it accumulates forward only.
# --------------------------------------------------------------------------- #
def _box_asof(db, ticker, asof):
    rows = db.execute(text(
        "SELECT high, low, close FROM raw_daily_prices WHERE ticker=:t AND date<=:d "
        "ORDER BY date DESC LIMIT 20"), {"t": ticker, "d": asof}).fetchall()
    if len(rows) < 20:
        return None
    return float(rows[0].close), min(float(r.low) for r in rows), max(float(r.high) for r in rows)


def _signal_asof(db, ticker, asof):
    box = _box_asof(db, ticker, asof)
    if not box:
        return None
    close, sup, res = box
    fr = db.execute(text(
        "SELECT SUM(foreign_net) f, SUM(inst_net) i FROM (SELECT foreign_net, inst_net "
        "FROM raw_investor_flows WHERE ticker=:t AND date<=:d ORDER BY date DESC LIMIT 5) q"),
        {"t": ticker, "d": asof}).first()
    f5 = float(fr.f or 0) if fr else 0
    i5 = float(fr.i or 0) if fr else 0
    score = 0
    if f5 > 0 and i5 > 0:
        score += 2
    elif f5 < 0 and i5 < 0:
        score -= 2
    if res > sup:
        pos = (close - sup) / (res - sup)
        score += 1 if pos < 0.33 else (-1 if pos > 0.67 else 0)
    sig = "BUY" if score >= 2 else "SELL" if score <= -2 else None
    if not sig:
        return None
    # REALISTIC vol-based target (matches trading_brief._box_levels): 1σ horizon move
    # from realized_vol AS-OF this date (no look-ahead), capped by the box edge.
    import math
    vr = db.execute(text(
        "SELECT realized_vol_20 FROM stock_features_daily WHERE ticker=:t AND date<=:d "
        "AND realized_vol_20 IS NOT NULL ORDER BY date DESC LIMIT 1"),
        {"t": ticker, "d": asof}).first()
    vol = float(vr[0]) if vr and vr[0] is not None else 25.0
    mv = max(1.5, min(12.0, vol * math.sqrt(5 / 252.0))) / 100.0
    if sig == "SELL":
        entry = round(close)
        target = round(max(sup, close * (1 - 0.9 * mv)))
        stop = round(min(res * 1.02, close * (1 + 0.6 * mv)))
    else:
        entry = round(min(close, sup * 1.03))
        target = round(min(res, entry * (1 + 0.9 * mv)))
        stop = round(max(sup * 0.97, entry * (1 - 0.6 * mv)))
    return {"signal": sig, "ref": round(close), "entry": entry, "target": target, "stop": stop}


def backfill_analysis(db, days: int = 45, horizon: int = 5) -> dict[str, Any]:
    """Log the Analysis method's signal for each of the last N trading dates, then grade."""
    ensure(db)
    dates = [r[0] for r in db.execute(text(
        "SELECT DISTINCT date FROM raw_daily_prices WHERE date <= (CURRENT_DATE - (:h+2)) "
        "ORDER BY date DESC LIMIT :n"), {"h": horizon, "n": days}).fetchall()]
    n = 0
    for d in dates:
        for tk in NAMES:
            s = _signal_asof(db, tk, d)
            if not s:
                continue
            db.execute(text(
                "INSERT INTO signal_log (logged_date, method, ticker, signal, ref_price, "
                "entry, target, stop, horizon) VALUES (:d,'analysis',:t,:s,:rp,:e,:tg,:st,:h) "
                "ON CONFLICT (logged_date, method, ticker) DO NOTHING"),
                {"d": d, "t": tk, "s": s["signal"], "rp": s["ref"], "e": s["entry"],
                 "tg": s["target"], "st": s["stop"], "h": horizon})
            n += 1
    db.commit()
    return {"ok": True, "dates": len(dates), "logged": n, **grade_matured(db)}


# --------------------------------------------------------------------------- #
# GRADE — walk the real price path for matured signals
# --------------------------------------------------------------------------- #
def _grade_one(db, row) -> Optional[dict]:
    path = db.execute(text(
        "SELECT date, high, low, close FROM raw_daily_prices WHERE ticker=:t "
        "AND date > :d ORDER BY date LIMIT :h"),
        {"t": row.ticker, "d": row.logged_date, "h": row.horizon}).fetchall()
    if len(path) < row.horizon:           # window not complete yet
        return None
    entry = float(row.entry or row.ref_price or 0)
    target, stop = float(row.target or 0), float(row.stop or 0)
    if not entry:
        return {"status": "graded", "outcome": "flat", "actual_ret": 0,
                "hit_target": False, "hit_stop": False}
    buy = row.signal == "BUY"
    hit_target = hit_stop = False
    for p in path:
        hi, lo = float(p.high), float(p.low)
        if buy:
            if hi >= target:
                hit_target = True; break
            if lo <= stop:
                hit_stop = True; break
        else:  # SELL — profit when price FALLS to target, stop is above
            if lo <= target:
                hit_target = True; break
            if hi >= stop:
                hit_stop = True; break
    last_close = float(path[-1].close)
    if hit_target:
        ret = abs(target - entry) / entry * 100; outcome = "win"
    elif hit_stop:
        ret = -abs(entry - stop) / entry * 100; outcome = "loss"
    else:
        ret = (last_close - entry) / entry * 100 * (1 if buy else -1); outcome = "flat"
    return {"status": "graded", "outcome": outcome, "actual_ret": round(ret, 2),
            "hit_target": hit_target, "hit_stop": hit_stop,
            "graded_date": path[-1].date}


def grade_matured(db) -> dict[str, Any]:
    ensure(db)
    rows = db.execute(text(
        "SELECT * FROM signal_log WHERE status='open' "
        "AND logged_date <= (CURRENT_DATE - (horizon + 2)) ORDER BY logged_date")).fetchall()
    graded = 0
    for r in rows:
        g = _grade_one(db, r)
        if not g:
            continue
        db.execute(text(
            "UPDATE signal_log SET status='graded', outcome=:o, actual_ret=:r, "
            "hit_target=:ht, hit_stop=:hs, graded_date=:gd WHERE id=:id"),
            {"o": g["outcome"], "r": g["actual_ret"], "ht": g["hit_target"],
             "hs": g["hit_stop"], "gd": g.get("graded_date"), "id": r.id})
        graded += 1
    db.commit()
    return {"ok": True, "graded": graded}


# --------------------------------------------------------------------------- #
# SCOREBOARD — what the UI reads
# --------------------------------------------------------------------------- #
def scoreboard(db) -> dict[str, Any]:
    ensure(db)
    stats = {}
    for m in ("ml", "analysis", "consensus"):
        r = db.execute(text(
            "SELECT count(*) n, "
            "  count(*) FILTER (WHERE outcome='win') wins, "
            "  count(*) FILTER (WHERE outcome='loss') losses, "
            "  count(*) FILTER (WHERE outcome='flat') flats, "
            "  avg(actual_ret) avg_ret, "
            "  count(*) FILTER (WHERE actual_ret > 0) profitable "
            "FROM signal_log WHERE method=:m AND status='graded'"), {"m": m}).first()
        n = r.n or 0
        stats[m] = {
            "graded": n,
            "win_rate": round((r.wins or 0) / n * 100, 1) if n else None,
            "profit_rate": round((r.profitable or 0) / n * 100, 1) if n else None,
            "avg_return": round(float(r.avg_ret), 2) if r.avg_ret is not None else None,
            "wins": r.wins or 0, "losses": r.losses or 0, "flats": r.flats or 0,
        }
    open_n = db.execute(text("SELECT count(*) FROM signal_log WHERE status='open'")).scalar()
    recent = [dict(ticker=r.ticker, name=NAMES.get(r.ticker, r.ticker), method=r.method,
                   signal=r.signal, outcome=r.outcome, actual_ret=float(r.actual_ret),
                   logged_date=str(r.logged_date))
              for r in db.execute(text(
                  "SELECT ticker, method, signal, outcome, actual_ret, logged_date "
                  "FROM signal_log WHERE status='graded' ORDER BY graded_date DESC, id DESC LIMIT 12"))]
    return {"by_method": stats, "open_signals": open_n, "recent": recent,
            "note": "체결 가정: 진입가 매수 후 목표(target) 먼저 도달=win, 손절 먼저=loss, 기간내 미달=flat."}
