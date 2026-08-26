"""checklist_engine.py — the boss's 100-item day-trading checklist, run by the agent.

The user has a paper checklist he ran manually before every trade (100 items across
Preparation / Market / Supply&Demand / Stock Selection / Execution). A human can't
complete 100 checks per trade — the agent can, in seconds, on live data.

Design: each item is a DATA row (id, category, question, checker, weight, deal_breaker),
not hardcoded logic — editable without touching the engine. Three honest outcomes per
item: pass / fail / unknown("확인 불가" — no data source yet, never faked). Two layers:

  market_preflight(db)          — items about TODAY's market (once, cached ~5 min)
  stock_scorecard(db, ticker)   — items about ONE stock (on demand)

Deal-breakers veto a BUY regardless of score (same pattern as the scalp tape veto).
Human-only items (sleep/emotion/journal) are NOT scored — they're listed as reminders.
decide() consumes this as a gate + an appended scorecard line; the chatbot exposes the
full card via the '체크리스트' intent.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

KST = timezone(timedelta(hours=9))

# ---------------------------------------------------------------- indicators

def _ma(vals, n):
    return sum(vals[-n:]) / n if len(vals) >= n else None


def _rsi(closes, n=14) -> Optional[float]:
    if len(closes) < n + 1:
        return None
    gains, losses = [], []
    for a, b in zip(closes[-n - 1:-1], closes[-n:]):
        d = b - a
        gains.append(max(d, 0)); losses.append(max(-d, 0))
    avg_g, avg_l = sum(gains) / n, sum(losses) / n
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return round(100 - 100 / (1 + rs), 1)


def _ema(vals, n):
    if len(vals) < n:
        return None
    k = 2 / (n + 1)
    e = sum(vals[:n]) / n
    for v in vals[n:]:
        e = v * k + e * (1 - k)
    return e


def _macd_golden(closes) -> Optional[bool]:
    if len(closes) < 35:
        return None
    macd = [ _ema(closes[:i], 12) - _ema(closes[:i], 26)
             for i in range(35, len(closes) + 1) ]
    if len(macd) < 9:
        return None
    sig = _ema(macd, 9)
    return macd[-1] > sig if sig is not None else None


def _boll_upper(closes, n=20, k=2) -> Optional[float]:
    if len(closes) < n:
        return None
    win = closes[-n:]
    m = sum(win) / n
    var = sum((v - m) ** 2 for v in win) / n
    return m + k * (var ** 0.5)


def _ichimoku_above_cloud(highs, lows, closes) -> Optional[bool]:
    if len(closes) < 52:
        return None
    def mid(n):  # (highest high + lowest low)/2 over n
        return (max(highs[-n:]) + min(lows[-n:])) / 2
    tenkan, kijun = mid(9), mid(26)
    span_a = (tenkan + kijun) / 2
    span_b = mid(52)
    return closes[-1] > max(span_a, span_b)


def _expiry_day_kr(now: datetime) -> bool:
    """KR futures/options expiry = 2nd Thursday of the month."""
    d = now.date()
    thursdays = [x for x in range(1, 22)
                 if datetime(d.year, d.month, x, tzinfo=KST).weekday() == 3]
    return len(thursdays) >= 2 and d.day == thursdays[1]


# ---------------------------------------------------------------- data context

_cache: dict[str, tuple[float, Any]] = {}


def _cached(key: str, ttl: float, fn: Callable[[], Any]):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    v = fn()
    _cache[key] = (time.time(), v)
    return v


def _stock_ctx(db, code: str, news: Optional[dict] = None) -> dict[str, Any]:
    """One fetch pass per stock — every checker reads from this dict.
    `news` lets decide() pass its already-fetched news dict (skips a live re-fetch)."""
    ctx: dict[str, Any] = {"code": code}
    try:
        from services import naver_stock as ns
        # NOTE: Naver /price returns EMPTY when pageSize > 90 — cap at 90 days.
        # Every indicator here fits: Ichimoku 52, MACD 35, MA60 60, RSI 15.
        hist = _cached(f"hist:{code}", 300, lambda: ns.daily_history(code, days=90))
        chron = list(reversed(hist or []))
        ctx["closes"] = [r["close"] for r in chron if r.get("close") is not None]
        ctx["highs"] = [r["high"] for r in chron if r.get("high") is not None]
        ctx["lows"] = [r["low"] for r in chron if r.get("low") is not None]
        ctx["vols"] = [r.get("volume") for r in chron if r.get("volume") is not None]
        ctx["opens"] = [r.get("open") for r in chron if r.get("open") is not None]
    except Exception:
        pass
    try:
        from services.trading_brief import realtime_for
        ctx["rt"] = realtime_for(code, db=db) or {}
    except Exception:
        ctx["rt"] = {}
    try:
        from services.trading_brief import _flow
        ctx["flow"] = _flow(db, code) or {}
    except Exception:
        ctx["flow"] = {}
    if news is not None:
        ctx["news"] = news
    else:
        try:
            from services.decision_agent import _news
            from services.stock_resolver import display_name
            ctx["news"] = _news(db, code, display_name(code))
        except Exception:
            ctx["news"] = {}
    try:
        from services.wave_method import wave_for
        ctx["wave"] = wave_for(db, code) or {}
    except Exception:
        ctx["wave"] = {}
    try:
        from services import prediction_service as ps
        ctx["ml"] = ps.get_ticker(db, code) or {}
    except Exception:
        ctx["ml"] = {}
    cur = (ctx.get("rt") or {}).get("price")
    ctx["cur"] = float(cur) if cur else (ctx["closes"][-1] if ctx.get("closes") else None)
    return ctx


# ---------------------------------------------------------------- checkers
# Each checker: ctx -> (ok: bool|None, detail: str). None = unknown/확인 불가.

def _c_market_direction(m):
    r = m.get("mkt_ret")
    if r is None:
        return None, "지수 데이터 없음"
    return r > -1.0, f"KODEX200 {r:+.1f}%"


def _c_market_plunge(m):
    r = m.get("mkt_ret")
    if r is None:
        return None, ""
    return r > -2.5, f"지수 {r:+.1f}%" + (" — 급락일" if r <= -2.5 else "")


def _c_market_news(m):
    s = m.get("news_score")
    if s is None:
        return None, "시장 뉴스 데이터 없음"
    return s > -2, f"시장 뉴스 점수 {s:+d}"


def _c_geopolitics(m):
    n = m.get("geo_hits", 0)
    return n == 0, ("지정학 리스크 뉴스 없음" if n == 0 else f"지정학/매크로 악재 {n}건")


def _c_expiry(m):
    e = m.get("expiry", False)
    return not e, ("만기일 아님" if not e else "선물/옵션 만기일 — 변동성 주의")


def _c_near_close(m):
    n = datetime.now(KST)
    mins = n.hour * 60 + n.minute
    late = 540 <= mins <= 931 and mins >= 870          # after 14:30 KST
    return not late, ("장 마감 전 주의 시간대" if late else "마감 전 아님")


def _c_us_close(m):
    nd = (m.get("mi") or {}).get("nasdaq")
    if not nd:
        return None, "나스닥 데이터 없음"
    return nd["pct"] > -1.5, f"나스닥(전일) {nd['pct']:+.2f}%"


def _c_vix(m):
    vx = (m.get("mi") or {}).get("vix")
    if not vx:
        return None, "VIX 데이터 없음"
    v = float(str(vx["price"]).replace(",", ""))
    return v < 28, f"VIX {vx['price']}" + (" — 공포권" if v >= 30 else " — 경계권" if v >= 20 else " — 안정권")


def _c_oil(m):
    o = (m.get("mi") or {}).get("wti")
    if not o:
        return None, "유가 데이터 없음"
    return abs(o["pct"]) < 3, f"WTI ${o['price']} ({o['pct']:+.2f}%)" + (" — 급변동" if abs(o["pct"]) >= 3 else "")


def _c_fx(m):
    fx = (m.get("mi") or {}).get("usdkrw")
    if not fx:
        return None, "환율 데이터 없음"
    return abs(fx["pct"]) < 1.0, f"원/달러 {fx['price']} ({fx['pct']:+.2f}%)" + (" — 급변동" if abs(fx["pct"]) >= 1.0 else "")


def _c_bond(m):
    b = m.get("bond")
    if not b:
        return None, "국채 금리 데이터 없음"
    return abs(b["pct"]) < 4.0, f"국고채 10년 {b['price']}% ({b['pct']:+.2f}%)" + (" — 급변동" if abs(b["pct"]) >= 4.0 else "")


def _c_nq_futures(m):
    f = m.get("nqf")
    if not f:
        return None, "나스닥 선물 데이터 없음"
    return f["pct"] > -1.5, f"나스닥100 선물 {f['price']:,.0f} ({f['pct']:+.2f}%)"


def _c_policy_benefit(m):
    hits = m.get("benefit_hits")
    if hits is None:
        return None, "뉴스 데이터 없음"
    return True, ("정책 수혜 뉴스 감지: " + ", ".join(hits[:3])) if hits else "감지된 정책 수혜 뉴스 없음"


def _c_msci_div(m):
    hits = m.get("msci_hits") or []
    now = datetime.now(KST)
    msci_window = now.month in (2, 5, 8, 11) and now.day >= 22   # quarterly review effective window
    bits = []
    if msci_window:
        bits.append("MSCI 분기 리뷰 반영 시기(월말)")
    if hits:
        bits.append("일정 뉴스: " + ", ".join(hits[:2]))
    return True, " · ".join(bits) if bits else "감지된 배당/MSCI 일정 없음"


def _c_mkt_value(m):
    """#21 — total KOSPI trading value vs its own recent average (the baseline builds
    itself: each session's final value is stored, judgment starts at 5 stored days)."""
    v = m.get("mkt_value")
    base = m.get("mkt_value_base")
    if not v:
        return None, "시장 거래대금 데이터 없음"
    disp = f"코스피 거래대금 {v / 1e12:.1f}조원"
    if not base:
        return None, disp + " — 평균 축적 중 (5거래일 후 판정 시작)"
    r = v / base
    return r >= 0.8, disp + f" (최근 평균의 {r:.2f}배)" + (" — 한산" if r < 0.8 else "")


