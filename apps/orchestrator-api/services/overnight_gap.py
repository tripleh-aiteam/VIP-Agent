"""🌙 Overnight hold-or-sell advisor (boss 2026-07-16).

"It's 15:00, market closes in 20 minutes — hold overnight or sell?"

HONEST DESIGN — measured statistics, not ML. The backtest verdict on a full
year of 5-min bars (9,870 stock-nights, 39 stocks) was:
  · ML (logreg/GBM) finds NO edge over the base rate; its SELL calls were
    only 33-38% correct and LOSE money vs always-holding.
  · EVERY condition observable at 15:00 (big down day, weak close, big up
    day, strong close...) had a POSITIVE average overnight gap this year
    (+0.28% .. +0.80%); down days actually open UP more often (62-65% —
    mean reversion). Selling at close + rebuying costs 0.23% round trip.
So the advisor reports the MEASURED odds for today's condition slice, the
per-stock overnight record, the fee math, and a verdict — and every call is
logged and auto-graded against the real open next morning, so its true hit
rate accumulates like all our other forward tests.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import text

logger = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")
FEE = 0.23          # round-trip cost of sell-now + rebuy-tomorrow (%)

# in-process caches — the history stats barely change intraday
_stats_cache: dict[str, tuple[float, dict]] = {}   # key -> (ts, stats)
_STATS_TTL = 6 * 3600


def _ensure(db) -> None:
    db.execute(text(
        "CREATE TABLE IF NOT EXISTS overnight_calls ("
        " id SERIAL PRIMARY KEY, ticker TEXT, name TEXT, verdict TEXT,"
        " up_odds DOUBLE PRECISION, avg_gap DOUBLE PRECISION,"
        " close_px DOUBLE PRECISION, cond TEXT,"
        " called_at TIMESTAMPTZ DEFAULT now(),"
        " graded BOOLEAN NOT NULL DEFAULT FALSE, open_px DOUBLE PRECISION,"
        " gap_pct DOUBLE PRECISION, correct BOOLEAN)"))
    db.commit()


def _slice_key(day_chg: Optional[float], pos: Optional[float]) -> tuple[str, str, str]:
    """(slice key, KO label, EN label) for today's shape."""
    if day_chg is None or pos is None:
        return "all", "전체", "all nights"
    d = "up" if day_chg >= 1.0 else "down" if day_chg <= -1.0 else "flat"
    p = "weak" if pos < 0.33 else "strong" if pos > 0.67 else "mid"
    ko = {"up": "상승일", "down": "하락일", "flat": "보합일"}[d] + " · " + \
         {"weak": "저가권 마감", "strong": "고가권 마감", "mid": "중간 마감"}[p]
    en = {"up": "up day", "down": "down day", "flat": "flat day"}[d] + " · " + \
         {"weak": "weak close", "strong": "strong close", "mid": "mid-range close"}[p]
    return f"{d}:{p}", ko, en


