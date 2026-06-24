"""trading_brief — the unified short-term (단타) brief the Daily Trading UI renders.

Fuses everything we can serve WITHOUT extra credentials into one payload:
  • market regime      — KOSPI / USD-KRW / breadth (risk-on vs risk-off today)
  • picks + trade plan — BUY/SELL with 박스권 진입가/목표가/손절가 (support/resistance)
  • 수급 (who's buying) — latest 외국인/기관 net + 5d accumulation per stock
  • effective news     — impact-scored news + live DART disclosures (noise hidden)
  • honesty band       — the real backtest track record shown with every call

Pure reader over Supabase tables (model_predictions, stock_features_daily,
raw_daily_prices, raw_investor_flows, raw_news, raw_disclosures). No ML deps, so it
runs fine on Render. The granular real-time layer (거래원/program/분봉) lights up later
when Kiwoom is connected — this brief is the daily-resolution version of it.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

from sqlalchemy import text

from services import prediction_service as ps
from services.prediction_service import NAMES

# Featured stocks pinned to the FRONT of every list (both methods), in this order,
# even when the model says HOLD — the big names the user always wants to watch.
PRIORITY = ["000660", "035420", "005930"]   # SK하이닉스, NAVER, 삼성전자


# ---- market regime -------------------------------------------------------------
def market_regime(db) -> dict[str, Any]:
    r = db.execute(text(
        "SELECT mkt_kospi_ret5, mkt_kospi_vs_sma20, mkt_usdkrw_ret5, mkt_breadth, date "
        "FROM stock_features_daily WHERE mkt_breadth IS NOT NULL "
        "ORDER BY date DESC LIMIT 1")).first()
    if not r:
        return {"label_ko": "데이터 없음", "tone": "neutral"}
    k5 = float(r.mkt_kospi_ret5 or 0) * 100
    breadth = float(r.mkt_breadth or 0) * 100
    fx5 = float(r.mkt_usdkrw_ret5 or 0) * 100
    vs20 = float(r.mkt_kospi_vs_sma20 or 0) * 100
    risk_on = k5 > 0 and vs20 > 0 and breadth >= 50
    tone = "risk_on" if risk_on else ("risk_off" if (k5 < 0 and breadth < 45) else "mixed")
    label = {"risk_on": "위험선호 (강세)", "risk_off": "위험회피 (약세)",
             "mixed": "혼조"}[tone]
    won = "원화약세" if fx5 > 0.3 else ("원화강세" if fx5 < -0.3 else "환율보합")
    return {
        "date": str(r.date), "tone": tone, "label_ko": label,
        "kospi_ret5": round(k5, 2), "kospi_vs_sma20": round(vs20, 2),
        "usdkrw_ret5": round(fx5, 2), "breadth": round(breadth, 0), "won": won,
    }


# ---- 박스권 levels (support/resistance trade plan) ------------------------------
def _kr_market_open() -> bool:
    """KRX regular session: Mon-Fri 09:00-15:30 KST. During → Kiwoom live; after → Naver/EOD."""
    from datetime import datetime, timezone, timedelta
    n = datetime.now(timezone(timedelta(hours=9)))
    return n.weekday() < 5 and 540 <= (n.hour * 60 + n.minute) <= 930


def _recent_box(db, ticker: str):
    """(close, support, resistance, open) from the last 20 daily bars, or None."""
    rows = db.execute(text(
        "SELECT high, low, close, open FROM raw_daily_prices WHERE ticker=:t "
        "ORDER BY date DESC LIMIT 20"), {"t": ticker}).fetchall()
    if not rows:
        return None
    opn = float(rows[0].open) if rows[0].open is not None else None
    return (float(rows[0].close),
            min(float(r.low) for r in rows),      # support (박스권 하단)
            max(float(r.high) for r in rows),     # resistance (박스권 상단)
            opn)                                  # latest day's open (시가)


def _rr(entry, target, stop):
    if entry and target and stop and entry != stop:
        return round(abs(target - entry) / abs(entry - stop), 2)
    return None


def _move_pct(db, ticker: str, horizon: int = 5) -> float:
    """Realistic expected move over `horizon` days, as a % — from the stock's own
    realized volatility (realized_vol_20 is annualized %). Used to set ACHIEVABLE
    targets instead of the far box edge (the scorekeeper showed full-box targets are
    hit only ~12% of the time in 5 days)."""
    import math
    r = db.execute(text(
        "SELECT realized_vol_20 FROM stock_features_daily WHERE ticker=:t "
        "AND realized_vol_20 IS NOT NULL ORDER BY date DESC LIMIT 1"), {"t": ticker}).first()
    vol = float(r[0]) if r and r[0] is not None else 25.0          # annualized %
    return max(1.5, min(12.0, vol * math.sqrt(horizon / 252.0)))   # 1σ horizon move %, clamped


def _ml_levels(db, ticker: str, advice: str, exp_low, exp_high) -> dict[str, Any]:
    """METHOD 1 (ML): trade plan from the MODEL's expected move (not the chart box).
    Entry = market, Target = close ± expected-move%, Stop = half-band risk."""
    box = _recent_box(db, ticker)
    if not box:
        return {}
    close, sup, res, opn = box
    out = {"close": round(close), "open": round(opn) if opn else None,
           "support": round(sup), "resistance": round(res)}
    band_up = abs(exp_high) if exp_high else 3.0
    band_dn = abs(exp_low) if exp_low else 3.0
    if advice == "BUY":
        out["entry"] = round(close)
        out["target"] = round(close * (1 + band_up / 100))            # model's upside
        out["stop"] = round(close * (1 - band_dn / 100 * 0.5))        # half-band risk
    elif advice == "SELL":
        out["entry"] = round(close)
        out["target"] = round(close * (1 - band_dn / 100))
        out["stop"] = round(close * (1 + band_up / 100 * 0.5))
    else:                                          # HOLD — range reference (지지/저항)
        out["entry"] = round(sup)                  # 참고: 지지 부근 매수
        out["target"] = round(res)                 # 참고: 저항 목표
        out["stop"] = round(sup * 0.97)
    out["rr"] = _rr(out.get("entry"), out.get("target"), out.get("stop"))
    _add_zones_around(out)                          # buy/sell price INTERVALS around entry/target
    return out


def _add_zones_around(out: dict, buy_band: float = 0.008, sell_band: float = 0.008):
    """Turn the single entry/target points into BUY and SELL price intervals (±band%)."""
    e, t = out.get("entry"), out.get("target")
    if e:
        out["buy_lo"], out["buy_hi"] = round(e * (1 - buy_band)), round(e * (1 + buy_band * 0.4))
    if t:
        lo, hi = sorted([round(t * (1 - sell_band * 0.4)), round(t * (1 + sell_band))])
        out["sell_lo"], out["sell_hi"] = lo, hi


def _box_levels(db, ticker: str, signal: str) -> dict[str, Any]:
    """METHOD 2 (Analysis): trade plan from 박스권 지지/저항 — ALWAYS returns levels
    (even for WATCH = accumulate-near-support plan), so every stock shows a result and
    the numbers differ from the ML method's expected-move plan."""
    box = _recent_box(db, ticker)
    if not box:
        return {}
    close, sup, res, opn = box
    out = {"close": round(close), "open": round(opn) if opn else None,
           "support": round(sup), "resistance": round(res)}
    # REALISTIC target: a 1σ horizon move (vol-based), capped by the box edge — so it's
    # actually reachable in the window (fixes the ~12% target-hit problem).
    mv = _move_pct(db, ticker) / 100.0
    entry = round(min(close, sup * 1.03))                  # 지지 부근 매수 (buy near support)
    if signal == "SELL":
        out["entry"] = round(close)
        out["target"] = round(max(sup, close * (1 - 0.9 * mv)))   # realistic down move, floored at support
        out["stop"] = round(min(res * 1.02, close * (1 + 0.6 * mv)))
    else:                                                 # BUY or WATCH
        out["entry"] = entry
        out["target"] = round(min(res, entry * (1 + 0.9 * mv)))   # realistic up move, capped at resistance
        out["stop"] = round(max(sup * 0.97, entry * (1 - 0.6 * mv)))
    out["rr"] = _rr(out.get("entry"), out.get("target"), out.get("stop"))
    # Price INTERVALS: tight zones around the (now realistic) entry/target.
    _add_zones_around(out, buy_band=0.008, sell_band=0.008)
    return out


