"""📈 Intraday-HIGH forecast (boss 2026-07-20): "prediction of today's highest
price and what time" was returning the CURRENT price (hallucination). This gives
a real, honest forecast — a likely high RANGE and the likely TIME-OF-DAY window —
built from 1 year of the stock's own intraday pattern + today's path so far.

Honest by construction: it's a statistical estimate labelled as such, never the
live quote card. Where the high lands overnight-of-US-driven days can't be known,
so we give a range and the historical hit-rate, not a fake exact number.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)
_cache: dict[str, tuple[float, dict]] = {}
_TTL = 12 * 3600


def _hist(db, code: str) -> Optional[dict[str, Any]]:
    """Per-day: hour-of-daily-high distribution + avg/percentile high-above-open,
    from minute_bars_hist. Cached 12h."""
    import time as _t
    hit = _cache.get(code)
    if hit and _t.time() - hit[0] < _TTL:
        return hit[1]
    hrs = db.execute(text(
        "WITH d AS (SELECT (ts AT TIME ZONE 'Asia/Seoul')::date AS day,"
        " extract(hour from (ts AT TIME ZONE 'Asia/Seoul')) AS hr, high,"
        " max(high) OVER (PARTITION BY (ts AT TIME ZONE 'Asia/Seoul')::date) AS dh"
        " FROM minute_bars_hist WHERE ticker=:c AND high IS NOT NULL)"
        " SELECT hr, count(*) FROM d WHERE high = dh GROUP BY hr ORDER BY count(*) DESC"),
        {"c": code}).fetchall()
    agg = db.execute(text(
        "WITH d AS (SELECT (ts AT TIME ZONE 'Asia/Seoul')::date AS day,"
        " first_value(open) OVER (PARTITION BY (ts AT TIME ZONE 'Asia/Seoul')::date ORDER BY ts) AS o,"
        " max(high) OVER (PARTITION BY (ts AT TIME ZONE 'Asia/Seoul')::date) AS dh"
        " FROM minute_bars_hist WHERE ticker=:c AND high IS NOT NULL)"
        " SELECT avg(dh/NULLIF(o,0)-1)*100, "
        "        percentile_cont(0.5) WITHIN GROUP (ORDER BY dh/NULLIF(o,0)-1)*100,"
        "        count(distinct day) FROM d"), {"c": code}).first()
    if not hrs or not agg or not agg[2]:
        return None
    total = sum(int(h[1]) for h in hrs)
    top = [(int(h[0]), round(int(h[1]) / total * 100)) for h in hrs[:3]]
    out = {"hours": top, "avg_pct": round(float(agg[0] or 0), 2),
           "med_pct": round(float(agg[1] or 0), 2), "days": int(agg[2])}
    _cache[code] = (_t.time(), out)
    return out


def _today(code: str) -> dict[str, Any]:
    o = h = c = None
    try:
        from services.kiwoom_rest import current_price, minute_bars
        cur = current_price(code) or {}
        c = cur.get("price")
        bars = minute_bars(code, tic="5", count=90) or []
        if bars:
            d0 = bars[-1]["ts"][:10]
            today = [b for b in bars if b["ts"][:10] == d0]
            if today:
                o = today[0].get("open")
                h = max((b.get("high") or 0) for b in today) or None
                c = c or today[-1].get("close")
    except Exception:
        pass
    return {"open": o, "high_so_far": h, "cur": c}


def _hour_window(hours: list) -> tuple[str, str]:
    """Turn the top high-hours into a readable KO/EN window phrase."""
    hs = sorted(h for h, _ in hours[:2])
    if not hs:
        return "장중", "during the session"
    if 9 in [h for h, _ in hours[:1]]:
        return "개장 직후 (09:00~10:00)", "right after the open (09:00-10:00)"
    if any(h >= 14 for h, _ in hours[:1]):
        return "마감 무렵 (14:00~15:30)", "near the close (14:00-15:30)"
    lo = min(hs)
    return f"{lo:02d}:00~{lo+1:02d}:00 무렵", f"around {lo:02d}:00-{lo+1:02d}:00"


def forecast(db, code: str, name: str, lang: str = "ko") -> Optional[str]:
    en = str(lang or "").lower().startswith("en")
    hi = _hist(db, code)
    if not hi:
        return None
    td = _today(code)
    o, hsf, cur = td["open"], td["high_so_far"], td["cur"]
    # predicted high = open lifted by the typical high-above-open, floored at today's high so far
    base = o or cur
    pred = None
    lo_pred = hipred = None
    if base:
        pred = base * (1 + hi["med_pct"] / 100)
        if hsf:
            pred = max(pred, hsf)
        lo_pred = base * (1 + min(hi["med_pct"], hi["avg_pct"]) / 100 * 0.6)
        hipred = base * (1 + max(hi["med_pct"], hi["avg_pct"]) / 100 * 1.2)
        if hsf:
            lo_pred = max(lo_pred, hsf)
            hipred = max(hipred, hsf * 1.002)
    win_ko, win_en = _hour_window(hi["hours"])
    top_hr, top_pct = hi["hours"][0]

    def w(v):
        try:
            return f"{int(round(v)):,}"
        except Exception:
            return "-"

    if en:
        L = [f"📈 Today's HIGH — forecast for {name} (a statistical estimate, not the current price):"]
        if cur:
            L.append(f"- Now ₩{w(cur)}" + (f" · today's high so far ₩{w(hsf)}" if hsf else "")
                     + (f" · opened ₩{w(o)}" if o else ""))
        if pred:
            L.append(f"- Likely daily high ≈ **₩{w(pred)}** (range ₩{w(lo_pred)}–₩{w(hipred)})")
        L.append(f"- Likely TIME: {win_en} — over the past {hi['days']} days the daily high "
                 f"landed in that hour {top_pct}% of the time")
        L.append(f"- Basis: this stock's high has averaged +{hi['avg_pct']}% above the open "
                 f"(median +{hi['med_pct']}%) across {hi['days']} days")
        L.append("⚠️ An estimate from history + today's path — tonight's US move and news can "
                 "push the real high outside this range. Not a guarantee.")
    else:
        L = [f"📈 오늘 최고가 — {name} 예측 (현재가가 아니라 통계적 추정치입니다):"]
        if cur:
            L.append(f"- 현재 ₩{w(cur)}" + (f" · 오늘 고가(지금까지) ₩{w(hsf)}" if hsf else "")
                     + (f" · 시가 ₩{w(o)}" if o else ""))
        if pred:
            L.append(f"- 예상 최고가 ≈ **₩{w(pred)}** (범위 ₩{w(lo_pred)}~₩{w(hipred)})")
        L.append(f"- 예상 시간대: {win_ko} — 지난 {hi['days']}일 중 이 시간대에 당일 고가가 "
                 f"나온 비율 {top_pct}%")
        L.append(f"- 근거: 이 종목의 고가는 시가 대비 평균 +{hi['avg_pct']}% "
                 f"(중앙값 +{hi['med_pct']}%), {hi['days']}일 기준")
        L.append("⚠️ 과거 패턴 + 오늘 흐름 기반 추정입니다 — 밤사이 미국장·뉴스로 실제 고가가 "
                 "이 범위를 벗어날 수 있어요. 보장이 아닙니다.")
    return "\n".join(L)


_PRED = ("예측", "전망", "예상", "얼마까지", "몇 시", "몇시", "언제", "predict", "forecast",
         "expect", "how high", "what time", "when will", "peak")
_HIGH = ("최고가", "고가", "highest", "high price", "day high", "peak", "얼마까지",
         "몇 시", "몇시", "언제", "what time", "when")


def is_high_forecast_question(text_in: str) -> bool:
    t = (text_in or "").lower()
    return any(k in t for k in _PRED) and any(k in t for k in _HIGH)