def _hist_stats(db, ticker: str) -> dict[str, Any]:
    """Per-stock overnight record + pooled per-slice stats from the 1yr archive.
    One SQL day-aggregation, cached 6h."""
    import time as _t
    hit = _stats_cache.get(ticker)
    if hit and _t.time() - hit[0] < _STATS_TTL:
        return hit[1]
    rows = db.execute(text(
        "WITH day AS ("
        "  SELECT ticker, (ts AT TIME ZONE 'Asia/Seoul')::date AS d,"
        "         (array_agg(open ORDER BY ts))[1] AS o,"
        "         (array_agg(close ORDER BY ts DESC))[1] AS c,"
        "         max(high) AS h, min(low) AS lo"
        "  FROM minute_bars_hist WHERE close IS NOT NULL"
        "  GROUP BY ticker, (ts AT TIME ZONE 'Asia/Seoul')::date"
        "), lag AS ("
        "  SELECT ticker, d, c, h, lo,"
        "         lag(c) OVER w AS prev_c,"
        "         lead(o) OVER w AS next_o"
        "  FROM day WINDOW w AS (PARTITION BY ticker ORDER BY d)"
        ") SELECT ticker,"
        "         CASE WHEN c/prev_c-1 >= 0.01 THEN 'up'"
        "              WHEN c/prev_c-1 <= -0.01 THEN 'down' ELSE 'flat' END,"
        "         CASE WHEN h > lo AND (c-lo)/(h-lo) < 0.33 THEN 'weak'"
        "              WHEN h > lo AND (c-lo)/(h-lo) > 0.67 THEN 'strong' ELSE 'mid' END,"
        "         count(*),"
        "         avg(CASE WHEN next_o > c THEN 1.0 ELSE 0.0 END),"
        "         avg(next_o/c - 1) * 100"
        "  FROM lag WHERE prev_c IS NOT NULL AND next_o IS NOT NULL AND prev_c > 0 AND c > 0"
        "  GROUP BY GROUPING SETS ((ticker), (2, 3), ())")).fetchall()
    stats: dict[str, Any] = {"per_stock": {}, "slices": {}, "all": None}
    for tk, d, p, n, up, gap in rows:
        rec = {"n": int(n), "up": round(float(up) * 100, 1), "gap": round(float(gap), 3)}
        if tk is not None:
            stats["per_stock"][tk] = rec
        elif d is not None:
            stats["slices"][f"{d}:{p}"] = rec
        else:
            stats["all"] = rec
    _stats_cache[ticker] = (_t.time(), stats)
    return stats


def _today_shape(ticker: str) -> dict[str, Any]:
    """Today's condition, live from Kiwoom: day change %, close position in range,
    latest price. Works during and after market (last session's bars)."""
    out: dict[str, Any] = {"px": None, "day_chg": None, "pos": None}
    try:
        from services.kiwoom_rest import current_price, minute_bars
        cur = current_price(ticker) or {}
        out["px"] = cur.get("price")
        prev = cur.get("prev_close")
        bars = minute_bars(ticker, tic="5", count=90) or []
        if bars:
            last_day = bars[-1]["ts"][:10]
            today = [b for b in bars if b["ts"][:10] == last_day]
            hi = max(b["high"] for b in today if b.get("high"))
            lo = min(b["low"] for b in today if b.get("low"))
            c = today[-1]["close"]
            out["px"] = out["px"] or c
            if hi and lo and hi > lo:
                out["pos"] = round((c - lo) / (hi - lo), 2)
            if prev:
                out["day_chg"] = round((c / float(prev) - 1) * 100, 2)
        elif out["px"] and cur.get("change_pct") is not None:
            out["day_chg"] = float(cur["change_pct"])
    except Exception as e:
        logger.info("overnight shape %s: %s", ticker, str(e)[:80])
    return out


def _track_record(db) -> Optional[dict[str, Any]]:
    r = db.execute(text(
        "SELECT count(*), sum(CASE WHEN correct THEN 1 ELSE 0 END) "
        "FROM overnight_calls WHERE graded")).first()
    if not r or not r[0]:
        return None
    return {"n": int(r[0]), "hit": round(int(r[1] or 0) / int(r[0]) * 100)}