# ---- 수급 (who's buying) -------------------------------------------------------
def _flow(db, ticker: str) -> dict[str, Any]:
    r = db.execute(text(
        "SELECT foreign_net, inst_net, foreign_hold_pct, date FROM raw_investor_flows "
        "WHERE ticker=:t ORDER BY date DESC LIMIT 1"), {"t": ticker}).first()
    c5 = db.execute(text(
        "SELECT SUM(foreign_net) f, SUM(inst_net) i FROM (SELECT foreign_net, inst_net "
        "FROM raw_investor_flows WHERE ticker=:t ORDER BY date DESC LIMIT 5) q"),
        {"t": ticker}).first()
    if not r:
        return {}
    fn, inn = float(r.foreign_net or 0), float(r.inst_net or 0)
    f5 = float(c5.f or 0) if c5 else 0
    i5 = float(c5.i or 0) if c5 else 0
    def arrow(v): return "▲" if v > 0 else ("▼" if v < 0 else "－")
    smart5 = f5 + i5
    tag = ("강력매집" if smart5 > 0 and f5 > 0 and i5 > 0 else
           "분산매도" if smart5 < 0 and f5 < 0 and i5 < 0 else "혼조")
    return {"date": str(r.date), "foreign": arrow(fn), "inst": arrow(inn),
            "foreign_net": int(fn), "inst_net": int(inn),
            "foreign_5d": int(f5), "inst_5d": int(i5),
            "foreign_hold_pct": float(r.foreign_hold_pct) if r.foreign_hold_pct else None,
            "tag": tag, "tag_en": TAG_EN.get(tag, tag)}