def _c_mkt_liquidity(m):
    """#25 — overall liquidity, read from the same trading-value ratio."""
    v = m.get("mkt_value")
    base = m.get("mkt_value_base")
    if not v:
        return None, "시장 거래대금 데이터 없음"
    if not base:
        return None, f"거래대금 {v / 1e12:.1f}조원 — 평균 축적 중"
    r = v / base
    return r >= 0.7, f"유동성 {'충분' if r >= 0.7 else '부족'} (거래대금이 평균의 {r:.2f}배)"


def _c_futures_flow(m):
    """#24 — intraday futures flow, proxied by KODEX200 (the spot twin of the K200
    futures; no free futures feed exists, and the proxy is labeled as one)."""
    r = m.get("mkt_ret")
    if r is None:
        return None, "지수 데이터 없음"
    return r > -1.0, f"KODEX200 {r:+.1f}% (지수선물 대용 지표)"


def _c_pension(m):
    hits = m.get("pension_hits")
    if hits is None:
        return None, "뉴스 데이터 없음"
    return True, ("연기금 관련 뉴스: " + ", ".join(hits[:2])) if hits else "연기금 매매 관련 특이 뉴스 없음"


def _c_fut_fx(m):
    """#37 — stock/dollar futures flows, proxied by the spot pair (환율 + 지수)."""
    fx = (m.get("mi") or {}).get("usdkrw")
    r = m.get("mkt_ret")
    if not fx and r is None:
        return None, "데이터 없음"
    bits = []
    ok = True
    if fx:
        bits.append(f"달러선물 대용: 환율 {fx['price']} ({fx['pct']:+.2f}%)")
        ok = ok and abs(fx["pct"]) < 1.0
    if r is not None:
        bits.append(f"주식선물 대용: KODEX200 {r:+.1f}%")
        ok = ok and r > -2.5
    return ok, " · ".join(bits)


def _c_sector_rotation(m):
    hits = m.get("sector_hits")
    if hits is None:
        return None, "뉴스 데이터 없음"
    if not hits:
        return True, "오늘 뉴스에 뚜렷한 주도 섹터 언급 없음"
    top = " · ".join(f"{k} {v}건" for k, v in hits[:3])
    return True, f"오늘 뉴스 언급 섹터: {top}"


def _c_econ_events(m):
    hits = m.get("econ_hits")
    if hits is None:
        return None, "뉴스 데이터 없음"
    return True, ("주요 지표 일정 감지: " + ", ".join(hits[:3])) if hits else "감지된 경제지표 일정 없음"


def _c_policy_events(m):
    hits = m.get("policy_hits")
    if hits is None:
        return None, "뉴스 데이터 없음"
    return True, ("정책 이벤트 감지: " + ", ".join(hits[:3])) if hits else "감지된 정책 이벤트 없음"


def _mk(ok, detail):
    return ok, detail


def _c_ma_align(c):
    cl = c.get("closes") or []
    if len(cl) < 60:
        return None, "일봉 부족"
    cur, m5, m20, m60 = cl[-1], _ma(cl, 5), _ma(cl, 20), _ma(cl, 60)
    ok = cur > m20 and m5 > m20
    return ok, f"현재가 vs MA20 {'위' if cur > m20 else '아래'} · MA5{'>' if m5 > m20 else '<'}MA20"


def _c_daily_uptrend(c):
    cl = c.get("closes") or []
    if len(cl) < 25:
        return None, "일봉 부족"
    return cl[-1] > _ma(cl, 20), "20일선 " + ("위" if cl[-1] > _ma(cl, 20) else "아래")


def _c_box_or_trend(c):
    cl, hi, lo = c.get("closes") or [], c.get("highs") or [], c.get("lows") or []
    if len(cl) < 20:
        return None, "일봉 부족"
    res, sup = max(hi[-20:]), min(lo[-20:])
    pos = (cl[-1] - sup) / (res - sup) * 100 if res > sup else 50
    return True, f"박스권 {round(pos)}% 지점 (지지 {sup:,.0f} / 저항 {res:,.0f})"


