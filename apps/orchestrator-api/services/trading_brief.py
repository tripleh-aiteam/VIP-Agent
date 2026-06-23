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
def _levels(db, ticker: str, advice: str) -> dict[str, Any]:
    rows = db.execute(text(
        "SELECT high, low, close FROM raw_daily_prices WHERE ticker=:t "
        "ORDER BY date DESC LIMIT 20"), {"t": ticker}).fetchall()
    if not rows:
        return {}
    close = float(rows[0].close)
    hi = max(float(r.high) for r in rows)     # 20d resistance (박스권 상단)
    lo = min(float(r.low) for r in rows)      # 20d support   (박스권 하단)
    entry = target = stop = None
    if advice == "BUY":
        entry = round(close)                              # at/just-below market
        target = round(hi)                                # box top
        stop = round(max(lo, close * 0.97))               # box bottom or -3%
    elif advice == "SELL":
        entry = round(close)
        target = round(lo)
        stop = round(min(hi, close * 1.03))
    rr = None
    if entry and target and stop and entry != stop:
        rr = round(abs(target - entry) / abs(entry - stop), 2)   # reward:risk
    return {"close": round(close), "support": round(lo), "resistance": round(hi),
            "entry": entry, "target": target, "stop": stop, "rr": rr}


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
            "tag": tag}


# ---- LIVE real-time signals (Kiwoom) — the day-trading layer --------------------
def realtime_for(ticker: str) -> dict[str, Any] | None:
    """Live order-book imbalance + intraday 수급 + program net from Kiwoom REST.
    Returns None if Kiwoom keys aren't set or the call fails — the brief still works
    without it (graceful degradation). Cached 20s inside kiwoom_rest."""
    try:
        from services import kiwoom_rest as kr
        if kr._token() is None:          # no creds / unreachable -> skip cleanly
            return None
        sig = kr.realtime_signals(ticker)
        ob = sig.get("order_book") or {}
        fl = sig.get("flows") or {}
        pr = sig.get("program") or {}
        imb = ob.get("imbalance")
        pressure = ("매수우위" if imb is not None and imb > 0.15 else
                    "매도우위" if imb is not None and imb < -0.15 else "균형")
        base = getattr(kr, "_active_base", "") or ""        # which env actually authed
        env = "모의" if "mockapi" in base else "실전"
        return {
            "live": True, "env": env, "as_of": fl.get("date"),
            "imbalance": imb, "pressure": pressure,
            "best_bid": ob.get("best_bid"), "best_ask": ob.get("best_ask"),
            "foreign": fl.get("foreign"), "institution": fl.get("institution"),
            "fin_invest": fl.get("fin_invest"), "individual": fl.get("individual"),
            "program_net": pr.get("net_amt"), "price": fl.get("price"),
        }
    except Exception:
        return None


# ---- per-stock card ------------------------------------------------------------
def stock_card(db, ticker: str, horizon: int = 5, live: bool = False) -> dict[str, Any]:
    pred = ps.get_ticker(db, ticker, horizon) or {}
    advice = pred.get("advice", "HOLD")
    return {
        "ticker": ticker, "name": NAMES.get(ticker, ticker),
        "advice": advice, "confidence": pred.get("confidence"),
        "direction": pred.get("direction"),
        "backtest_acc": pred.get("backtest_acc"),
        "expected_low_pct": pred.get("expected_low_pct"),
        "expected_high_pct": pred.get("expected_high_pct"),
        "reasoning": pred.get("reasoning"),
        "levels": _levels(db, ticker, advice),
        "flow": _flow(db, ticker),
        "realtime": realtime_for(ticker) if live else None,
    }


# ---- the full brief ------------------------------------------------------------
def brief(db, horizon: int = 5) -> dict[str, Any]:
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
    picks = [stock_card(db, tk, horizon) for tk in ordered]
    picks_buy = [c for c in picks if c["advice"] == "BUY"]
    picks_sell = [c for c in picks if c["advice"] == "SELL"]

    # 수급 heatmap across the whole universe (who's buying today). Featured stocks
    # (PRIORITY) listed first, then the rest in the universe order.
    heat = []
    heat_order = PRIORITY + [tk for tk in NAMES if tk not in PRIORITY]
    for tk in heat_order:
        f = _flow(db, tk)
        if f:
            heat.append({"ticker": tk, "name": NAMES[tk],
                         "foreign": f["foreign"], "inst": f["inst"],
                         "foreign_net": f["foreign_net"], "inst_net": f["inst_net"],
                         "tag": f["tag"]})

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