# Tag enum -> English (so the heatmap isn't mixed-language in EN mode)
TAG_EN = {"강력매집": "Strong accumulation", "분산매도": "Distribution", "혼조": "Mixed"}


def _heatmap_batch(db) -> list[dict]:
    """Whole-universe 수급 in TWO queries (latest + 5d net per ticker) instead of 37
    per-ticker calls — the main page-load speedup. Featured stocks pinned first."""
    latest = {r.ticker: r for r in db.execute(text(
        "SELECT DISTINCT ON (ticker) ticker, foreign_net, inst_net "
        "FROM raw_investor_flows ORDER BY ticker, date DESC"))}
    sums = {r.ticker: r for r in db.execute(text(
        "SELECT ticker, SUM(foreign_net) f5, SUM(inst_net) i5 FROM ("
        "  SELECT ticker, foreign_net, inst_net, ROW_NUMBER() OVER "
        "  (PARTITION BY ticker ORDER BY date DESC) rn FROM raw_investor_flows) q "
        "WHERE rn <= 5 GROUP BY ticker"))}

    def arrow(v): return "▲" if v > 0 else ("▼" if v < 0 else "－")
    order = PRIORITY + [tk for tk in NAMES if tk not in PRIORITY]
    heat = []
    for tk in order:
        r = latest.get(tk)
        if not r:
            continue
        fn, inn = float(r.foreign_net or 0), float(r.inst_net or 0)
        s = sums.get(tk)
        f5 = float(s.f5 or 0) if s else 0
        i5 = float(s.i5 or 0) if s else 0
        smart5 = f5 + i5
        tag = ("강력매집" if smart5 > 0 and f5 > 0 and i5 > 0 else
               "분산매도" if smart5 < 0 and f5 < 0 and i5 < 0 else "혼조")
        heat.append({"ticker": tk, "name": NAMES[tk],
                     "foreign": arrow(fn), "inst": arrow(inn),
                     "foreign_net": int(fn), "inst_net": int(inn),
                     "tag": tag, "tag_en": TAG_EN.get(tag, tag)})
    return heat


# ---- LIVE real-time signals — read PC-collected snapshot, fallback direct Kiwoom ---
def _read_snapshots(db, tickers, max_age_sec: int = 240) -> dict[str, Any]:
    """Read fresh live signals from realtime_snapshot (written by the PC collector that
    has the 실전/registered IP). This is how the WEBSITE (Render) gets live data without
    calling Kiwoom itself. Stale rows (collector stopped / after market) are skipped."""
    try:
        rows = db.execute(text(
            "SELECT ticker, price, imbalance, best_bid, best_ask, foreign_net, inst_net, "
            "fin_invest, program_net, env, ts, EXTRACT(EPOCH FROM (now()-ts)) AS age "
            "FROM realtime_snapshot WHERE ticker = ANY(:t)"), {"t": list(tickers)}).fetchall()
    except Exception:
        return {}
    out = {}
    for r in rows:
        if r.age is None or r.age > max_age_sec:
            continue
        imb = float(r.imbalance) if r.imbalance is not None else None
        pressure = ("매수우위" if imb is not None and imb > 0.15 else
                    "매도우위" if imb is not None and imb < -0.15 else "균형")
        pressure_en = ("Bid-heavy" if imb is not None and imb > 0.15 else
                       "Ask-heavy" if imb is not None and imb < -0.15 else "Balanced")
        ii = lambda v: int(v) if v is not None else None
        out[r.ticker] = {
            "live": True, "env": r.env, "imbalance": imb,
            "pressure": pressure, "pressure_en": pressure_en,
            "best_bid": ii(r.best_bid), "best_ask": ii(r.best_ask),
            "foreign": ii(r.foreign_net), "institution": ii(r.inst_net),
            "fin_invest": ii(r.fin_invest), "program_net": ii(r.program_net),
            "price": float(r.price) if r.price is not None else None,
            "as_of": str(r.ts)[11:16],
        }
    return out