def _c_support_clear(c):
    hi, lo = c.get("highs") or [], c.get("lows") or []
    if len(lo) < 20:
        return None, "일봉 부족"
    res, sup = max(hi[-20:]), min(lo[-20:])
    return (res - sup) / sup > 0.03, f"지지-저항 폭 {round((res - sup) / sup * 100, 1)}%"


def _c_vol_surge(c):
    v = c.get("vols") or []
    if len(v) < 21:
        return None, "거래량 데이터 부족"
    avg = sum(v[-21:-1]) / 20
    ratio = v[-1] / avg if avg else 0
    return ratio >= 1.2, f"거래량 20일 평균의 {ratio:.1f}배"


def _c_gap_open(c):
    cl, op = c.get("closes") or [], c.get("opens") or []
    if len(cl) < 2 or not op:
        return None, "시가 데이터 부족"
    gap = (op[-1] - cl[-2]) / cl[-2] * 100
    return abs(gap) < 4, f"갭 {gap:+.1f}%" + (" — 과대 갭 주의" if abs(gap) >= 4 else "")


def _c_new_high_low(c):
    cl, hi, lo = c.get("closes") or [], c.get("highs") or [], c.get("lows") or []
    if len(cl) < 60:
        return None, "일봉 부족"
    cur = c.get("cur") or cl[-1]
    if cur >= max(hi[-60:]) * 0.995:
        return True, "60일 신고가권 (추세 강함)"
    if cur <= min(lo[-60:]) * 1.005:
        return False, "60일 신저가권 (약세)"
    return True, "신고/신저가 아님"


def _c_vs_yesterday(c):
    cl = c.get("closes") or []
    cur = c.get("cur")
    if len(cl) < 2 or not cur:
        return None, "데이터 부족"
    chg = (cur - cl[-2]) / cl[-2] * 100
    return chg > -3, f"전일 종가 대비 {chg:+.1f}%"


def _c_recovered_open(c):
    rt = c.get("rt") or {}
    cur, op = c.get("cur"), (c.get("opens") or [None])[-1]
    if not cur or not op:
        return None, "시가 데이터 부족"
    return cur >= op * 0.995, f"시가 대비 {'회복' if cur >= op * 0.995 else '미회복'} ({(cur - op) / op * 100:+.1f}%)"


def _c_spoof(c):
    """#57 — fake bid/ask suspicion: an extreme one-sided order-book wall. Live
    imbalance first; after hours, the last recorded book snapshot."""
    imb = (c.get("rt") or {}).get("imbalance")
    src = "실시간 호가"
    if imb is None:
        try:
            from services.kiwoom_tape import load_book
            rows = load_book(c.get("code") or "")
            if rows:
                b = rows[-1]
                asks = sum(q for _p, q in (b.get("asks") or []))
                bids = sum(q for _p, q in (b.get("bids") or []))
                if asks + bids:
                    imb = (bids - asks) / (bids + asks)
                    src = f"마지막 호가 스냅샷 {str(b.get('t') or '')[:5]}"
        except Exception:
            pass
    if imb is None:
        return None, "호가 데이터 없음 (데스크 종목만 측정)"
    ok = abs(imb) <= 0.6
    return ok, f"호가 쏠림 {imb:+.0%} ({src}) — " + ("허매수/허매도 의심 없음" if ok else "한쪽 벽 과대 — 허수 주의")


def _c_min5_align(c):
    """#59 — 5-minute-bar bullish alignment, from OUR OWN recorded tape (desk stocks)."""
    try:
        from services.kiwoom_tape import bars_time, load
        ticks = load(c.get("code") or "")
        if not ticks:
            return None, "실시간 테이프 없음 (데스크 종목만 측정)"
        bars = bars_time(ticks, 300)
        if len(bars) < 12:
            return None, "5분봉 부족"
        cl = [b["close"] for b in bars]
        m5, m10 = sum(cl[-5:]) / 5, sum(cl[-10:]) / 10
        ok = cl[-1] > m5 > m10
        return ok, (f"5분봉 {'정배열' if ok else '역배열/혼조'} "
                    f"(현재 {cl[-1]:,.0f} · MA5 {m5:,.0f} · MA10 {m10:,.0f})")
    except Exception:
        return None, "테이프 읽기 실패"


def _c_min_pullback(c):
    """#64 — a pullback forming on the minute chart: a rise, then a shallow dip."""
    try:
        from services.kiwoom_tape import bars_time, load
        ticks = load(c.get("code") or "")
        if not ticks:
            return None, "실시간 테이프 없음 (데스크 종목만 측정)"
        bars = bars_time(ticks, 300)
        if len(bars) < 8:
            return None, "5분봉 부족"
        cl = [b["close"] for b in bars]
        rise = (cl[-3] - cl[-8]) / cl[-8] * 100 if cl[-8] else 0
        dip = (cl[-1] - cl[-3]) / cl[-3] * 100 if cl[-3] else 0
        ok = rise >= 0.3 and -0.6 <= dip < 0
        return ok, (f"직전 상승 {rise:+.2f}% · 최근 되돌림 {dip:+.2f}%"
                    + (" — 눌림목 형성" if ok else ""))
    except Exception:
        return None, "테이프 읽기 실패"


def _c_vs_yday_range(c):
    """#54 — where the current price sits against YESTERDAY's high/low/open."""
    hi, lo, op = c.get("highs") or [], c.get("lows") or [], c.get("opens") or []
    cur = c.get("cur")
    if len(hi) < 2 or not cur:
        return None, "전일 데이터 부족"
    yh, yl, yo = hi[-2], lo[-2], (op[-2] if len(op) >= 2 else None)
    if yh <= yl:
        return None, "전일 범위 없음"
    pos = (cur - yl) / (yh - yl) * 100
    return cur >= yl, (f"전일 고가 {yh:,.0f} · 저가 {yl:,.0f}"
                       + (f" · 시가 {yo:,.0f}" if yo else "")
                       + f" — 현재 범위 {round(pos)}% 지점"
                       + (" (전일 저가 이탈)" if cur < yl else ""))


def _c_prev_support(c):
    """#63 — holding above the previous low (support intact)."""
    lo = c.get("lows") or []
    cur = c.get("cur")
    if len(lo) < 6 or not cur:
        return None, "일봉 부족"
    sup = min(lo[-6:-1])
    ok = cur >= sup * 0.995
    return ok, f"최근 5일 저점 {sup:,.0f} — " + ("지지 유지" if ok else "지지 이탈")


def _c_rsi(c):
    r = _rsi(c.get("closes") or [])
    if r is None:
        return None, "일봉 부족"
    return 25 <= r <= 72, f"RSI(14) {r}" + (" — 과열" if r > 72 else " — 과매도" if r < 25 else "")


def _c_macd(c):
    g = _macd_golden(c.get("closes") or [])
    if g is None:
        return None, "일봉 부족"
    return g, "MACD " + ("골든크로스 상태" if g else "데드크로스 상태")


def _c_bollinger(c):
    up = _boll_upper(c.get("closes") or [])
    cur = c.get("cur")
    if up is None or not cur:
        return None, "일봉 부족"
    return cur <= up * 1.02, ("볼린저 상단 과이탈 — 과열" if cur > up * 1.02 else "볼린저 밴드 정상 범위")


