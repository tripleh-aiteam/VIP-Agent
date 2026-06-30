"""wave_strategy.py — METHOD 3 ("Wave") verdict from wave_features_daily.

Turns the 6-D wave score + Fibonacci levels into an actionable, rule-based call:

  • Quality gate:  only a STRONG rally (wave_score ≥ MIN_SCORE) is tradable.
  • Entry trigger: price must be in the DEEP-pullback Fibonacci zone (retrace 0.5–0.80,
    Korea-optimized "deep retracement"); primary entry = 61.8%, deeper add = 75%.
  • Exit/R-R:      stop just below the swing-low (wave invalidation), target = the prior
    swing-high; only BUY when reward/risk ≥ MIN_RR.
  • Verdicts:      BUY (in zone) / WATCH (strong wave, waiting for the dip) /
                   AVOID (no strong wave, or the rally already broke).

Deterministic — no LLM, no subjective wave-counting. Returns a dict the chatbot /
Daily Trading / consensus layer can render. Reads the latest wave_features_daily row
+ the latest close (or a passed-in live price).

Usage:  python ml/models/wave_strategy.py [005930 000660 ...]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # ml/ on path
from _db import get_conn  # noqa: E402

MIN_SCORE = 0.55          # below this = no strong wave → AVOID
ENTRY_LO, ENTRY_HI = 0.50, 0.80   # deep-pullback retrace band that triggers BUY
WATCH_LO = 0.30          # strong wave but shallow pullback → WATCH (wait for the dip)
MIN_RR = 1.5             # require reward/risk ≥ 1.5 to call BUY


def _feat(db, ticker: str) -> dict | None:
    cur = db.cursor()
    cur.execute(
        "SELECT wave_score, wave_amplitude, wave_duration, swing_low, swing_high, "
        "pullback_pct, fib_382, fib_500, fib_618, fib_750 "
        "FROM wave_features_daily WHERE ticker=%s ORDER BY date DESC LIMIT 1", (ticker,))
    r = cur.fetchone()
    if not r:
        return None
    k = ["wave_score", "wave_amplitude", "wave_duration", "swing_low", "swing_high",
         "pullback_pct", "fib_382", "fib_500", "fib_618", "fib_750"]
    return dict(zip(k, [float(x) if x is not None else None for x in r]))


def _last_close(db, ticker: str) -> float | None:
    cur = db.cursor()
    cur.execute("SELECT close FROM raw_daily_prices WHERE ticker=%s ORDER BY date DESC LIMIT 1", (ticker,))
    r = cur.fetchone()
    return float(r[0]) if r else None


def wave_verdict(db, ticker: str, price: float | None = None) -> dict:
    """Method-3 verdict for one ticker."""
    f = _feat(db, ticker)
    if not f:
        return {"method": "wave", "ticker": ticker, "verdict": "N/A",
                "reason": "no wave data (rally not found yet)"}
    px = price if price is not None else _last_close(db, ticker)
    lo, hi = f["swing_low"], f["swing_high"]
    rng = (hi - lo) or 1.0
    retrace = (hi - px) / rng if px else 0.0          # 0 = at high, 1 = at swing-low
    score = f["wave_score"] or 0.0

    base = {"method": "wave", "ticker": ticker, "wave_score": round(score, 3),
            "amplitude": f["wave_amplitude"], "duration": int(f["wave_duration"] or 0),
            "swing_low": round(lo), "swing_high": round(hi), "price": round(px) if px else None,
            "retrace": round(retrace, 3),
            "fib": {"0.382": f["fib_382"], "0.5": f["fib_500"], "0.618": f["fib_618"], "0.75": f["fib_750"]}}

    if score < MIN_SCORE:
        return {**base, "verdict": "AVOID", "confidence": "낮음",
                "reason": f"약한 추세 (wave_score {score:.2f} < {MIN_SCORE}) — 매매 부적합"}
    if px is None or retrace >= 1.0:
        return {**base, "verdict": "AVOID", "confidence": "낮음",
                "reason": "추세 저점(swing-low) 이탈 — 파동 무효화"}

    entry = f["fib_618"]                              # primary deep-pullback entry
    deeper = f["fib_750"]
    stop = round(lo * 0.98)                           # below the rally start = invalidation
    target = round(hi)                               # retest the prior swing-high
    rr = round((target - entry) / (entry - stop), 2) if entry and entry > stop else None
    conf = "높음" if (score >= 0.75 and (rr or 0) >= 2) else ("보통" if score >= 0.62 else "낮음")

    if ENTRY_LO <= retrace <= ENTRY_HI:
        v = "BUY" if (rr and rr >= MIN_RR) else "WATCH"
        reason = (f"강한 파동(score {score:.2f}) + 깊은 눌림목(되돌림 {retrace*100:.0f}%, 피보 0.618 구간) "
                  f"→ 매수 / 손절 {stop:,} / 목표 {target:,} (R:R {rr})")
        if v == "WATCH":
            reason = f"강한 파동 + 눌림 구간이나 R:R {rr} 부족 → 관망"
        return {**base, "verdict": v, "confidence": conf, "entry": entry, "deeper_entry": deeper,
                "stop": stop, "target": target, "rr": rr, "reason": reason}
    if retrace < WATCH_LO:
        return {**base, "verdict": "WATCH", "confidence": conf, "entry": entry,
                "deeper_entry": deeper, "stop": stop, "target": target, "rr": rr,
                "reason": (f"강한 파동(score {score:.2f})이나 눌림 얕음(되돌림 {retrace*100:.0f}%) "
                           f"→ 매수 대기: 0.618={entry:,} / 0.75={deeper:,}")}
    return {**base, "verdict": "WATCH", "confidence": conf, "entry": entry,
            "deeper_entry": deeper, "stop": stop, "target": target, "rr": rr,
            "reason": (f"매수 구간 접근 중(되돌림 {retrace*100:.0f}%) — 0.618={entry:,} 도달 시 진입 검토")}


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    conn = get_conn()
    cur = conn.cursor()
    tickers = sys.argv[1:]
    if not tickers:
        cur.execute("SELECT ticker FROM wave_features_daily ORDER BY wave_score DESC LIMIT 12")
        tickers = [r[0] for r in cur.fetchall()]
    for tk in tickers:
        v = wave_verdict(conn, tk)
        print(f"{tk} {v['verdict']:6} conf={v.get('confidence','-')} "
              f"score={v.get('wave_score')} retrace={v.get('retrace')} rr={v.get('rr')} | {v['reason']}")
    conn.close()