def stock_detail(db, ticker: str) -> dict[str, Any]:
    """Rich single-stock detail for the click-through detail view (both methods).
    OHLC / 등락% / 거래량 / 52주 고저 / NXT 시간외 from Naver (works on Render, ~live during
    market, EOD after). LIVE 호가·수급·program from Kiwoom 실전 via the PC snapshot (the
    real edge). Plus 개별주식 선물·옵션 when available. Source label: market hours = 키움
    실전+네이버, after = 네이버."""
    from services import naver_stock
    from services import prediction_service as ps
    raw = str(ticker or "").strip()
    code = raw if (raw.isdigit() and len(raw) == 6) else None
    if not code:
        rev = {v: k for k, v in ps.NAMES.items()}
        code = rev.get(raw) or next((c for nm, c in rev.items() if raw and (raw in nm or nm in raw)), None)
    if not code:
        return {"ok": False, "error": f"'{raw}' 종목을 찾지 못했습니다"}
    name = ps.NAMES.get(code, code)
    import time as _t

    def _naver(fn, *a):                                   # space calls — Naver throttles rapid-fire
        for attempt in range(2):
            try:
                r = fn(*a)
                if r:
                    return r
            except Exception:
                pass
            _t.sleep(0.35)
        return fn(*a)
    hist = _naver(naver_stock.daily_history, code, 60) or []   # Naver /price rejects >~90
    _t.sleep(0.25)
    q = naver_stock.realtime_quote(code) or {}
    _t.sleep(0.25)
    flows = naver_stock.investor_flows(code, 1) or []
    rt = realtime_for(code, db=db) or {}
    today = hist[0] if hist else {}                      # today's full candle (OHLCV)
    highs = [h["high"] for h in hist if h.get("high") is not None]
    lows = [h["low"] for h in hist if h.get("low") is not None]
    # live price from realtime_quote (fresher), OHLC/volume from today's candle (the
    # basic endpoint leaves intraday O/H/L/V null, but the daily candle has them).
    price = q.get("price") if q.get("price") is not None else today.get("close")
    chg = q.get("change_pct") if q.get("change_pct") is not None else today.get("change_pct")
    _open = q.get("open") if q.get("open") is not None else today.get("open")
    _high = q.get("high") if q.get("high") is not None else today.get("high")
    _low = q.get("low") if q.get("low") is not None else today.get("low")
    _vol = q.get("volume") if q.get("volume") is not None else today.get("volume")
    prev_close = None
    if price is not None and chg not in (None, 0):
        try:
            prev_close = round(price / (1 + chg / 100.0))
        except Exception:
            prev_close = None
    mopen = _kr_market_open()
    fl0 = flows[0] if flows else {}
    return {
        "ok": True, "ticker": code, "name": name, "market_open": mopen,
        "source": ("키움 실전 + 네이버" if mopen else "네이버 (장마감)"),
        "as_of": q.get("as_of"),
        # price block
        "price": price, "change_pct": chg, "prev_close": prev_close,
        "open": _open, "high": _high, "low": _low,
        "volume": _vol,
        "period_high": max(highs) if highs else None,        # ~3개월 (Naver 60일)
        "period_low": min(lows) if lows else None,
        "period_label": "최근 3개월",
        # daily OHLCV series (oldest-first) for our own candlestick chart — always works
        # for KR stocks (no TradingView symbol-access popup).
        "candles": [
            {"time": h["date"], "open": h.get("open"), "high": h.get("high"),
             "low": h.get("low"), "close": h.get("close"), "volume": h.get("volume")}
            for h in reversed(hist)
            if h.get("date") and h.get("close") is not None
            and h.get("open") is not None and h.get("high") is not None and h.get("low") is not None
        ],
        "nxt_price": q.get("nxt_price"), "nxt_change_pct": q.get("nxt_change_pct"),
        "nxt_status": q.get("nxt_status"),
        # supply/demand (daily, Naver)
        "foreign_net": fl0.get("foreign"), "organ_net": fl0.get("organ"),
        "individual_net": fl0.get("individual"), "foreign_hold": fl0.get("foreign_hold"),
        # LIVE Kiwoom signals (snapshot)
        "live": rt.get("live", False), "env": rt.get("env"),
        "best_bid": rt.get("best_bid"), "best_ask": rt.get("best_ask"),
        "imbalance": rt.get("imbalance"), "pressure": rt.get("pressure"),
        "rt_foreign": rt.get("foreign"), "rt_institution": rt.get("institution"),
        "rt_fin_invest": rt.get("fin_invest"), "program_net": rt.get("program_net"),
        # derivatives (per-stock, when available)
        "derivatives": _stock_derivatives(code),
    }