def advise(db, ticker: str, name: str, lang: str = "ko") -> Optional[str]:
    """The chatbot answer: verdict + measured odds + fee math, KO or EN.
    Logs the call for next-morning grading."""
    _ensure(db)
    en = str(lang or "").lower().startswith("en")
    shape = _today_shape(ticker)
    stats = _hist_stats(db, ticker)
    key, cond_ko, cond_en = _slice_key(shape.get("day_chg"), shape.get("pos"))
    sl = stats["slices"].get(key) or stats.get("all")
    mine = stats["per_stock"].get(str(ticker).zfill(6))
    if not sl:
        return None

    # verdict from the MEASURED slice (net of the rebuy fee)
    edge = sl["gap"]          # expected overnight gap in this condition
    if edge <= -FEE:
        verdict = "SELL"
    elif edge >= 0.05:
        verdict = "HOLD"
    else:
        verdict = "NEUTRAL"

    try:
        db.execute(text(
            "INSERT INTO overnight_calls (ticker, name, verdict, up_odds, avg_gap, "
            "close_px, cond) VALUES (:t,:n,:v,:u,:g,:p,:c)"),
            {"t": str(ticker).zfill(6), "n": name, "v": verdict, "u": sl["up"],
             "g": edge, "p": shape.get("px"), "c": key})
        db.commit()
    except Exception:
        db.rollback()

    tr = _track_record(db)
    chg = shape.get("day_chg")
    pos = shape.get("pos")
    L: list[str] = []
    if en:
        L.append(f"🌙 Overnight call — {name}")
        if chg is not None:
            postxt = ("near the day's high" if (pos or 0) > 0.67
                      else "near the day's low" if (pos or 1) < 0.33 else "mid-range")
            L.append(f"- Today: {chg:+.2f}%, closing {postxt} ({cond_en})")
        L.append(f"- Measured over the past year, nights like this opened UP {sl['up']}% "
                 f"of the time, average gap {sl['gap']:+.2f}% (n={sl['n']:,} stock-nights)")
        if mine and mine["n"] >= 40:
            L.append(f"- {name} itself: overnight average {mine['gap']:+.2f}%, "
                     f"up-open {mine['up']}% (n={mine['n']})")
        L.append(f"- Fee math: selling now and rebuying tomorrow costs a fixed −{FEE}%")
        if verdict == "HOLD":
            L.append(f"✅ Verdict: HOLD — the measured odds favored holding "
                     f"({sl['gap']:+.2f}% avg vs a −{FEE}% certain fee). A statistical lean, not a guarantee.")
        elif verdict == "SELL":
            L.append(f"🔴 Verdict: SELL before close — in this condition the average "
                     f"gap ({sl['gap']:+.2f}%) was worse than the −{FEE}% rebuy fee.")
        else:
            L.append(f"⚖️ Verdict: NO CLEAR EDGE — expected gap ≈ 0 after the −{FEE}% fee; "
                     "either choice is defensible. Position size should decide.")
        L.append("⚠️ Tonight's US session is the biggest driver and nobody can see it at 15:00 — "
                 "if a US drop worries you, selling PART is the honest middle ground.")
        L.append("📊 This call is auto-graded against tomorrow's real open"
                 + (f" — record so far: {tr['hit']}% correct over {tr['n']} calls" if tr else
                    " — the track record starts accumulating from today") + ".")
    else:
        L.append(f"🌙 오버나잇 판단 — {name}")
        if chg is not None:
            postxt = ("고가권 마감" if (pos or 0) > 0.67
                      else "저가권 마감" if (pos or 1) < 0.33 else "중간권 마감")
            L.append(f"- 오늘: {chg:+.2f}%, {postxt} ({cond_ko})")
        L.append(f"- 지난 1년 측정: 이런 날 다음날 {sl['up']}% 확률로 상승 출발, "
                 f"평균 갭 {sl['gap']:+.2f}% (표본 {sl['n']:,}종목-밤)")
        if mine and mine["n"] >= 40:
            L.append(f"- {name} 자체 기록: 하룻밤 평균 {mine['gap']:+.2f}%, "
                     f"상승 출발 {mine['up']}% (n={mine['n']})")
        L.append(f"- 수수료 계산: 지금 팔고 내일 다시 사면 −{FEE}% 확정 비용")
        if verdict == "HOLD":
            L.append(f"✅ 판단: 들고 가세요 — 측정된 기대값(평균 {sl['gap']:+.2f}%)이 "
                     f"확정 수수료(−{FEE}%)보다 유리했습니다. 통계적 우위이지 보장이 아닙니다.")
        elif verdict == "SELL":
            L.append(f"🔴 판단: 장 마감 전 매도 — 이 조건의 평균 갭({sl['gap']:+.2f}%)이 "
                     f"재매수 수수료(−{FEE}%)보다 나빴습니다.")
        else:
            L.append(f"⚖️ 판단: 뚜렷한 우위 없음 — 수수료(−{FEE}%)를 빼면 기대값이 0 근처입니다. "
                     "포지션 크기로 결정하세요.")
        L.append("⚠️ 오늘 밤 미국장이 가장 큰 변수인데 15:00엔 아무도 미리 볼 수 없습니다 — "
                 "미국 하락이 걱정되면 일부만 파는 것이 정직한 절충입니다.")
        L.append("📊 이 판단은 내일 아침 실제 시가로 자동 채점됩니다"
                 + (f" — 지금까지 기록: {tr['n']}회 중 {tr['hit']}% 적중" if tr else
                    " — 오늘부터 기록이 쌓입니다") + ".")
    return "\n".join(L)


