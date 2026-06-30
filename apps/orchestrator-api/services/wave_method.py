"""wave_method.py — METHOD 3 ("Wave") served from the orchestrator (read-only).

The heavy compute (swing detection + 6-D score + Fibonacci) runs off-Render in
ml/features/wave_features.py and writes wave_features_daily. Here the orchestrator
just READS the latest row and applies the deterministic deep-pullback verdict — the
same rules as ml/models/wave_strategy.py — for the chatbot, Daily Trading, and the
consensus layer. Advisory only (entry/stop/target suggestions; no orders).
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import text

MIN_SCORE = 0.55
ENTRY_LO, ENTRY_HI = 0.50, 0.80
WATCH_LO = 0.30
MIN_RR = 1.5


def _price(db, ticker: str) -> Optional[float]:
    """Live price if available (Kiwoom in-market / Naver), else the last daily close."""
    try:
        from services.assistant_agent import _live_price_for_code
        from services.prediction_service import NAMES
        q = _live_price_for_code(ticker, NAMES.get(ticker))
        if q and q.get("price"):
            return float(q["price"])
    except Exception:
        pass
    r = db.execute(text("SELECT close FROM raw_daily_prices WHERE ticker=:t ORDER BY date DESC LIMIT 1"),
                   {"t": ticker}).fetchone()
    return float(r[0]) if r else None


def _mkt_ret1d(db) -> Optional[float]:
    """KODEX200 (069500) last 1-day return — for the index-plunge risk gate."""
    rows = db.execute(text("SELECT close FROM raw_daily_prices WHERE ticker='069500' "
                           "ORDER BY date DESC LIMIT 2")).fetchall()
    if len(rows) == 2 and rows[1][0]:
        return float(rows[0][0]) / float(rows[1][0]) - 1.0
    return None


def wave_for(db, ticker: str, price: Optional[float] = None) -> dict[str, Any]:
    """Method-3 verdict for one ticker, read from wave_features_daily."""
    ticker = str(ticker).zfill(6)
    r = db.execute(text(
        "SELECT date, wave_score, wave_amplitude, wave_duration, swing_low, swing_high, "
        "pullback_pct, fib_382, fib_500, fib_618, fib_750 "
        "FROM wave_features_daily WHERE ticker=:t ORDER BY date DESC LIMIT 1"),
        {"t": ticker}).fetchone()
    if not r:
        return {"method": "wave", "ticker": ticker, "verdict": "N/A",
                "reason": "아직 파동 데이터 없음 (유효한 상승 파동 미검출)"}
    as_of = r[0]
    score = float(r[1] or 0); ampl = float(r[2] or 0); dur = int(r[3] or 0)
    lo = float(r[4]); hi = float(r[5])
    f382, f500, f618, f750 = float(r[7]), float(r[8]), float(r[9]), float(r[10])
    px = price if price is not None else _price(db, ticker)
    rng = (hi - lo) or 1.0
    retrace = (hi - px) / rng if px else 0.0

    base = {"method": "wave", "ticker": ticker, "as_of": str(as_of),
            "wave_score": round(score, 3), "amplitude": round(float(ampl), 3),
            "duration": int(dur or 0), "swing_low": round(lo), "swing_high": round(hi),
            "price": round(px) if px else None, "retrace": round(retrace, 3),
            "max_hold_days": 20,        # risk gate #5: time-based cap
            "fib": {"0.382": f382, "0.5": f500, "0.618": f618, "0.75": f750}}

    # risk gate #1 (junk/penny): skip ultra-low-price names
    if px is not None and px < 1000:
        return {**base, "verdict": "AVOID", "confidence": "낮음", "reason": "저가/유동성 부적합 (junk filter)"}
    if score < MIN_SCORE:
        return {**base, "verdict": "AVOID", "confidence": "낮음",
                "reason": f"약한 추세 (파동점수 {score:.2f}) — 매매 부적합"}
    if px is None or retrace >= 1.0:
        return {**base, "verdict": "AVOID", "confidence": "낮음",
                "reason": "추세 저점 이탈 — 파동 무효화"}

    entry = float(f618); stop = round(lo * 0.98); target = round(hi)
    rr = round((target - entry) / (entry - stop), 2) if entry and entry > stop else None
    conf = "높음" if (score >= 0.75 and (rr or 0) >= 2) else ("보통" if score >= 0.62 else "낮음")
    levels = {"entry": round(entry), "deeper_entry": round(float(f750)), "stop": stop,
              "target": target, "rr": rr}

    levels["risk_per_share"] = round(entry - stop)     # gate #3 input: position sizing
    if ENTRY_LO <= retrace <= ENTRY_HI:
        if rr and rr >= MIN_RR:
            # risk gate #2: index-plunge halt — no new entries on a sharp market drop
            mret = _mkt_ret1d(db)
            if mret is not None and mret <= -0.02:
                return {**base, **levels, "verdict": "WATCH", "confidence": "낮음",
                        "risk_gate": "index_plunge_halt",
                        "reason": f"매수 신호이나 시장 급락(지수 {mret*100:.1f}%) → 신규 진입 보류"}
            return {**base, **levels, "verdict": "BUY", "confidence": conf,
                    "reason": (f"강한 파동(점수 {score:.2f}) + 깊은 눌림목(되돌림 {retrace*100:.0f}%, "
                               f"피보 0.618) → 매수 / 손절 {stop:,} / 목표 {target:,} (R:R {rr}) · 최대보유 20일")}
        return {**base, **levels, "verdict": "WATCH", "confidence": conf,
                "reason": f"강한 파동 + 눌림 구간이나 R:R {rr} 부족 → 관망"}
    if retrace < WATCH_LO:
        return {**base, **levels, "verdict": "WATCH", "confidence": conf,
                "reason": (f"강한 파동(점수 {score:.2f})이나 눌림 얕음(되돌림 {retrace*100:.0f}%) "
                           f"→ 매수 대기: 0.618={round(entry):,} / 0.75={round(float(f750)):,}")}
    return {**base, **levels, "verdict": "WATCH", "confidence": conf,
            "reason": f"매수구간 접근 중(되돌림 {retrace*100:.0f}%) — 0.618={round(entry):,} 도달 시 진입 검토"}