def _stock_derivatives(code: str) -> dict[str, Any]:
    """개별주식선물 (single-stock futures) daily summary for this underlying — REAL data
    from KRX (data.krx.co.kr) via services/krx_stock_futures, summed across contract
    months: 거래량(계약), 거래대금(원), 미결제약정(계약). Needs KRX_ID/KRX_PW (a free KRX
    정보데이터시스템 account) on the server; returns {available: False} without creds or when
    the stock has no listed single-stock futures. NOTE: per-stock 옵션(call/put) is listed
    for only ~40 stocks and has no fetcher yet → reported as 미지원."""
    fut, src = None, None
    # 1) KIS (한국투자증권) — no KRX login needed; already used by the Kiwoom report.
    try:
        from services import kis_derivatives as kisd
        fut = kisd.stock_futures(code)
        if fut:
            src = "KIS"
    except Exception:
        fut = None
    # 2) Fallback: KRX 정보데이터시스템 (needs KRX_ID/KRX_PW).
    if not fut:
        try:
            from services import krx_stock_futures as ksf
            fut = ksf.stock_futures(code)
            if fut:
                src = "KRX"
        except Exception:
            fut = None
    if isinstance(fut, dict) and (fut.get("volume") or fut.get("open_interest")):
        return {
            "available": True, "type": "개별주식선물", "source": src,
            "date": fut.get("date"),
            "volume": fut.get("volume"),                 # 거래량 (계약수)
            "value": fut.get("value"),                   # 거래대금 (원)
            "open_interest": fut.get("open_interest"),   # 미결제약정 (계약수)
            "options": "미지원",                          # per-stock options not fetched
        }
    return {"available": False, "note": "개별주식선물 미상장 또는 데이터 미연동 (KIS/KRX)",
            "options": "미지원"}


def realtime_for(ticker: str, db=None) -> dict[str, Any] | None:
    """Live signals for ONE stock — snapshot table first (works on Render), then direct
    Kiwoom (works on the registered PC). Returns None if neither is available."""
    if db is not None:
        snap = _read_snapshots(db, [ticker]).get(ticker)
        if snap:
            return snap
    return _realtime_impl(ticker, with_program=True)


def _realtime_impl(ticker: str, with_program: bool = True) -> dict[str, Any] | None:
    try:
        from services import kiwoom_rest as kr
        if kr._token() is None:          # no creds / unreachable -> skip cleanly
            return None
        ob = kr.order_book(ticker) or {}
        fl = kr.investor_flows(ticker) or {}
        pr = (kr.program_trade(ticker) or {}) if with_program else {}
        imb = ob.get("imbalance")
        pressure = ("매수우위" if imb is not None and imb > 0.15 else
                    "매도우위" if imb is not None and imb < -0.15 else "균형")
        pressure_en = ("Bid-heavy" if imb is not None and imb > 0.15 else
                       "Ask-heavy" if imb is not None and imb < -0.15 else "Balanced")
        base = getattr(kr, "_active_base", "") or ""        # which env actually authed
        env = "모의" if "mockapi" in base else "실전"
        return {
            "live": True, "env": env, "as_of": fl.get("date"),
            "imbalance": imb, "pressure": pressure, "pressure_en": pressure_en,
            "best_bid": ob.get("best_bid"), "best_ask": ob.get("best_ask"),
            "foreign": fl.get("foreign"), "institution": fl.get("institution"),
            "fin_invest": fl.get("fin_invest"), "individual": fl.get("individual"),
            "program_net": pr.get("net_amt"), "price": fl.get("price"),
        }
    except Exception:
        return None


# ---- buy/sell TIMING (price + when) — both methods ------------------------------
def _add_trading_days(start_iso: Optional[str], n: int) -> str:
    try:
        d = date.fromisoformat(start_iso) if start_iso else date.today()
    except (ValueError, TypeError):
        d = date.today()
    added = 0
    while added < n:
        d += timedelta(days=1)
        if d.weekday() < 5:           # skip Sat/Sun
            added += 1
    return d.isoformat()