def grade_calls(db) -> dict[str, Any]:
    """Morning job (09:06 KST): grade yesterday's calls against today's REAL open.
    HOLD is correct when the gap beat the −0.23% fee alternative; SELL is
    correct when the gap was worse than −0.23%; NEUTRAL grades as gap>0."""
    _ensure(db)
    rows = db.execute(text(
        "SELECT id, ticker, verdict, close_px FROM overnight_calls "
        "WHERE NOT graded AND close_px IS NOT NULL "
        "AND called_at < (now() AT TIME ZONE 'Asia/Seoul')::date")).fetchall()
    graded = 0
    from services.kiwoom_rest import minute_bars
    opens: dict[str, Optional[float]] = {}
    for oid, tk, verdict, close_px in rows:
        if tk not in opens:
            try:
                bars = minute_bars(tk, tic="5", count=80) or []
                today = datetime.now(KST).strftime("%Y-%m-%d")
                tb = [b for b in bars if b["ts"][:10] == today]
                opens[tk] = float(tb[0]["open"]) if tb and tb[0].get("open") else None
            except Exception:
                opens[tk] = None
        o = opens.get(tk)
        if not o or not close_px:
            continue
        gap = (o / float(close_px) - 1) * 100
        correct = (gap > -FEE) if verdict == "HOLD" else \
                  (gap < -FEE) if verdict == "SELL" else (gap > 0)
        db.execute(text(
            "UPDATE overnight_calls SET graded=TRUE, open_px=:o, gap_pct=:g, "
            "correct=:c WHERE id=:i"), {"o": o, "g": round(gap, 3), "c": correct, "i": oid})
        graded += 1
    db.commit()
    return {"graded": graded}


# ---- intent detection (shared by both bots via the orchestrator) ------------- #
import re as _re

# NB (2026-08-03): a bare 'hold or sell' / 'sell or hold' used to match here. That
# stole every plain position question in ENGLISH — "I hold SK Hynix, should I sell or
# hold?" got the overnight-gap card, while the Korean twin ("...팔까 말까?") fell through
# to the full position_advice engine. Same question, two different answers depending on
# language. Those two phrasings carry NO overnight meaning on their own, so they're gone;
# a real overnight ask still matches via 'overnight' / 'till tomorrow' / '들고 가' / etc.
_OVERNIGHT_RE = _re.compile(
    r"(들고\s?가|들고\s?갈|가져\s?가|가지고\s?가|오버나잇|밤새|내일까지|팔고\s?가|팔고\s?갈|"
    r"장\s?마감\s?전에\s?팔|마감\s?전에\s?팔|종가에\s?팔|내일\s?갭|홀딩할까|홀드할까|"
    r"hold .{0,40}overnight|overnight|(hold|keep|carry) .{0,40}(till|until|to) tomorrow|"
    r"sell (before|at) (the )?close)",
    _re.IGNORECASE,
)


def is_overnight_question(text_in: str) -> bool:
    return bool(_OVERNIGHT_RE.search(text_in or ""))