def _c_ichimoku(c):
    a = _ichimoku_above_cloud(c.get("highs") or [], c.get("lows") or [], c.get("closes") or [])
    if a is None:
        return None, "일봉 부족(52일)"
    return a, "일목균형표 구름대 " + ("위" if a else "아래")


def _c_pivot_r2(c):
    hi, lo, cl = c.get("highs") or [], c.get("lows") or [], c.get("closes") or []
    cur = c.get("cur")
    if len(cl) < 2 or not cur:
        return None, "데이터 부족"
    p = (hi[-2] + lo[-2] + cl[-2]) / 3
    r2 = p + (hi[-2] - lo[-2])
    return cur < r2, f"피봇 R2({r2:,.0f}) " + ("아래 — 여유 있음" if cur < r2 else "돌파 — 과열 주의")


def _c_near_day_high(c):
    rt = c.get("rt") or {}
    cur = c.get("cur")
    hi, lo = c.get("highs") or [], c.get("lows") or []
    if not cur or not hi:
        return None, "데이터 부족"
    dh, dl = hi[-1], lo[-1]
    if dh <= dl:
        return None, "당일 범위 없음"
    pos = (cur - dl) / (dh - dl) * 100
    return pos < 85, f"당일 범위 {round(pos)}% 지점" + (" — 고점 추격 주의" if pos >= 85 else "")


def _c_orderbook_strength(c):
    rt = c.get("rt") or {}
    if not rt.get("live"):
        return None, "실시간 호가 없음"
    imb = rt.get("imbalance")
    return (imb or 0) > 0, f"호가 {'매수우위' if (imb or 0) > 0 else '매도우위'} ({imb:+.0%})" if imb is not None else (None, "호가 없음")


def _c_short_overheat(c):
    rt = c.get("rt") or {}
    sr = rt.get("short_ratio")
    if sr is None:
        return None, "공매도 데이터 없음"
    return float(sr) < 10, f"공매도 비중 {sr}%" + (" — 과열" if float(sr) >= 10 else "")


def _c_program(c):
    rt = c.get("rt") or {}
    pn = rt.get("program_net")
    if pn is None:
        return None, "프로그램 데이터 없음"
    return pn >= 0, f"프로그램 순매수 {pn:+,}"


def _c_foreign_today(c):
    f = c.get("flow") or {}
    fn = f.get("foreign_net")
    if fn is None:
        return None, "수급 데이터 없음"
    return fn > 0, f"외국인 {fn:+,}"


def _c_inst_today(c):
    f = c.get("flow") or {}
    n = f.get("inst_net")
    if n is None:
        return None, "수급 데이터 없음"
    return n > 0, f"기관 {n:+,}"


def _c_flows_5d(c):
    f = c.get("flow") or {}
    n5 = (f.get("foreign_5d") or 0) + (f.get("inst_5d") or 0)
    if not f:
        return None, "수급 데이터 없음"
    return n5 > 0, f"외인+기관 5일 합산 {n5:+,}"


def _c_not_distribution(c):
    f = c.get("flow") or {}
    tag = f.get("tag")
    if not tag:
        return None, "수급 태그 없음"
    return tag != "분산매도", f"수급 패턴: {tag}"


def _c_stock_news_positive(c):
    n = c.get("news") or {}
    s = n.get("score")
    if s is None or n.get("count", 0) == 0:
        return None, "종목 뉴스 없음"
    return s >= 0, f"뉴스 점수 {s:+d} ({n.get('count')}건)"


def _c_catalyst_quality(c):
    n = c.get("news") or {}
    titles = " ".join(n.get("titles") or [])
    if not titles:
        return None, "뉴스 없음"
    strong = any(k in titles for k in ("실적", "수주", "계약", "증설", "공급", "인수"))
    weak_only = any(k in titles for k in ("목표가", "리포트")) and not strong
    return not weak_only, ("지속성 있는 재료(실적/수주/증설)" if strong
                           else "리포트성 재료뿐 — 지속성 약함" if weak_only else "일반 뉴스")


def _c_rr(c):
    w = c.get("wave") or {}
    rr = w.get("rr")
    if rr is None:
        cl, hi, lo = c.get("closes") or [], c.get("highs") or [], c.get("lows") or []
        cur = c.get("cur")
        if len(cl) >= 20 and cur:
            res, sup = max(hi[-20:]), min(lo[-20:])
            stop = max(sup * 0.98, cur * 0.97)
            tgt = min(res, cur * 1.04)
            rr = round((tgt - cur) / (cur - stop), 2) if cur > stop else None
    if rr is None:
        return None, "손익비 계산 불가"
    return float(rr) >= 1.2, f"손익비 {rr}"


def _c_entry_levels(c):
    cl, hi, lo = c.get("closes") or [], c.get("highs") or [], c.get("lows") or []
    if len(cl) < 20:
        return None, "레벨 계산 불가"
    return True, f"진입/손절/목표 산출 가능 (지지 {min(lo[-20:]):,.0f} · 저항 {max(hi[-20:]):,.0f})"


def _c_method_agreement(c):
    ml = ((c.get("ml") or {}).get("advice") or "").upper()
    wv = ((c.get("wave") or {}).get("verdict") or "").upper()
    if not ml and not wv:
        return None, "방법 신호 데이터 없음"
    n = sum(1 for v in (ml == "BUY", wv == "BUY") if v)
    return n >= 1, f"방법 신호: ML {ml or '-'} · 파동 {wv or '-'}"


# ---------------------------------------------------------------- item registry
# (no, category, question_ko, checker, weight, deal_breaker)