def timing_plan(levels: dict, advice: str, as_of: Optional[str], horizon: int) -> dict[str, Any]:
    """Turn a signal into WHEN to act, not just at what price. For a daily/5d model the
    grain is days; live intraday refines this once 실전 keys feed minute data."""
    L = levels or {}
    entry = L.get("entry")
    if not entry:                     # no actionable plan (e.g. ML HOLD)
        return {"buy_time": "신호 대기 (관망)", "buy_time_en": "Awaiting signal (watch)",
                "sell_time": None, "sell_time_en": None, "by": None}
    # TIME INTERVALS (windows), not single points.
    buy_from = _add_trading_days(as_of, 1)                       # next session
    buy_to = _add_trading_days(as_of, 2)
    sell_from = _add_trading_days(as_of, max(2, horizon - 2))    # back half of the horizon
    sell_to = _add_trading_days(as_of, horizon)
    md = lambda d: d[5:]                                         # MM-DD
    buy_time = f"{md(buy_from)}~{md(buy_to)} 매수"
    buy_time_en = f"Buy {md(buy_from)}~{md(buy_to)}"
    sell_time = f"{md(sell_from)}~{md(sell_to)} 매도(목표 도달 시)"
    sell_time_en = f"Sell {md(sell_from)}~{md(sell_to)} (on target)"
    return {"buy_time": buy_time, "buy_time_en": buy_time_en,
            "sell_time": sell_time, "sell_time_en": sell_time_en,
            "buy_from": buy_from, "buy_to": buy_to,
            "sell_from": sell_from, "sell_to": sell_to, "by": sell_to}


# ---- per-stock card ------------------------------------------------------------
def stock_card(db, ticker: str, horizon: int = 5, live: bool = False,
               as_of: Optional[str] = None) -> dict[str, Any]:
    pred = ps.get_ticker(db, ticker, horizon) or {}
    advice = pred.get("advice", "HOLD")
    levels = _ml_levels(db, ticker, advice,
                        pred.get("expected_low_pct"), pred.get("expected_high_pct"))
    return {
        "ticker": ticker, "name": NAMES.get(ticker, ticker),
        "advice": advice, "confidence": pred.get("confidence"),
        "direction": pred.get("direction"),
        "model": pred.get("model"),                       # best algorithm for THIS stock
        "backtest_acc": pred.get("backtest_acc"),
        "expected_low_pct": pred.get("expected_low_pct"),
        "expected_high_pct": pred.get("expected_high_pct"),
        "reasoning": pred.get("reasoning"),
        "levels": levels,
        "timing": timing_plan(levels, advice, as_of, horizon),
        "flow": _flow(db, ticker),
        "realtime": realtime_for(ticker) if live else None,
    }


# ---- METHOD 2: rule-based analysis signal (the analyst's brain) -----------------
# Independent of the ML model. Scores each stock from the signals the day-trader in
# the video actually reads: 호가 imbalance + 실시간/일별 수급 + 박스권 position.
def analysis_signal(card: dict, rt: dict | None, news: int = 0,
                    regime_tone: str = "mixed") -> dict[str, Any]:
    L = card.get("levels") or {}
    flow = card.get("flow") or {}
    score = 0
    reasons: list[str] = []       # Korean
    reasons_en: list[str] = []    # English (so the EN UI isn't mixed)

    def add(pts, ko, en):
        nonlocal score
        score += pts
        reasons.append(ko); reasons_en.append(en)

    # 1) 호가 매수/매도 압력 (live order book) — strongest intraday tell
    imb = (rt or {}).get("imbalance")
    if imb is not None:
        if imb > 0.15:
            add(2, f"호가 매수우위 {round(imb*100)}%", f"Order book bid-heavy {round(imb*100)}%")
        elif imb < -0.15:
            add(-2, f"호가 매도우위 {round(abs(imb)*100)}%", f"Order book ask-heavy {round(abs(imb)*100)}%")

    # 2) 실시간 수급 (외국인+기관+금투 net)
    if rt:
        net = (rt.get("foreign") or 0) + (rt.get("institution") or 0) + (rt.get("fin_invest") or 0)
        if net > 0:
            add(1, "실시간 수급 순매수", "Live flows net buying")
        elif net < 0:
            add(-1, "실시간 수급 순매도", "Live flows net selling")

    # 3) 일별 수급 판정 (외국인/기관 누적)
    tag = flow.get("tag")
    if tag == "강력매집":
        add(2, "외국인+기관 강력매집", "Foreign+inst strong accumulation")
    elif tag == "분산매도":
        add(-2, "외국인+기관 분산매도", "Foreign+inst distribution")

    # 4) 박스권 위치 — 저점은 매수, 고점은 매도(분할) 구간
    close, sup, res = L.get("close"), L.get("support"), L.get("resistance")
    if close and sup and res and res > sup:
        pos = (close - sup) / (res - sup)
        if pos < 0.33:
            add(1, "박스권 저점(지지) 매수구간", "Near box support (buy zone)")
        elif pos > 0.67:
            add(-1, "박스권 고점(저항) 매도구간", "Near box resistance (sell zone)")

    # 5) 영향있는 뉴스/공시 (#3) — only fires when there IS impactful news for this stock
    if news > 0:
        add(1, "긍정 뉴스/공시", "Positive news/filing")
    elif news < 0:
        add(-1, "부정 뉴스/공시", "Negative news/filing")

    # Regime-adjusted thresholds (#4): in a risk-off market, BUY needs more conviction
    # and SELL fires easier; in risk-on, the reverse. Base ±2.
    buy_t, sell_t = ({"risk_on": (1, -3), "risk_off": (3, -1)}
                     .get(regime_tone, (2, -2)))
    sig = "BUY" if score >= buy_t else "SELL" if score <= sell_t else "WATCH"
    label = {"BUY": "매수", "SELL": "매도", "WATCH": "관망"}[sig]
    label_en = {"BUY": "Buy", "SELL": "Sell", "WATCH": "Watch"}[sig]
    return {"signal": sig, "label": label, "label_en": label_en, "score": score,
            "reasons": reasons[:3] or ["뚜렷한 신호 없음 (관망)"],
            "reasons_en": reasons_en[:3] or ["No clear signal (watch)"]}


