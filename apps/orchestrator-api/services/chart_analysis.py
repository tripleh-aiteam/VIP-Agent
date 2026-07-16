"""chart_analysis.py — DEEP graph reading for the chatbots (boss 2026-07-16).

"It must answer using the Kiwoom GRAPH — 1-minute, 5-minute, historical — to
see and analyze the movement. Whenever a question relates to the graph, part
of the answer comes from the graph. Recommendations too."

One call = a multi-timeframe read composed from real bars:
  · DAILY  (Naver 120d): MA5/20/60 alignment & trend, swing support/resistance,
    win/loss streak, 20-day range position, gap vs prev close, ATR volatility
  · 5-MIN  (Kiwoom ka10080, fallback collected bars): today's shape, close
    quality (position in the day's range), candle body vs wicks, volume tilt
  · 1-MIN  (Kiwoom ka10080 tic=1): last-hour micro structure & momentum
  · ANALOG (pattern_layer): what similar past movements did next

Returns numbers + a bilingual opinion block ready to append to any answer.
Every timeframe fails open — whatever data exists gets read.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("vip.chart")
KST = ZoneInfo("Asia/Seoul")


def _f(n) -> str:
    try:
        return f"{float(n):,.0f}"
    except Exception:
        return "-"


# ---- daily / historical ----------------------------------------------------- #
def _daily_read(code: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        from services.naver_stock import daily_history
        rows = daily_history(code, days=120)          # newest-first
        if len(rows) < 25:
            return out
        closes = [float(r["close"]) for r in rows][::-1]   # oldest→newest
        highs = [float(r.get("high") or r["close"]) for r in rows][::-1]
        lows = [float(r.get("low") or r["close"]) for r in rows][::-1]
        # sanitize corrupted bars (Naver occasionally returns absurd highs/lows):
        # clamp any high/low further than 35% from that day's close to the close
        for k in range(len(closes)):
            if not (0.65 * closes[k] <= highs[k] <= 1.35 * closes[k]):
                highs[k] = closes[k]
            if not (0.65 * closes[k] <= lows[k] <= 1.35 * closes[k]):
                lows[k] = closes[k]
        last = closes[-1]
        ma = {n: sum(closes[-n:]) / n for n in (5, 20, 60) if len(closes) >= n}
        out["ma"] = {k: round(v) for k, v in ma.items()}
        out["above"] = {k: last > v for k, v in ma.items()}
        if len(ma) == 3:
            out["alignment"] = ("정배열" if ma[5] > ma[20] > ma[60]
                                else "역배열" if ma[5] < ma[20] < ma[60] else "혼조")
        # streak of daily direction
        streak = 0
        for i in range(len(closes) - 1, 0, -1):
            d = closes[i] - closes[i - 1]
            if streak == 0:
                streak = 1 if d > 0 else -1 if d < 0 else 0
            elif (d > 0) != (streak > 0) or d == 0:
                break
            else:
                streak += 1 if streak > 0 else -1
        out["streak"] = streak
        # 20-day range position
        h20, l20 = max(highs[-20:]), min(lows[-20:])
        if h20 > l20:
            out["pos20"] = round((last - l20) / (h20 - l20) * 100)
        # swing support/resistance from the last 20 sessions (excluding today) —
        # near enough to matter for the next days' trading
        out["support"] = round(min(lows[-20:-1])) if len(lows) > 21 else round(min(lows[:-1]))
        out["resistance"] = round(max(highs[-20:-1])) if len(highs) > 21 else round(max(highs[:-1]))
        # ATR-ish volatility (14d, % of price)
        trs = [max(highs[i], closes[i - 1]) - min(lows[i], closes[i - 1])
               for i in range(len(closes) - 14, len(closes))]
        out["atr_pct"] = round(sum(trs) / 14 / last * 100, 2)
        out["last"] = round(last)
        out["prev_close"] = round(closes[-2])
    except Exception as e:
        logger.warning("daily read %s: %s", code, str(e)[:80])
    return out


# ---- intraday 5-min ---------------------------------------------------------- #
def _bars_5m(db, code: str) -> list[dict]:
    try:
        from services import kiwoom_rest as kr
        b = kr.minute_bars(code, tic="5", count=120) or []
        if b:
            return b
    except Exception:
        pass
    try:
        from services.cycle_scalp import _bars
        return [{"ts": r["ts"].strftime("%Y-%m-%d %H:%M") if hasattr(r["ts"], "strftime") else str(r["ts"]),
                 "open": r["open"], "high": r["high"], "low": r["low"],
                 "close": r["close"], "volume": r.get("volume") or 0}
                for r in _bars(db, code, limit=120)]
    except Exception:
        return []


def _intraday_read(db, code: str, prev_close: Optional[float]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        bars = _bars_5m(db, code)
        today = datetime.now(KST).strftime("%Y-%m-%d")
        tb = [b for b in bars if str(b.get("ts", "")).startswith(today)]
        if len(tb) < 3:
            return out
        opens = float(tb[0]["open"] or tb[0]["close"])
        closes = [float(b["close"]) for b in tb]
        highs = [float(b.get("high") or b["close"]) for b in tb]
        lows = [float(b.get("low") or b["close"]) for b in tb]
        last, hi, lo = closes[-1], max(highs), min(lows)
        out["open"], out["high"], out["low"], out["last"] = round(opens), round(hi), round(lo), round(last)
        if prev_close:
            out["gap_pct"] = round((opens / prev_close - 1) * 100, 2)
            out["chg_pct"] = round((last / prev_close - 1) * 100, 2)
        out["vs_open_pct"] = round((last / opens - 1) * 100, 2)
        if hi > lo:
            out["close_pos"] = round((last - lo) / (hi - lo) * 100)   # 0=저가권, 100=고가권
        # when did the high/low print (morning vs afternoon)?
        hi_i, lo_i = highs.index(hi), lows.index(lo)
        out["hi_time"] = str(tb[hi_i]["ts"])[-5:]
        out["lo_time"] = str(tb[lo_i]["ts"])[-5:]
        # day shape: slope of the two halves
        half = len(closes) // 2
        if half >= 2:
            a = (closes[half - 1] - closes[0])
            b = (closes[-1] - closes[half])
            out["shape"] = ("상승 지속" if a > 0 and b > 0 else
                            "상승 후 반락" if a > 0 and b < 0 else
                            "하락 후 반등(V)" if a < 0 and b > 0 else "하락 지속")
        # volume tilt: first third vs last third
        vols = [float(b.get("volume") or 0) for b in tb]
        third = max(1, len(vols) // 3)
        if sum(vols) > 0:
            out["vol_front"] = round(sum(vols[:third]) / sum(vols) * 100)
    except Exception as e:
        logger.warning("intraday read %s: %s", code, str(e)[:80])
    return out


# ---- last hour on 1-min bars -------------------------------------------------- #
def _minute_read(code: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        from services import kiwoom_rest as kr
        bars = kr.minute_bars(code, tic="1", count=60) or []
        closes = [float(b["close"]) for b in bars if b.get("close")]
        if len(closes) < 12:
            return out
        out["r10"] = round((closes[-1] / closes[-10] - 1) * 100, 2)
        out["r30"] = round((closes[-1] / closes[-min(30, len(closes))] - 1) * 100, 2)
        w = closes[-30:] if len(closes) >= 30 else closes
        t = max(3, len(w) // 3)
        out["higher_lows"] = min(w[-t:]) > min(w[:t])
        out["lower_highs"] = max(w[-t:]) < max(w[:t])
    except Exception as e:
        logger.warning("minute read %s: %s", code, str(e)[:80])
    return out


# ---- the composed read -------------------------------------------------------- #
def chart_read(db, code: str, name: Optional[str] = None) -> dict[str, Any]:
    code = str(code).zfill(6)
    name = name or code
    d = _daily_read(code)
    i = _intraday_read(db, code, d.get("prev_close"))
    m = _minute_read(code)
    pat = None
    try:
        from services.pattern_layer import pattern_vote
        pat = pattern_vote(db, code)
    except Exception:
        pass

    ko: list[str] = [f"📈 차트 분석 — {name}"]
    en: list[str] = [f"📈 Chart read — {name}"]
    if d:
        al = d.get("alignment") or "-"
        ab = d.get("above") or {}
        pos_txt = (f" · 20일 범위의 {d['pos20']}% 위치" if d.get("pos20") is not None else "")
        ko.append(f"- 일봉: MA {al} (5/20/60일선 {'위' if ab.get(5) else '아래'}/"
                  f"{'위' if ab.get(20) else '아래'}/{'위' if ab.get(60) else '아래'})"
                  f" · {abs(d.get('streak') or 0)}일 연속 {'상승' if (d.get('streak') or 0) > 0 else '하락'}"
                  f"{pos_txt} · 지지 ₩{_f(d.get('support'))} / 저항 ₩{_f(d.get('resistance'))}"
                  f" · 일변동성(ATR14) {d.get('atr_pct', '-')}%")
        pos_en = (f" · at {d['pos20']}% of the 20d range" if d.get("pos20") is not None else "")
        en.append(f"- Daily: MA {'bullish stack' if al == '정배열' else 'bearish stack' if al == '역배열' else 'mixed'}"
                  f" (vs MA5/20/60: {'above' if ab.get(5) else 'below'}/"
                  f"{'above' if ab.get(20) else 'below'}/{'above' if ab.get(60) else 'below'})"
                  f" · {abs(d.get('streak') or 0)}-day {'up' if (d.get('streak') or 0) > 0 else 'down'} streak"
                  f"{pos_en}"
                  f" · support ₩{_f(d.get('support'))} / resistance ₩{_f(d.get('resistance'))}"
                  f" · ATR14 {d.get('atr_pct', '-')}%")
    if i:
        gap = f"갭 {i['gap_pct']:+.1f}%" if i.get("gap_pct") is not None else ""
        cp = i.get("close_pos")
        cp_txt = ("고가권" if (cp or 0) >= 70 else "저가권" if (cp or 0) <= 30 else "중간") if cp is not None else "-"
        ko.append(f"- 오늘(5분봉): {gap} · 시가 대비 {i.get('vs_open_pct', 0):+.1f}% · {i.get('shape', '-')}"
                  f" · 고가 {i.get('hi_time', '-')} / 저가 {i.get('lo_time', '-')}"
                  f" · 현재가는 당일 범위의 {cp}% ({cp_txt})"
                  + (f" · 거래량 초반 집중 {i['vol_front']}%" if i.get("vol_front") is not None else ""))
        _shape_en = {"상승 지속": "steady rise", "상승 후 반락": "rise then fade",
                     "하락 후 반등(V)": "fall then V-rebound", "하락 지속": "steady fall"}.get(
                         i.get("shape") or "", i.get("shape", "-"))
        en.append(f"- Today (5-min): {'gap ' + format(i['gap_pct'], '+.1f') + '%' if i.get('gap_pct') is not None else ''}"
                  f" · vs open {i.get('vs_open_pct', 0):+.1f}% · shape: {_shape_en}"
                  f" · high at {i.get('hi_time', '-')} / low at {i.get('lo_time', '-')}"
                  f" · now at {cp}% of the day range"
                  + (f" · {i['vol_front']}% of volume in the first third" if i.get("vol_front") is not None else ""))
    if m:
        struct_ko = "저점 높아짐" if m.get("higher_lows") else "고점 낮아짐" if m.get("lower_highs") else "뚜렷한 구조 없음"
        struct_en = "higher lows" if m.get("higher_lows") else "lower highs" if m.get("lower_highs") else "no clear structure"
        ko.append(f"- 최근 1시간(1분봉): 10분 {m.get('r10', 0):+.2f}% · 30분 {m.get('r30', 0):+.2f}% · {struct_ko}")
        en.append(f"- Last hour (1-min): 10m {m.get('r10', 0):+.2f}% · 30m {m.get('r30', 0):+.2f}% · {struct_en}")
    if pat:
        ko.append(f"- 과거 패턴: {pat.get('line_ko')}")
        en.append(f"- History pattern: {pat.get('line_en')}")

    return {"ok": len(ko) > 1, "code": code, "name": name,
            "daily": d, "intraday": i, "minute": m, "pattern": pat,
            "block_ko": "\n".join(ko), "block_en": "\n".join(en)}