# (no, category, question_ko, question_en, checker, weight, deal_breaker)
MARKET_ITEMS = [
    (11, "시장", "코스피/코스닥 방향은 상방인가?", "Are KOSPI/KOSDAQ pointing up?", _c_market_direction, 2, False),
    (12, "시장", "전일 미국 증시(나스닥) 마감은 무난했는가?", "Was the previous US (NASDAQ) close OK?", _c_us_close, 1, False),
    (13, "시장", "나스닥 선물 지수의 흐름은?", "How are NASDAQ futures moving?", _c_nq_futures, 1, False),
    (28, "이슈", "정부의 정책 수혜 기대감이 있는가?", "Expectation of government policy benefits?", _c_policy_benefit, 1, False),
    (30, "이슈", "배당/MSCI 등 주요 일정이 있는가?", "Key schedules like dividends/MSCI?", _c_msci_div, 1, False),
    (14, "시장", "원·달러 환율이 급변동하지 않는가?", "Is USD/KRW NOT swinging sharply?", _c_fx, 1, False),
    (15, "시장", "국채 금리 움직임이 안정적인가?", "Are government bond yields stable?", _c_bond, 1, False),
    (18, "시장", "오늘 발표될 경제 지표가 있는가?", "Any economic indicators being released today?", _c_econ_events, 1, False),
    (19, "시장", "금리 결정 등 정책 이벤트가 있는가?", "Any policy events like rate decisions?", _c_policy_events, 1, False),
    (17, "시장", "VIX(공포지수)가 적정 수준인가?", "Is the VIX (fear index) at a reasonable level?", _c_vix, 1, False),
    (16, "시장", "유가가 급변동하고 있지 않은가?", "Is oil NOT swinging sharply?", _c_oil, 1, False),
    (21, "시장", "시장 전체 거래 대금이 평소보다 많은가?", "Is total market trading value above normal?", _c_mkt_value, 1, False),
    (25, "시장", "시장의 전반적인 유동성이 충분한가?", "Is overall market liquidity sufficient?", _c_mkt_liquidity, 1, False),
    (24, "시장", "장중 선물 시장의 흐름은?", "Intraday futures market flow?", _c_futures_flow, 1, False),
    (33, "이슈", "연기금의 매매 방향은?", "Pension funds' trading direction?", _c_pension, 1, False),
    (37, "이슈", "주식선물/달러선물 수급 체크", "Check stock-futures / dollar-futures flows", _c_fut_fx, 1, False),
    (39, "이슈", "섹터별 순환매 흐름은 어디인가?", "Where is the sector rotation flowing?", _c_sector_rotation, 1, False),
    (22, "시장", "시장 전체를 압도하는 악재는 없는가?", "No market-wide overwhelming bad news?", _c_market_news, 2, True),
    (20, "시장", "지정학적 리스크는 없는가?", "No geopolitical risk?", _c_geopolitics, 1, False),
    (95, "시장", "지수 급락일이 아닌가? (방어)", "Not an index-plunge day? (defense)", _c_market_plunge, 3, True),
    (36, "시장", "선물/옵션 만기일이 아닌가?", "Not a futures/options expiry day?", _c_expiry, 1, False),
    (100, "시장", "장 마감 직전 시간대가 아닌가?", "Not right before market close?", _c_near_close, 1, False),
]

STOCK_ITEMS = [
    (51, "종목선정", "가격이 이동평균선(5/20/60) 위에 정렬돼 있는가?", "Price aligned above the 5/20/60 moving averages?", _c_ma_align, 2, False),
    (58, "종목선정", "일봉 추세가 상방인가?", "Is the daily-chart trend up?", _c_daily_uptrend, 2, False),
    (59, "종목선정", "5분봉상 정배열인가?", "Bullish alignment on the 5-minute chart?", _c_min5_align, 1, False),
    (64, "종목선정", "분봉상 눌림목 형성인가?", "Pullback forming on the minute chart?", _c_min_pullback, 1, False),
    (52, "종목선정", "추세장인가 박스권인가 파악됐는가?", "Trending or range-bound — identified?", _c_box_or_trend, 1, False),
    (53, "종목선정", "지지/저항선이 명확한가?", "Are support/resistance clear?", _c_support_clear, 1, False),
    (47, "종목선정", "거래량이 평소보다 증가했는가?", "Is volume above normal?", _c_vol_surge, 2, False),
    (49, "종목선정", "과도한 갭 시가가 아닌가?", "No excessive opening gap?", _c_gap_open, 1, False),
    (50, "종목선정", "신고가/신저가 위치는 유리한가?", "Is the new-high/new-low position favorable?", _c_new_high_low, 1, False),
    (54, "종목선정", "전일 고점/저점/시가 위치는?", "Position vs yesterday's high/low/open?", _c_vs_yday_range, 1, False),
    (63, "종목선정", "이전 고점/저점 지지인가?", "Holding above previous high/low support?", _c_prev_support, 1, False),
    (67, "종목선정", "전일 종가 대비 위치는 건전한가?", "Healthy position vs yesterday's close?", _c_vs_yesterday, 1, False),
    (68, "종목선정", "시가를 회복했는가?", "Has it recovered the opening price?", _c_recovered_open, 1, False),
    (60, "종목선정", "RSI가 과열/과매도 구간이 아닌가?", "RSI not overbought/oversold?", _c_rsi, 1, False),
    (61, "종목선정", "MACD가 골든크로스 상태인가?", "Is MACD in a golden cross?", _c_macd, 1, False),
    (62, "종목선정", "볼린저 상단 과이탈(과열)이 아닌가?", "Not overextended above the Bollinger upper band?", _c_bollinger, 1, False),
    (65, "종목선정", "일목균형표 구름대 위인가?", "Above the Ichimoku cloud?", _c_ichimoku, 1, False),
    (66, "종목선정", "피봇 R2 아래(과열 아님)인가?", "Below pivot R2 (not overheated)?", _c_pivot_r2, 1, False),
    (75, "종목선정", "당일 고점 추격 매수가 아닌가?", "Not chasing today's high?", _c_near_day_high, 1, False),
    (55, "수급", "호가창 체결 강도(매수우위)가 있는가?", "Order-book strength on the bid side?", _c_orderbook_strength, 2, False),
    (57, "수급", "허매수/허매도가 의심되는가?", "Suspected fake bids/asks (spoofing)?", _c_spoof, 1, False),
    (43, "수급", "공매도 과열 종목이 아닌가?", "Not a short-selling-overheated stock?", _c_short_overheat, 2, True),
    (35, "수급", "프로그램 매매가 우호적인가?", "Is program trading favorable?", _c_program, 1, False),
    (31, "수급", "외국인이 오늘 순매수인가?", "Are foreigners net buying today?", _c_foreign_today, 1, False),
    (32, "수급", "기관이 오늘 순매수인가?", "Are institutions net buying today?", _c_inst_today, 1, False),
    (38, "수급", "외인+기관 5일 수급이 순유입인가?", "Foreign+institutional 5-day flows net positive?", _c_flows_5d, 2, False),
    (34, "수급", "분산매도(개인 집중) 패턴이 아닌가?", "Not a distribution (retail-crowding) pattern?", _c_not_distribution, 1, False),
    (23, "이슈", "종목 뉴스 흐름이 우호적인가?", "Is the stock's news flow favorable?", _c_stock_news_positive, 2, False),
    (41, "이슈", "재료가 일회성이 아니라 지속성이 있는가?", "Is the catalyst sustainable (not one-off)?", _c_catalyst_quality, 1, False),
    (76, "실행", "진입/손절/목표 레벨이 명확한가?", "Entry/stop/target levels clear?", _c_entry_levels, 2, False),
    (79, "실행", "손익비가 1.2 이상인가?", "Risk:reward at least 1.2?", _c_rr, 2, True),
    (82, "실행", "방법(ML/파동) 중 매수를 지지하는 신호가 있는가?", "Does at least one method (ML/Wave) back a buy?", _c_method_agreement, 2, False),
]

_CAT_EN = {"시장": "Market", "종목선정": "Stock selection", "수급": "Supply & demand",
           "이슈": "Catalyst", "실행": "Execution"}

# Human-only reminders (not scored — the agent can't honestly check these)
HUMAN_REMINDERS_KO = [
    "충분히 잤는가? 컨디션·감정 상태는 안정적인가? (1~4)",
    "오늘의 매매 목표와 손실 한도를 정했는가? (5, 92)",
    "예측이 아니라 대응할 준비가 됐는가? (10)",
    "손절은 기계적으로, 뇌동매매 금지 (83, 85, 87)",
]
HUMAN_REMINDERS_EN = [
    "Slept enough? Condition and emotions stable? (1–4)",
    "Set today's trading goal and loss limit? (5, 92)",
    "Ready to REACT, not predict? (10)",
    "Cut losses mechanically — no impulsive/herd trades (83, 85, 87)",
]