def _news_scores(db, lookback_days: int = 3) -> dict[str, int]:
    """Per-ticker news/DART direction: +1/0/-1 from impact-weighted recent news + DART.
    Mostly 0 (no impactful news) — only adds signal when a stock actually has news."""
    from services.news_impact import score as _nscore, classify as _classify
    agg: dict[str, float] = {}
    try:
        for r in db.execute(text(
            "SELECT ticker, title, snippet, sentiment FROM raw_news WHERE ticker IS NOT NULL "
            "AND ts > now() - (:d||' days')::interval"), {"d": lookback_days}):
            s = _nscore(r.title or "", r.snippet or "", r.sentiment)
            if s["impact"] >= 0.4 and s["direction"]:
                agg[r.ticker] = agg.get(r.ticker, 0) + s["impact"] * s["direction"]
        for r in db.execute(text(
            "SELECT ticker, title FROM raw_disclosures WHERE ticker IS NOT NULL "
            "AND ts > now() - (:d||' days')::interval"), {"d": lookback_days}):
            _, imp, ddir = _classify(r.title or "")
            if imp >= 0.5 and ddir:
                agg[r.ticker] = agg.get(r.ticker, 0) + imp * ddir * 0.5
    except Exception:
        return {}
    return {tk: (1 if v > 0.3 else -1 if v < -0.3 else 0) for tk, v in agg.items()}


def analysis_batch(db, tickers: list[str], horizon: int = 5) -> dict[str, Any]:
    """METHOD 2 (Analysis) — its OWN signal + 박스권 levels, independent of ML.

    Reliability: the signal + levels are computed from DB data (daily 수급 + 박스권 =
    Naver/EOD) which ALWAYS works, so every stock shows a result even after market /
    when Kiwoom is slow. DURING market hours we additionally fetch Kiwoom live 호가/수급
    in PARALLEL (best-effort, short timeout) to sharpen it — that's the only API work,
    and it can never block the result. This is what fixes the blank cards + the
    'both methods identical' fallback."""
    as_of = ps._latest_as_of(db, horizon)
    flows = {tk: _flow(db, tk) for tk in tickers}          # DB (Naver EOD) — always present
    regime_tone = market_regime(db).get("tone", "mixed")   # #4 regime filter
    news = _news_scores(db)                                 # #3 news/DART signal

    # Live signals: read the PC-collected snapshot (works on Render, no Kiwoom call,
    # no IP issue, fast). Stale/empty after market -> falls back to EOD signal.
    rts: dict[str, Any] = _read_snapshots(db, tickers)
    if not rts and _kr_market_open():
        # local fallback: direct Kiwoom (only works on the registered PC)
        try:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=5) as ex:
                futs = {ex.submit(_realtime_impl, tk, False): tk for tk in tickers}
                for f in as_completed(futs, timeout=12):
                    try:
                        rts[futs[f]] = f.result()
                    except Exception:
                        rts[futs[f]] = None
        except Exception:
            pass

    out: dict[str, Any] = {}
    for tk in tickers:
        rt = rts.get(tk)
        card = {"levels": _box_levels(db, tk, "WATCH"), "flow": flows.get(tk)}  # for box pos
        sig = analysis_signal(card, rt, news=news.get(tk, 0), regime_tone=regime_tone)
        levels = _box_levels(db, tk, sig["signal"])        # OWN levels for the signal
        timing = timing_plan(levels, sig["signal"], as_of, horizon)
        out[tk] = {"realtime": rt, "levels": levels, "flow": flows.get(tk),
                   "timing": timing, "market_open": _kr_market_open(), **sig}
    return out


