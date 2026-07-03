"""self_tune.py — Autopilot B: nightly, bounded self-tuning of the dip-bounce strategy.

Every evening after close, replays the strategy path-accurately on the banked 5-minute
series (intraday_snapshot_history) over a bounded grid AROUND the current parameters,
and adopts the best cell only when the evidence is strong enough. Guardrails:
  - bounded steps (a parameter can move at most ±0.25pp per night, inside hard rails)
  - minimum sample (n ≥ 30 trades) and minimum win rate (≥ 55% net of costs)
  - every change is WRITTEN DOWN (strategy_params.note) and shown in the morning email
Parameters live in the DB (strategy_params) — dip_bounce/paper_trader read them at
runtime — so tuning never needs a deploy. Code is never self-modified; numbers only.
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from sqlalchemy import text

from services.logger import log

# hard rails — tuning may never leave these
RAILS = {"min_dip": (1.0, 3.0), "target_pct": (0.8, 2.0), "stop_pct": (0.5, 2.0)}
STEP = 0.25            # max move per night per param
MIN_TRADES = 30
MIN_WIN = 55.0
COST = 0.25

DEFAULTS = {"min_dip": 1.5, "target_pct": 1.2, "stop_pct": 1.0}

_DDL = """
CREATE TABLE IF NOT EXISTS strategy_params (
    strategy   TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      NUMERIC NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now(),
    note       TEXT,
    PRIMARY KEY (strategy, key)
)
"""

_cache: dict[str, tuple[float, dict]] = {}


def params_get(db, strategy: str = "dip_bounce") -> dict[str, float]:
    """Current tuned params (DB), defaults where unset. Cached ~5 min."""
    hit = _cache.get(strategy)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    out = dict(DEFAULTS)
    try:
        db.execute(text(_DDL))
        db.commit()
        for r in db.execute(text(
                "SELECT key, value FROM strategy_params WHERE strategy=:s"), {"s": strategy}).fetchall():
            if r.key in out:
                out[r.key] = float(r.value)
    except Exception as e:
        db.rollback()
        log.warning(f"params_get: {str(e)[:100]}")
    _cache[strategy] = (time.time(), out)
    return out


def _series(db, days: int = 14) -> dict[str, list]:
    rows = db.execute(text(
        "SELECT ticker, ts, price::float FROM intraday_snapshot_history "
        "WHERE price IS NOT NULL AND ts > now() - (:d || ' days')::interval "
        "ORDER BY ticker, ts"), {"d": days}).fetchall()
    s: dict[str, list] = defaultdict(list)
    for r in rows:
        s[r.ticker].append((r.ts, r.price))
    return s


def _replay(series: dict, min_dip: float, target: float, stop: float,
            hold_min: int = 60) -> tuple[int, float, float]:
    """Path-accurate replay → (n, win_pct, total_net_pct)."""
    trades = []
    for tk, pts in series.items():
        i = 0
        while i < len(pts):
            ts, px = pts[i]
            p1h = None
            for j in range(i - 1, -1, -1):
                dt = (ts - pts[j][0]).total_seconds() / 60
                if 50 <= dt <= 75:
                    p1h = pts[j][1]
                    break
                if dt > 75:
                    break
            if not p1h or (px - p1h) / p1h * 100 > -min_dip:
                i += 1
                continue
            entry, k, exit_px = px, i + 1, None
            tgt, stp = entry * (1 + target / 100), entry * (1 - stop / 100)
            while k < len(pts):
                t2, p2 = pts[k]
                if p2 >= tgt or p2 <= stp or (t2 - ts).total_seconds() / 60 >= hold_min:
                    exit_px = p2
                    break
                k += 1
            if exit_px is None:
                break
            trades.append((exit_px - entry) / entry * 100 - COST)
            i = k + 1
    n = len(trades)
    if not n:
        return 0, 0.0, 0.0
    wins = sum(1 for r in trades if r > 0)
    return n, wins / n * 100, sum(trades)


def run(db, force: bool = False) -> dict[str, Any]:
    """One tuning pass: grid around current params (±STEP), adopt the best cell that
    clears the sample/win-rate bars; write the note either way."""
    cur = params_get(db, "dip_bounce")
    series = _series(db)
    if not series:
        return {"tuned": False, "reason": "no 5-min history"}

    def grid(v, key):
        lo, hi = RAILS[key]
        return sorted({round(max(lo, min(hi, v + d)), 2) for d in (-STEP, 0.0, STEP)})

    base = _replay(series, cur["min_dip"], cur["target_pct"], cur["stop_pct"])
    best = (base[2], cur["min_dip"], cur["target_pct"], cur["stop_pct"], base[0], base[1])
    for d in grid(cur["min_dip"], "min_dip"):
        for t in grid(cur["target_pct"], "target_pct"):
            for s in grid(cur["stop_pct"], "stop_pct"):
                n, w, tot = _replay(series, d, t, s)
                if n >= MIN_TRADES and w >= MIN_WIN and tot > best[0]:
                    best = (tot, d, t, s, n, w)

    changed = (best[1], best[2], best[3]) != (cur["min_dip"], cur["target_pct"], cur["stop_pct"])
    note = (f"replay n={best[4]} win={best[5]:.1f}% total={best[0]:+.1f}% | "
            f"was dip={cur['min_dip']}/tgt={cur['target_pct']}/stop={cur['stop_pct']}"
            + (f" -> dip={best[1]}/tgt={best[2]}/stop={best[3]}" if changed else " (kept)"))
    if changed or force:
        try:
            db.execute(text(_DDL))
            for k, v in (("min_dip", best[1]), ("target_pct", best[2]), ("stop_pct", best[3])):
                db.execute(text(
                    "INSERT INTO strategy_params (strategy, key, value, note) "
                    "VALUES ('dip_bounce', :k, :v, :n) "
                    "ON CONFLICT (strategy, key) DO UPDATE SET value=:v, updated_at=now(), note=:n"),
                    {"k": k, "v": v, "n": note})
            db.commit()
            _cache.pop("dip_bounce", None)
        except Exception as e:
            db.rollback()
            log.warning(f"self_tune write: {str(e)[:120]}")
    log.info(f"self_tune: {note}", extra={"action": "self_tune.run"})
    return {"tuned": changed, "params": {"min_dip": best[1], "target_pct": best[2],
                                         "stop_pct": best[3]},
            "replay": {"n": best[4], "win_pct": round(best[5], 1),
                       "total_net_pct": round(best[0], 2)},
            "note": note}


def latest_note(db) -> str | None:
    try:
        r = db.execute(text(
            "SELECT note, updated_at FROM strategy_params WHERE strategy='dip_bounce' "
            "ORDER BY updated_at DESC LIMIT 1")).first()
        return f"{r.note} ({str(r.updated_at)[:16]})" if r else None
    except Exception:
        return None