# ---------------------------------------------------------------- layers

def market_preflight(db) -> dict[str, Any]:
    """Layer 1 — TODAY's market readiness. Cached 5 min."""
    def _build():
        m: dict[str, Any] = {}
        try:
            from services.trading_brief import _mkt_ret_today
            m["mkt_ret"] = _mkt_ret_today(db)
        except Exception:
            m["mkt_ret"] = None
        try:
            from services.news_impact import effective_news
            items = effective_news(db, None, limit=8) or []
            sc = 0
            geo = 0
            econ, pol, ben, msci = [], [], [], []
            _ECON_KW = ("CPI", "GDP", "고용", "물가", "PMI", "실업", "수출입", "경제지표")
            _POL_KW = ("금리 결정", "금리결정", "FOMC", "금통위", "연준", "기준금리", "한국은행")
            _BEN_KW = ("수혜", "정책 지원", "보조금", "규제 완화", "지원책", "육성")
            _MSCI_KW = ("MSCI", "배당락", "배당 기준일", "리밸런싱")
            _PEN_KW = ("연기금", "국민연금", "공무원연금")
            _SECT_KW = ("반도체", "바이오", "방산", "조선", "2차전지", "배터리", "금융",
                        "자동차", "정유", "화학", "플랫폼", "AI")
            pen: list = []
            sect: dict = {}
            for n in items:
                d = n.get("direction") or 0
                sc += (1 if d in (1, "▲") else -1 if d in (-1, "▼") else 0)
                if n.get("type") == "지정학/매크로" and d in (-1, "▼"):
                    geo += 1
                t_ = n.get("title") or ""
                if any(k in t_ for k in _ECON_KW):
                    econ.append(t_[:30])
                if any(k in t_ for k in _POL_KW):
                    pol.append(t_[:30])
                if any(k in t_ for k in _BEN_KW):
                    ben.append(t_[:30])
                if any(k in t_ for k in _MSCI_KW):
                    msci.append(t_[:30])
                if any(k in t_ for k in _PEN_KW):
                    pen.append(t_[:30])
                for k in _SECT_KW:
                    if k in t_:
                        sect[k] = sect.get(k, 0) + 1
            m["news_score"] = max(-3, min(3, sc))
            m["pension_hits"] = pen
            m["sector_hits"] = sorted(sect.items(), key=lambda x: -x[1])
            m["geo_hits"] = geo
            m["econ_hits"] = econ
            m["policy_hits"] = pol
            m["benefit_hits"] = ben
            m["msci_hits"] = msci
        except Exception:
            m["news_score"] = None
            m["geo_hits"] = 0
            m["econ_hits"] = None
            m["policy_hits"] = None
            m["benefit_hits"] = None
            m["msci_hits"] = None
            m["pension_hits"] = None
            m["sector_hits"] = None
        try:
            # 나스닥100 선물 (Yahoo NQ=F) — checklist #13; cached with the 5-min preflight
            import httpx as _hx2
            j2 = _hx2.get("https://query1.finance.yahoo.com/v8/finance/chart/NQ%3DF"
                          "?range=1d&interval=1d",
                          headers={"User-Agent": "Mozilla/5.0"}, timeout=8).json()
            mt = j2["chart"]["result"][0]["meta"]
            px_, pv_ = mt.get("regularMarketPrice"), (mt.get("chartPreviousClose")
                                                     or mt.get("previousClose"))
            if px_ and pv_:
                m["nqf"] = {"price": float(px_), "pct": (float(px_) / float(pv_) - 1) * 100}
        except Exception:
            m["nqf"] = None
        try:
            # 코스피 전체 거래대금 (Naver polling API) — checklist #21/#25. The baseline is
            # our own rolling store: today's max-seen value is written per day, judgment
            # begins once 5 prior sessions are stored (never a made-up threshold).
            import httpx as _hx3
            j3 = _hx3.get("https://polling.finance.naver.com/api/realtime/domestic/index/KOSPI",
                          headers={"User-Agent": "Mozilla/5.0"}, timeout=6).json()
            d3 = (j3.get("datas") or [{}])[0]
            raw = d3.get("accumulatedTradingValueRaw")
            if raw:
                v = float(raw)
                m["mkt_value"] = v
                _hf = Path(__file__).resolve().parent.parent / "data" / "market_value_hist.json"
                try:
                    hist = json.loads(_hf.read_text(encoding="utf-8"))
                except Exception:
                    hist = {}
                _dkey = datetime.now(KST).strftime("%Y%m%d")
                hist[_dkey] = max(v, float(hist.get(_dkey, 0)))
                hist = dict(sorted(hist.items())[-40:])
                _hf.parent.mkdir(exist_ok=True)
                _hf.write_text(json.dumps(hist), encoding="utf-8")
                prior = [float(x) for k, x in hist.items() if k != _dkey]
                if len(prior) >= 5:
                    m["mkt_value_base"] = sum(prior[-20:]) / len(prior[-20:])
        except Exception:
            m["mkt_value"] = None
        m["expiry"] = _expiry_day_kr(datetime.now(KST))
        try:
            # 국고채 10년 (Naver bond API) — checklist #15
            import httpx as _hx
            j = _hx.get("https://m.stock.naver.com/front-api/marketIndex/prices"
                        "?category=bond&reutersCode=KR10YT%3DRR&page=1&pageSize=10",
                        headers={"User-Agent": "Mozilla/5.0"}, timeout=6).json()
            row = ((j.get("result") or [{}])[0]) if isinstance(j.get("result"), list) else {}
            if row.get("closePrice"):
                m["bond"] = {"price": row["closePrice"],
                             "pct": float(str(row.get("fluctuationsRatio") or 0).replace(",", ""))}
        except Exception:
            m["bond"] = None
        try:
            from services.decision_agent import _market_indicators
            m["mi"] = _market_indicators()      # 나스닥/VIX/유가 (+국내지수/환율)
        except Exception:
            m["mi"] = {}
        return m
    mctx = _cached("preflight", 300, _build)
    return _score_items(MARKET_ITEMS, mctx, layer="market")


def stock_scorecard(db, ticker: str, news: Optional[dict] = None) -> dict[str, Any]:
    """Layer 2 — one stock vs the checklist + the market layer folded in."""
    code = str(ticker).zfill(6)
    sctx = _stock_ctx(db, code, news=news)
    stock = _score_items(STOCK_ITEMS, sctx, layer="stock")
    market = market_preflight(db)
    from services.stock_resolver import display_name
    out = {
        "ticker": code, "name": display_name(code),
        "market": market, "stock": stock,
        "score": stock["score"], "max": stock["max"],
        "pct": stock["pct"],
        "deal_breakers": market["deal_breakers"] + stock["deal_breakers"],
        "unknown": stock["unknown"] + market["unknown"],
        "human_reminders": HUMAN_REMINDERS_KO,
        "human_reminders_en": HUMAN_REMINDERS_EN,
    }
    out["verdict_ok"] = not out["deal_breakers"] and stock["pct"] is not None and stock["pct"] >= 55
    return out