# ---- CONSENSUS: stocks where BOTH methods agree (highest conviction) ------------
def consensus_picks(db, horizon: int = 5) -> list[dict[str, Any]]:
    """High-conviction list: stocks where the ML model AND the Analysis signal point
    the SAME way (both BUY or both SELL). Agreement of two independent methods is a
    stronger signal than either alone. Analysis uses the DB/EOD path (no Kiwoom), so
    it's deterministic and works after market."""
    as_of = ps._latest_as_of(db, horizon)
    regime_tone = market_regime(db).get("tone", "mixed")
    news = _news_scores(db)
    out = []
    for tk in NAMES:
        pred = ps.get_ticker(db, tk, horizon) or {}
        ml_adv = pred.get("advice", "HOLD")
        if ml_adv not in ("BUY", "SELL"):
            continue
        levels = _box_levels(db, tk, ml_adv)              # realistic 박스권 levels
        sig = analysis_signal({"levels": levels, "flow": _flow(db, tk)}, None,
                              news=news.get(tk, 0), regime_tone=regime_tone)
        if sig["signal"] != ml_adv:                        # must AGREE
            continue
        out.append({
            "ticker": tk, "name": NAMES.get(tk, tk), "signal": ml_adv,
            "ml": ml_adv, "analysis": sig["signal"], "label": sig["label"],
            "label_en": sig.get("label_en"), "confidence": pred.get("confidence"),
            "backtest_acc": pred.get("backtest_acc"), "model": pred.get("model"),
            "levels": levels, "timing": timing_plan(levels, ml_adv, as_of, horizon),
            "reasons": sig["reasons"], "reasons_en": sig.get("reasons_en"),
            "flow": _flow(db, tk),
        })
    return out


# ---- the full brief ------------------------------------------------------------
# Short server-side cache: the ML brief is daily data (changes once/day), so caching
# it makes the page open instantly instead of re-querying ~50 rows every load.
_brief_cache: dict[int, tuple[float, dict]] = {}
_BRIEF_TTL = 60.0


def brief(db, horizon: int = 5) -> dict[str, Any]:
    import time as _t
    hit = _brief_cache.get(horizon)
    if hit and (_t.time() - hit[0]) < _BRIEF_TTL:
        return hit[1]
    out = _build_brief(db, horizon)
    _brief_cache[horizon] = (_t.time(), out)
    return out


def _build_brief(db, horizon: int = 5) -> dict[str, Any]:
    summ = ps.summary(db, horizon=horizon)
    buy_tk = [p["ticker"] for p in summ.get("buys", [])]
    sell_tk = [p["ticker"] for p in summ.get("sells", [])]

    # Ordered display list: PRIORITY featured stocks first (always, even HOLD),
    # then BUY picks, then SELL picks. Deduped. Both method views render this.
    ordered, seen = [], set()
    for tk in PRIORITY + buy_tk + sell_tk:
        if tk in NAMES and tk not in seen:
            ordered.append(tk)
            seen.add(tk)
    picks = [stock_card(db, tk, horizon, as_of=summ.get("as_of")) for tk in ordered]
    picks_buy = [c for c in picks if c["advice"] == "BUY"]
    picks_sell = [c for c in picks if c["advice"] == "SELL"]

    # 수급 heatmap — ONE batched query (latest + 5d net per ticker) instead of 37 calls.
    heat = _heatmap_batch(db)

    # effective news (impact-scored) + live DART disclosures
    news: list[dict] = []
    try:
        from services.news_impact import effective_news
        news += effective_news(db, ticker=None, limit=8)
    except Exception as e:
        print(f"[brief] news_impact failed: {str(e)[:60]}")
    try:
        from services.dart_collector import recent as dart_recent
        news += dart_recent(db, ticker=None, limit=6)
    except Exception as e:
        print(f"[brief] dart failed: {str(e)[:60]}")
    news.sort(key=lambda x: (x.get("impact") or 0), reverse=True)

    return {
        "as_of": summ.get("as_of"),
        "horizon": horizon,
        "regime": market_regime(db),
        "counts": summ.get("counts", {}),
        "picks": picks,                 # ordered: featured first, then BUY, then SELL
        "buys": picks_buy,
        "sells": picks_sell,
        "consensus": consensus_picks(db, horizon),   # both methods agree = high conviction
        "flow_heatmap": heat,
        "news": news[:12],
        "disclaimer": ("ML 5일 예측 · 정확도 ~48%이나 BUY픽 평균 아웃퍼폼 · 보장 아님. "
                       "실시간 분봉/거래원/프로그램 수급은 Kiwoom 연동 후 제공."),
    }
