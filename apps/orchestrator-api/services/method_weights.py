"""method_weights.py — M5.7: track-record-weighted fusion, from REAL graded history.

Replaces "every method counts equally" with weights earned from measured accuracy.
Validated on 857 real paired hourly forecasts (walk-forward replay, 2026-07-03):

    equal-weight fusion        53.0%
    track-record weighted      54.4%
    ML-only decisive           58.7%
    TIER (agree → ML-proven)   61.4%   ← shipped here
    both-methods-agree only    80.0%   (n=25 — small but strong)

Two horizons, because the methods flip rank:
  - INTRADAY (next ~1h, intraday_forecasts): ML 56% decisive vs Analysis 47% —
    ML leads; both are bad at 10:00 KST.
  - DAILY (multi-day, signal_log): Analysis 66% win vs ML 31% — Analysis leads.

All stats are recomputed from the DB (rolling window) and cached ~10 min, so the
weights keep learning as the graders mature. Small samples shrink toward 1.0
(never let 14 rows swing the fusion hard).
"""
from __future__ import annotations

import time
from typing import Any

from sqlalchemy import text

from services.logger import log

_cache: dict[str, tuple[float, Any]] = {}
_TTL = 600.0


def _cached(key: str, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    v = fn()
    _cache[key] = (time.time(), v)
    return v


def _shrunk_weight(acc_pct: float | None, n: int, target: float = 55.0,
                   k: int = 30, lo: float = 0.4, hi: float = 1.5) -> float:
    """acc → raw weight (acc/target), shrunk toward 1.0 by sample size (k pseudo-counts)."""
    if acc_pct is None or n <= 0:
        return 1.0
    raw = max(lo, min(hi, (acc_pct / target)))
    return round((n * raw + k * 1.0) / (n + k), 2)


def intraday_stats(db, days: int = 30) -> dict[str, Any]:
    """Rolling decisive direction-accuracy per method + agreement tier, from the hourly
    forward test. {'ml': {acc,n}, 'analysis': {acc,n}, 'agree': {acc,n}, 'bad_hours': [...]}"""
    def _build():
        out: dict[str, Any] = {"ml": None, "analysis": None, "agree": None, "bad_hours": []}
        try:
            rows = db.execute(text("""
                SELECT method, count(*) n,
                       avg(CASE WHEN dir_correct THEN 1.0 ELSE 0 END)*100 acc
                FROM intraday_forecasts
                WHERE status='graded' AND pred_dir IN ('UP','DOWN')
                  AND made_at > now() - (:d || ' days')::interval
                GROUP BY method"""), {"d": days}).fetchall()
            for r in rows:
                out[r.method] = {"acc": round(float(r.acc), 1), "n": int(r.n)}
            ag = db.execute(text("""
                SELECT count(*) n,
                       avg(CASE WHEN a.dir_correct THEN 1.0 ELSE 0 END)*100 acc
                FROM intraday_forecasts a
                JOIN intraday_forecasts b
                  ON a.ticker=b.ticker AND a.made_at=b.made_at
                 AND a.method='ml' AND b.method='analysis'
                WHERE a.status='graded' AND b.status='graded'
                  AND a.pred_dir=b.pred_dir AND a.pred_dir IN ('UP','DOWN')
                  AND a.made_at > now() - (:d || ' days')::interval"""), {"d": days}).first()
            if ag and ag.n:
                out["agree"] = {"acc": round(float(ag.acc), 1), "n": int(ag.n)}
            hrs = db.execute(text("""
                SELECT made_hour, count(*) n,
                       avg(CASE WHEN dir_correct THEN 1.0 ELSE 0 END)*100 acc
                FROM intraday_forecasts
                WHERE status='graded' AND pred_dir IN ('UP','DOWN')
                  AND made_at > now() - (:d || ' days')::interval
                GROUP BY made_hour"""), {"d": days}).fetchall()
            out["bad_hours"] = [int(r.made_hour) for r in hrs
                                if r.n >= 40 and float(r.acc) < 45.0]
        except Exception as e:
            log.warning(f"method_weights intraday: {str(e)[:100]}")
        return out
    return _cached(f"intraday:{days}", _build)


def daily_stats(db, days: int = 90) -> dict[str, Any]:
    """Per-method decisive win% + beat-market% from the day-based signal log."""
    def _build():
        out: dict[str, Any] = {}
        try:
            rows = db.execute(text("""
                SELECT method, count(*) n,
                       avg(CASE WHEN outcome='win' THEN 1.0 WHEN outcome='loss' THEN 0 END)*100 win,
                       avg(CASE WHEN beat_mkt THEN 1.0 ELSE 0 END)*100 beat
                FROM signal_log
                WHERE status='graded'
                  AND logged_date > (now() - (:d || ' days')::interval)::date
                GROUP BY method"""), {"d": days}).fetchall()
            for r in rows:
                eff = None
                if r.win is not None:
                    eff = (float(r.win) + float(r.beat or 0)) / 2   # blend: absolute + vs-market
                out[r.method] = {"win": round(float(r.win), 1) if r.win is not None else None,
                                 "beat": round(float(r.beat or 0), 1), "n": int(r.n),
                                 "eff": round(eff, 1) if eff is not None else None}
        except Exception as e:
            log.warning(f"method_weights daily: {str(e)[:100]}")
        return out
    return _cached(f"daily:{days}", _build)


def fusion_weights(db) -> dict[str, Any]:
    """Weights for decide()'s multi-day fusion + a transparency line (KO/EN).
    ML is weighted by its measured DAILY record; Wave stays 1.0 until it has one."""
    ds = daily_stats(db)
    ml = ds.get("ml") or {}
    an = ds.get("analysis") or {}
    w_ml = _shrunk_weight(ml.get("eff"), ml.get("n") or 0)
    w_an = _shrunk_weight(an.get("eff"), an.get("n") or 0)
    line_ko = ("실측 가중치: ML "
               + f"{w_ml}×" + (f" (일간 승률 {ml['win']}%·n={ml['n']})" if ml.get("win") is not None else " (데이터 부족)")
               + f" · 분석 {w_an}×" + (f" (승률 {an['win']}%·n={an['n']})" if an.get("win") is not None else ""))
    line_en = ("Measured weights: ML "
               + f"{w_ml}x" + (f" (daily win {ml['win']}%, n={ml['n']})" if ml.get("win") is not None else " (no data)")
               + f" · Analysis {w_an}x" + (f" (win {an['win']}%, n={an['n']})" if an.get("win") is not None else ""))
    return {"ml": w_ml, "analysis": w_an, "wave": 1.0,
            "line_ko": line_ko, "line_en": line_en, "daily": ds}


def intraday_tier(db, ticker: str) -> dict[str, Any] | None:
    """The replay-validated TIER read for ONE stock right now, from its latest hourly
    forecasts (same hour, both methods). Returns None when no fresh pair exists.
    tier: 'agree' (historic ~80%) | 'ml_solo' (~59%) | 'conflict' | 'no_signal'."""
    try:
        rows = db.execute(text("""
            SELECT DISTINCT ON (method) method, pred_dir, made_at
            FROM intraday_forecasts
            WHERE ticker=:t AND made_at > now() - interval '75 minutes'
            ORDER BY method, made_at DESC"""), {"t": str(ticker).zfill(6)}).fetchall()
        d = {r.method: r.pred_dir for r in rows}
        ml, an = d.get("ml"), d.get("analysis")
        if ml is None and an is None:
            return None
        st = intraday_stats(db)
        if ml in ("UP", "DOWN") and ml == an:
            tier = "agree"
        elif ml in ("UP", "DOWN") and an in ("UP", "DOWN"):
            tier = "conflict"
        elif ml in ("UP", "DOWN"):
            tier = "ml_solo"
        else:
            tier = "no_signal"
        return {"tier": tier, "ml": ml, "analysis": an,
                "agree_acc": (st.get("agree") or {}).get("acc"),
                "agree_n": (st.get("agree") or {}).get("n"),
                "ml_acc": (st.get("ml") or {}).get("acc"),
                "bad_hours": st.get("bad_hours") or []}
    except Exception as e:
        log.warning(f"intraday_tier: {str(e)[:100]}")
        return None