def _score_items(items, ctx, layer: str) -> dict[str, Any]:
    rows, score, mx = [], 0, 0
    breakers, unknown = [], []
    for no, cat, q, q_en, fn, w, db_flag in items:
        try:
            ok, detail = fn(ctx)
        except Exception as e:
            ok, detail = None, f"체크 오류: {str(e)[:40]}"
        rows.append({"no": no, "category": cat, "q": q, "q_en": q_en, "ok": ok,
                     "detail": detail, "weight": w, "deal_breaker": db_flag})
        if ok is None:
            unknown.append(no)
            continue
        mx += w
        if ok:
            score += w
        elif db_flag:
            breakers.append({"no": no, "q": q, "detail": detail})
    return {"layer": layer, "items": rows, "score": score, "max": mx,
            "pct": round(score / mx * 100) if mx else None,
            "deal_breakers": breakers, "unknown": unknown}


# ---------------------------------------------------------------- rendering

def render_ko(card: dict[str, Any]) -> str:
    """Full scorecard in the user's paper-checklist style."""
    L = [f"**📋 {card['name']} 체크리스트 — {card['score']}/{card['max']}점 ({card['pct']}%)**"]
    if card["deal_breakers"]:
        L.append("")
        L.append("🚫 **결격 사유 (매수 금지):**")
        for b in card["deal_breakers"]:
            L.append(f"· #{b['no']} {b['q']} — {b['detail']}")
    else:
        L.append("✅ 결격 사유 없음")
    for layer_key, title in (("market", "시장 (오늘)"), ("stock", "종목")):
        lay = card[layer_key]
        L += ["", f"**{title} — {lay['score']}/{lay['max']}**"]
        by_cat: dict[str, list] = {}
        for it in lay["items"]:
            by_cat.setdefault(it["category"], []).append(it)
        for cat, its in by_cat.items():
            for it in its:
                mark = "✅" if it["ok"] else "❌" if it["ok"] is False else "❓"
                L.append(f"{mark} #{it['no']} {it['q']} — {it['detail']}")
    if card.get("unknown"):
        L += ["", f"❓ 확인 불가 {len(card['unknown'])}개 항목은 데이터 연결 후 자동 활성화됩니다."]
    L += ["", "**🧑 본인 확인 (에이전트가 대신 못 하는 항목):**"]
    L += [f"· {r}" for r in card["human_reminders"]]
    L += ["", "※ 체크리스트는 조건 점검용이며, 방향 판단은 3가지 방법(살까? 질문)과 함께 보세요."]
    return "\n".join(L)


def render_en(card: dict[str, Any]) -> str:
    """Full scorecard in English (item questions translated; live details may keep
    Korean data-source fragments, numbers dominate)."""
    try:
        from services.stock_resolver import display_name_en
        _nm = display_name_en(card["ticker"]) or card["name"]
    except Exception:
        _nm = card["name"]
    L = [f"**📋 {_nm} checklist — {card['score']}/{card['max']} pts ({card['pct']}%)**"]
    if card["deal_breakers"]:
        L += ["", "🚫 **Deal-breakers (no buying):**"]
        for b in card["deal_breakers"]:
            L.append(f"- #{b['no']} {b['detail']}")
    else:
        L.append("✅ No deal-breakers")
    for layer_key, title in (("market", "Market (today)"), ("stock", "Stock")):
        lay = card[layer_key]
        L += ["", f"**{title} — {lay['score']}/{lay['max']}**"]
        for it in lay["items"]:
            mark = "✅" if it["ok"] else "❌" if it["ok"] is False else "❓"
            L.append(f"{mark} #{it['no']} {it.get('q_en') or it['q']} — {it['detail']}")
    if card.get("unknown"):
        L += ["", f"❓ {len(card['unknown'])} item(s) can't be checked yet — they activate as data sources connect."]
    L += ["", "**🧑 Self-check (things the agent can't check for you):**"]
    L += [f"- {r}" for r in card.get("human_reminders_en") or HUMAN_REMINDERS_EN]
    L += ["", "Note: the checklist verifies CONDITIONS; ask 'should I buy X?' for the 3-method direction call."]
    return "\n".join(L)


def render_market_en(db) -> str:
    """Market pre-flight only, in English."""
    m = market_preflight(db)
    ok_day = not m["deal_breakers"]
    L = [f"**📋 Today's market check — {m['score']}/{m['max']} pts**",
         ("✅ Conditions are OK for trading today." if ok_day
          else "🚫 Better to skip new buying today."), ""]
    for it in m["items"]:
        mark = "✅" if it["ok"] else "❌" if it["ok"] is False else "❓"
        L.append(f"{mark} #{it['no']} {it.get('q_en') or it['q']} — {it['detail']}")
    L += ["", "**🧑 Self-check:**"] + [f"- {r}" for r in HUMAN_REMINDERS_EN]
    L += ["", 'Per-stock check: ask "<stock name> checklist" · full 100-item list: "show all checklist".']
    return "\n".join(L)


def render_market_ko(db) -> str:
    """Market pre-flight only (no stock named): '오늘 단타 하기 좋은 날인가?'"""
    m = market_preflight(db)
    ok_day = not m["deal_breakers"]
    L = [f"**📋 오늘의 시장 체크 — {m['score']}/{m['max']}점**",
         ("✅ 오늘은 매매 가능한 환경입니다." if ok_day else "🚫 오늘은 신규 매수를 쉬는 게 좋은 날입니다."), ""]
    for it in m["items"]:
        mark = "✅" if it["ok"] else "❌" if it["ok"] is False else "❓"
        L.append(f"{mark} #{it['no']} {it['q']} — {it['detail']}")
    L += ["", "**🧑 본인 확인:**"] + [f"· {r}" for r in HUMAN_REMINDERS_KO]
    L += ["", "종목별 점검은 \"종목명 체크리스트\" · 전체 100문항은 \"체크리스트 전체 보여줘\"로 물어보세요."]
    return "\n".join(L)


# ---------------------------------------------------------------- full 100-item list
# The boss handed over his ORIGINAL paper checklist verbatim (2026-08-24). It lives in
# data/checklist_100.json — his exact KO wording + EN translation, with `auto`
# marking the 90 items the platform checks or enforces automatically (scoring
# columns, doors, ladder/stop laws, order-book watch, news intern, nightly
# audits); the 10 🧑 items are the trader's own mindset/journal/allocation
# (reclassified 2026-08-25 on the boss's order). The chatbot's "체크리스트 전체"
# intent renders it; editing the JSON changes the answer without touching code.

_FULL_FILE = Path(__file__).resolve().parent.parent / "data" / "checklist_100.json"


def full_checklist() -> dict[str, Any]:
    """The boss's verbatim 100-item list ({categories, items}); cached 10 min so a
    JSON edit shows up without a restart. Items always sorted 1..100 (boss
    2026-08-25: "it should count +1 like 1,2,3...100, not without order")."""
    def _load9():
        d = json.loads(_FULL_FILE.read_text(encoding="utf-8"))
        try:
            d["items"] = sorted(d["items"], key=lambda i: i.get("no") or 0)
        except Exception:
            pass
        return d
    return _cached("full100", 600, _load9)


