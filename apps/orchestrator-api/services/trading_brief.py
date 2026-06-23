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
    return out


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
    if signal == "SELL":
        out["entry"] = round(close)                       # 매도/청산 now
        out["target"] = round(sup)                        # 지지까지 하락 목표
        out["stop"] = round(res * 1.02)                   # 저항 돌파 시 손절
    else:                                                 # BUY or WATCH: buy-the-dip-at-support
        out["entry"] = round(min(close, sup * 1.03))      # 지지 부근 매수
        out["target"] = round(res)                        # 저항 목표
        out["stop"] = round(sup * 0.97)                   # 지지 이탈 손절
    out["rr"] = _rr(out.get("entry"), out.get("target"), out.get("stop"))
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


# ---- LIVE real-time signals (Kiwoom) — the day-trading layer --------------------
def realtime_for(ticker: str) -> dict[str, Any] | None:
    """Live order-book imbalance + intraday 수급 + program net from Kiwoom REST.
    Returns None if Kiwoom keys aren't set or the call fails — the brief still works
    without it (graceful degradation). Cached 20s inside kiwoom_rest."""
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
    close, entry = L.get("close"), L.get("entry")
    if not entry:                     # no actionable plan (e.g. ML HOLD)
        return {"buy_time": "신호 대기 (관망)", "buy_time_en": "Awaiting signal (watch)",
                "sell_time": None, "sell_time_en": None, "by": None}
    by = _add_trading_days(as_of, horizon)
    if advice == "SELL":
        buy_time = "반등(진입가) 부근 분할 매도/청산"
        buy_time_en = "Scale out near entry on a bounce"
        sell_time = f"지지(목표) 도달 시 · 예상 ~{horizon}거래일(≈{by})"
        sell_time_en = f"On reaching support · ~{horizon} trading days (≈{by})"
    elif advice == "BUY":
        if close and entry >= close:
            buy_time, buy_time_en = "지금~익일 시가 진입", "Now / next open"
        else:
            buy_time, buy_time_en = "진입가 도달 시 매수", "Buy when entry is hit"
        sell_time = f"목표 도달 시 청산 · 예상 ~{horizon}거래일(≈{by})"
        sell_time_en = f"On target · ~{horizon} trading days (≈{by})"
    else:                             # WATCH — analysis box plan (accumulate at support)
        buy_time, buy_time_en = "지지(진입가) 부근 매수 대기", "Wait to buy near support"
        sell_time = f"저항(목표) 도달 시 청산 · ~{horizon}거래일(≈{by})"
        sell_time_en = f"On resistance target · ~{horizon} trading days (≈{by})"
    return {"buy_time": buy_time, "buy_time_en": buy_time_en,
            "sell_time": sell_time, "sell_time_en": sell_time_en, "by": by}


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
def analysis_signal(card: dict, rt: dict | None) -> dict[str, Any]:
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

    # ±2 threshold so EOD-only data (daily 수급 + box, no live order book after market)
    # still produces actionable 매수/매도, not everything stuck on 관망.
    sig = "BUY" if score >= 2 else "SELL" if score <= -2 else "WATCH"
    label = {"BUY": "매수", "SELL": "매도", "WATCH": "관망"}[sig]
    label_en = {"BUY": "Buy", "SELL": "Sell", "WATCH": "Watch"}[sig]
    return {"signal": sig, "label": label, "label_en": label_en, "score": score,
            "reasons": reasons[:3] or ["뚜렷한 신호 없음 (관망)"],
            "reasons_en": reasons_en[:3] or ["No clear signal (watch)"]}


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

    # During market only: live Kiwoom 호가/수급, fetched in parallel, best-effort.
    rts: dict[str, Any] = {}
    if _kr_market_open():
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
        sig = analysis_signal(card, rt)
        levels = _box_levels(db, tk, sig["signal"])        # OWN levels for the signal
        timing = timing_plan(levels, sig["signal"], as_of, horizon)
        out[tk] = {"realtime": rt, "levels": levels, "flow": flows.get(tk),
                   "timing": timing, "market_open": _kr_market_open(), **sig}
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
        "flow_heatmap": heat,
        "news": news[:12],
        "disclaimer": ("ML 5일 예측 · 정확도 ~48%이나 BUY픽 평균 아웃퍼폼 · 보장 아님. "
                       "실시간 분봉/거래원/프로그램 수급은 Kiwoom 연동 후 제공."),
    }
