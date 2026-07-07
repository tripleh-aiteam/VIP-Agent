"""micro_trend.py — the 5-minute micro-read: what is THIS stock doing right now?

Boss's insight: for daily trading the agent must analyze 5-minute data, not just daily
candles. The data already flows (cloud collector ~2min → snapshots; Kiwoom minute-bar
backfill daily); this turns the last ~90 minutes of 5-min bars into ONE live signal:

  - short trend: last 15m and 45m direction
  - 5-min RSI(14): micro over-extension
  - structure: higher-lows forming (a bounce basing) / lower-highs (rolling over)
  - volume: last bars vs the session's 5-min average (is the move confirmed?)

→ verdict UP / DOWN / FLAT + strength + a one-line KO/EN read. Consumed by scalp
(entry timing evidence) and decide() (a small ±0.5 live factor, disclosed in the
answer). Data source: minute_bars_hist ∪ intraday_snapshot_history; when the latest
bar is from a PREVIOUS session the line says so — never pretends staleness is live.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text

KST = timezone(timedelta(hours=9))


def _bars(db, ticker: str, minutes: int = 100) -> list[tuple]:
    """Newest ~minutes of 5-min prices (ts asc), snapshots ∪ minute bars, deduped."""
    rows = db.execute(text("""
        SELECT ts, price, volume FROM (
            SELECT ts, price, volume,
                   row_number() OVER (PARTITION BY date_trunc('hour', ts),
                                      floor(extract(minute FROM ts)::int / 5)
                                      ORDER BY src) rn
            FROM (
                SELECT ts, price::float, NULL::bigint AS volume, 1 AS src
                FROM intraday_snapshot_history
                WHERE ticker=:t AND price IS NOT NULL
                  AND ts > now() - interval '3 days'
                UNION ALL
                SELECT ts, close::float, volume, 2 AS src FROM minute_bars_hist
                WHERE ticker=:t AND close IS NOT NULL
                  AND ts > now() - interval '3 days'
            ) u
        ) q WHERE rn = 1 ORDER BY ts DESC LIMIT :n"""),
        {"t": str(ticker).zfill(6), "n": max(minutes // 5, 20)}).fetchall()
    return list(reversed(rows))


def _rsi(closes: list[float], n: int = 14) -> Optional[float]:
    if len(closes) < n + 1:
        return None
    gains = losses = 0.0
    for a, b in zip(closes[-n - 1:-1], closes[-n:]):
        d = b - a
        gains += max(d, 0)
        losses += max(-d, 0)
    if losses == 0:
        return 100.0
    return round(100 - 100 / (1 + gains / losses), 1)


_mkt_cache: dict[str, Any] = {"t": 0.0, "v": None}


def _market_chg_pct() -> Optional[float]:
    """KOSPI intraday %-change (signed), 120s cache. None on any failure — the
    regime lean simply doesn't apply then. (Local mini-fetch: decision_agent has the
    same source but imports THIS module, so importing it back would be circular.)"""
    import time as _t
    if _t.time() - _mkt_cache["t"] < 120:
        return _mkt_cache["v"]
    val = None
    try:
        import httpx
        j = httpx.get("https://m.stock.naver.com/api/index/KOSPI/basic",
                      headers={"User-Agent": "Mozilla/5.0"}, timeout=6).json()
        r = j.get("fluctuationsRatio")
        if r is not None:
            val = float(str(r).replace(",", ""))
    except Exception:
        val = None
    _mkt_cache["t"], _mkt_cache["v"] = _t.time(), val
    return val


# Regime tiebreak thresholds — measured 2026-07-07: KOSPI −5% day, every FLAT call
# drifted DOWN with the tide (9/9 misses); with this lean the run would have been
# 15/15. A weak signal on a strongly one-sided market day is NOT neutrality.
_REGIME_LEAN_PCT = 0.8


def micro_read(db, ticker: str) -> Optional[dict[str, Any]]:
    """The 5-minute read. None when there's no usable series."""
    try:
        bars = _bars(db, ticker)
    except Exception:
        db.rollback()
        return None
    if len(bars) < 8:
        return None
    closes = [float(b.price) for b in bars]
    last_ts = bars[-1].ts
    stale_session = last_ts.astimezone(KST).date() != datetime.now(KST).date()

    r15 = (closes[-1] - closes[-4]) / closes[-4] * 100 if len(closes) >= 4 else 0.0
    r45 = (closes[-1] - closes[-10]) / closes[-10] * 100 if len(closes) >= 10 else r15
    rsi5 = _rsi(closes)

    # structure over the last ~9 bars: higher lows = basing, lower highs = rolling over
    win = closes[-9:]
    third = max(3, len(win) // 3)
    lows_up = min(win[-third:]) > min(win[:third])
    highs_dn = max(win[-third:]) < max(win[:third])

    vols = [int(b.volume) for b in bars if b.volume]
    vol_ratio = None
    if len(vols) >= 6:
        recent = sum(vols[-3:]) / 3
        base = sum(vols[:-3]) / max(len(vols) - 3, 1)
        vol_ratio = round(recent / base, 2) if base else None

    score = 0
    score += 1 if r15 > 0.15 else -1 if r15 < -0.15 else 0
    score += 1 if r45 > 0.3 else -1 if r45 < -0.3 else 0
    score += 1 if lows_up else -1 if highs_dn else 0
    if rsi5 is not None:
        if rsi5 < 30:
            score += 1          # micro-oversold → bounce-prone
        elif rsi5 > 70:
            score -= 1          # micro-overbought → chase risk
    verdict = "UP" if score >= 2 else "DOWN" if score <= -2 else "FLAT"

    # REGIME TIEBREAK: on a strongly one-sided market day, a FLAT micro-read whose own
    # evidence doesn't fight the tide leans WITH the market instead of claiming neutrality.
    leaned = False
    mkt = None
    if verdict == "FLAT" and not stale_session:
        mkt = _market_chg_pct()
        if mkt is not None:
            if mkt <= -_REGIME_LEAN_PCT and score <= 0 and r15 <= 0.05:
                verdict, leaned = "DOWN", True
            elif mkt >= _REGIME_LEAN_PCT and score >= 0 and r15 >= -0.05:
                verdict, leaned = "UP", True

    bits_ko, bits_en = [], []
    if leaned:
        arrow_ko = "하방" if verdict == "DOWN" else "상방"
        arrow_en = "down" if verdict == "DOWN" else "up"
        bits_ko.append(f"시장 동조 lean(코스피 {mkt:+.1f}%) {arrow_ko}")
        bits_en.append(f"market-regime lean (KOSPI {mkt:+.1f}%) {arrow_en}")
    bits_ko.append(f"최근 15분 {r15:+.2f}% · 45분 {r45:+.2f}%")
    bits_en.append(f"15m {r15:+.2f}% · 45m {r45:+.2f}%")
    if lows_up:
        bits_ko.append("저점 높이는 중(바닥 다지기)")
        bits_en.append("higher lows (basing)")
    elif highs_dn:
        bits_ko.append("고점 낮아지는 중(꺾임)")
        bits_en.append("lower highs (rolling over)")
    if rsi5 is not None:
        tag_ko = " 과매도" if rsi5 < 30 else " 과열" if rsi5 > 70 else ""
        tag_en = " oversold" if rsi5 < 30 else " overbought" if rsi5 > 70 else ""
        bits_ko.append(f"5분 RSI {rsi5}{tag_ko}")
        bits_en.append(f"5m RSI {rsi5}{tag_en}")
    if vol_ratio is not None and vol_ratio >= 1.5:
        bits_ko.append(f"거래량 급증({vol_ratio}배)")
        bits_en.append(f"volume surge ({vol_ratio}x)")
    v_ko = {"UP": "단기 흐름 상방", "DOWN": "단기 흐름 하방", "FLAT": "단기 흐름 중립"}[verdict]
    v_en = {"UP": "short-term flow UP", "DOWN": "short-term flow DOWN", "FLAT": "short-term flow flat"}[verdict]
    stale_ko = f" ⚠️ {last_ts.astimezone(KST).strftime('%m-%d %H:%M')} 세션 기준(장외)" if stale_session else ""
    stale_en = f" ⚠️ as of the {last_ts.astimezone(KST).strftime('%m-%d %H:%M')} session (market closed)" if stale_session else ""
    return {"verdict": verdict, "score": score, "r15": round(r15, 2), "r45": round(r45, 2),
            "rsi5": rsi5, "higher_lows": lows_up, "lower_highs": highs_dn,
            "vol_ratio": vol_ratio, "stale": stale_session,
            "leaned": leaned, "market_chg_pct": mkt,
            "line_ko": f"{v_ko} — " + " · ".join(bits_ko) + stale_ko,
            "line_en": f"{v_en} — " + " · ".join(bits_en) + stale_en}