def render_full_ko() -> str:
    data = full_checklist()
    items = data["items"]
    n_auto = sum(1 for i in items if i.get("auto"))
    _tot9 = sum(float(i.get("score") or 0) for i in items)
    L = [f"**📋 매매 체크리스트 전체 {len(items)}문항** (🤖 자동 점검 {n_auto} · 🧑 본인 확인 {len(items) - n_auto} · 합산제 총 {_tot9:g}점)"]
    by_cat: dict[str, list] = {}
    for it in items:
        by_cat.setdefault(it["cat"], []).append(it)
    for cat, its in by_cat.items():
        _cs9 = sum(float(i.get("score") or 0) for i in its)
        L += ["", f"**{cat} ({its[0]['no']}~{its[-1]['no']}) — 소계 {_cs9:g}점**"]
        for it in its:
            _sc9 = float(it.get("score") or 0)
            _lb9 = f"[{_sc9:g}점] " if _sc9 else "[—] "
            L.append(f"{'🤖' if it.get('auto') else '🧑'} {it['no']}. {_lb9}{it['q']}")
    L += ["", "🤖 = 에이전트가 실시간 데이터로 자동 점검 (\"종목명 체크리스트\"로 확인) · 🧑 = 본인이 지켜야 하는 원칙",
          "점수 = 항목별 가중치(측정+논문 근거, 2026-08-26 확정). 최종 점수는 통과 항목의 단순 합산.",
          "오늘의 시장 점검은 \"체크리스트\", 오늘 매매할 종목 추천은 \"오늘 뭐 살까?\"로 물어보세요."]
    return "\n".join(L)


def render_full_en() -> str:
    data = full_checklist()
    items = data["items"]
    cats_en = data.get("categories") or {}
    n_auto = sum(1 for i in items if i.get("auto"))
    _tot9 = sum(float(i.get("score") or 0) for i in items)
    L = [f"**📋 The full {len(items)}-item trading checklist** (🤖 auto-checked {n_auto} · 🧑 self-check {len(items) - n_auto} · sum-scored, {_tot9:g} pts total)"]
    by_cat: dict[str, list] = {}
    for it in items:
        by_cat.setdefault(it["cat"], []).append(it)
    for cat, its in by_cat.items():
        _cs9 = sum(float(i.get("score") or 0) for i in its)
        L += ["", f"**{cats_en.get(cat, cat)} ({its[0]['no']}–{its[-1]['no']}) — subtotal {_cs9:g} pts**"]
        for it in its:
            _sc9 = float(it.get("score") or 0)
            _lb9 = f"[{_sc9:g} pts] " if _sc9 else "[—] "
            L.append(f"{'🤖' if it.get('auto') else '🧑'} {it['no']}. {_lb9}{it.get('q_en') or it['q']}")
    L += ["", "🤖 = the agent checks this automatically from live data (ask \"<stock> checklist\") · 🧑 = your own discipline",
          "Points = per-item weights (measured + literature evidence, fixed 2026-08-26). The final score is the plain SUM of passed items.",
          "Today's market check: ask \"checklist\". Today's stock recommendation: ask \"what should I buy today?\"."]
    return "\n".join(L)


def render_items(nos: list[int], en: bool = False) -> str:
    """Specific checklist items by number — "59번이 뭐야?" / "what is the 59th item?"."""
    data = full_checklist()
    cats_en = data.get("categories") or {}
    found = [it for it in data["items"] if it["no"] in set(nos)]
    if not found:
        return ("해당 번호의 체크리스트 항목이 없습니다 (1~100)." if not en
                else "No checklist item with that number (1–100).")
    L = []
    for it in found[:15]:
        cat = cats_en.get(it["cat"], it["cat"]) if en else it["cat"]
        if en:
            L += [f"**{it['no']}. {it.get('q_en') or it['q']}**",
                  f"· Category: {cat} · " + ("🤖 auto-checked by the agent — ask \"<stock> checklist\" for the live result"
                                             if it.get("auto") else
                                             "🧑 self-check item (your own discipline — the agent reminds, you confirm)"),
                  f"· KO: {it['q']}", ""]
        else:
            L += [f"**{it['no']}. {it['q']}**",
                  f"· 분류: {cat} · " + ("🤖 자동 점검 항목 — \"종목명 체크리스트\"로 실시간 확인 가능"
                                        if it.get("auto") else
                                        "🧑 본인 확인 항목 (에이전트가 대신 판단하지 않는 원칙)"),
                  f"· EN: {it.get('q_en') or ''}", ""]
    return "\n".join(L).rstrip()


def render_category(cat_key: str, en: bool = False) -> str:
    """One category of the 100 — "준비 항목 보여줘" / "market checklist items"."""
    data = full_checklist()
    cats_en = data.get("categories") or {}
    its = [it for it in data["items"] if it["cat"] == cat_key]
    if not its:
        return render_full_en() if en else render_full_ko()
    title = cats_en.get(cat_key, cat_key) if en else cat_key
    L = [f"**📋 {title} ({its[0]['no']}–{its[-1]['no']}) — {len(its)}" + (" items**" if en else "문항**"), ""]
    for it in its:
        L.append(f"{'🤖' if it.get('auto') else '🧑'} {it['no']}. {(it.get('q_en') or it['q']) if en else it['q']}")
    L += ["", ("🤖 = auto-checked from live data · 🧑 = self-check" if en
               else "🤖 = 실시간 자동 점검 · 🧑 = 본인 확인")]
    return "\n".join(L)


# query keyword → category key; used by the chat intent (gated on an explicit
# list word so "market checklist" alone still means the live market pre-flight)
CATEGORY_ALIASES = (
    ("준비", "준비"), ("preparation", "준비"), ("prep ", "준비"),
    ("이슈", "이슈/수급"), ("수급", "이슈/수급"), ("catalyst", "이슈/수급"), ("flow", "이슈/수급"),
    ("실행", "실행/관리"), ("관리", "실행/관리"), ("execution", "실행/관리"), ("management", "실행/관리"),
    ("시장", "시장"), ("market", "시장"),
    ("종목", "종목"), ("stock", "종목"),
)


def summary_line(card: dict[str, Any], en: bool = False) -> str:
    """One-line summary appended to decide()/scalp answers."""
    if en:
        s = f"📋 Checklist {card['score']}/{card['max']} ({card['pct']}%)"
        if card["deal_breakers"]:
            s += " · 🚫 " + "; ".join(f"#{b['no']} {b['detail']}" for b in card["deal_breakers"][:2])
        else:
            s += " · no deal-breakers"
        return s
    s = f"📋 체크리스트 {card['score']}/{card['max']}점 ({card['pct']}%)"
    if card["deal_breakers"]:
        s += " · 🚫 결격: " + "; ".join(f"#{b['no']} {b['detail']}" for b in card["deal_breakers"][:2])
    else:
        s += " · 결격 없음"
    return s
