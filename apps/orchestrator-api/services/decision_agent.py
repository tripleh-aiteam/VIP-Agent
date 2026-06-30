"""decision_agent.py — unified BUY / HOLD / SELL decision from three factors.

Fuses, for one stock:
  1. NEWS analysis        — latest news + per-item impact/direction (news_impact)
  2. INVESTOR FLOWS       — 외국인/기관/개인 net buying + accumulation/distribution (trading_brief)
  3. HISTORICAL/TECHNICAL — trend, MAs, support/resistance, momentum (daily candles)
…plus the ML model's call, into ONE recommendation with a factor-by-factor breakdown.

Honest by design: each factor contributes a small ±score; the verdict is the weighted
sum. It is a reasoned synthesis, NOT a guarantee — accuracy is shown so the user can
judge. All numbers come from real data (prices, flows, news) — none invented.
"""
from __future__ import annotations

from typing import Any, Optional


# ---- factor 3: technicals from daily candles ----
def _technicals(code: str) -> dict[str, Any]:
    from services import naver_stock as ns
    hist = ns.daily_history(code, days=60)
    if not hist or len(hist) < 20:
        return {"score": 0, "summary_ko": "데이터 부족", "summary_en": "insufficient data"}
    chron = list(reversed(hist))
    closes = [r["close"] for r in chron if r.get("close") is not None]
    highs = [r["high"] for r in chron if r.get("high") is not None]
    lows = [r["low"] for r in chron if r.get("low") is not None]
    cur = closes[-1]

    def ma(n):
        return sum(closes[-n:]) / n if len(closes) >= n else None
    ma5, ma20, ma60 = ma(5), ma(20), ma(60)
    res = max(highs[-20:]); sup = min(lows[-20:])
    pos = (cur - sup) / (res - sup) * 100 if res > sup else 50          # 0=support,100=resistance
    # momentum: 5-day rate of change
    roc5 = (cur / closes[-6] - 1) * 100 if len(closes) >= 6 else 0

    score = 0
    bits_ko, bits_en = [], []
    if ma20 and ma60:
        if cur > ma20 > ma60:
            score += 2; bits_ko.append("정배열 상승추세"); bits_en.append("uptrend (price>MA20>MA60)")
        elif cur < ma20 < ma60:
            score -= 2; bits_ko.append("역배열 하락추세"); bits_en.append("downtrend (price<MA20<MA60)")
        elif cur > ma20:
            score += 1; bits_ko.append("20일선 위"); bits_en.append("above MA20")
        else:
            score -= 1; bits_ko.append("20일선 아래"); bits_en.append("below MA20")
    if pos < 30:
        score += 1; bits_ko.append("지지선 부근(저점)"); bits_en.append("near support")
    elif pos > 70:
        score -= 1; bits_ko.append("저항선 부근(고점)"); bits_en.append("near resistance")
    if roc5 > 3:
        score += 1; bits_ko.append(f"단기 모멘텀 +{roc5:.1f}%"); bits_en.append(f"momentum +{roc5:.1f}%")
    elif roc5 < -3:
        score -= 1; bits_ko.append(f"단기 모멘텀 {roc5:.1f}%"); bits_en.append(f"momentum {roc5:.1f}%")
    return {"score": score, "support": round(sup), "resistance": round(res),
            "pos_in_range": round(pos), "ma20": round(ma20) if ma20 else None,
            "summary_ko": ", ".join(bits_ko) or "중립", "summary_en": ", ".join(bits_en) or "neutral"}


# ---- factor 1: news ----
def _news(db, code: str) -> dict[str, Any]:
    from services import news_impact as ni
    items = ni.effective_news(db, code, limit=6) or []
    score = 0
    titles = []
    for n in items:
        d = n.get("direction")
        senti = n.get("sentiment")
        imp = n.get("impact") or 0.5
        # direction may be +1/-1/0 or '▲'/'▼'; sentiment may be a -1..1 score
        dv = (1 if d in (1, "▲", "up", "positive") else -1 if d in (-1, "▼", "down", "negative") else 0)
        if dv == 0 and isinstance(senti, (int, float)):
            dv = 1 if senti > 0.1 else -1 if senti < -0.1 else 0
        score += dv * (2 if imp and float(imp) >= 0.7 else 1)
        if n.get("title"):
            titles.append(("📈" if dv > 0 else "📉" if dv < 0 else "•") + " " + n["title"][:50])
    score = max(-3, min(3, score))
    return {"score": score, "count": len(items), "titles": titles[:4]}


# ---- factor 2: investor flows ----
def _flows(db, code: str) -> dict[str, Any]:
    from services.trading_brief import _flow
    f = _flow(db, code) or {}
    score = 0
    tag = f.get("tag")
    if tag == "강력매집":
        score += 2
    elif tag == "분산매도":
        score -= 2
    net5 = (f.get("foreign_5d") or 0) + (f.get("inst_5d") or 0)
    if net5 > 0:
        score += 1
    elif net5 < 0:
        score -= 1
    score = max(-3, min(3, score))
    return {"score": score, "tag": tag, "tag_en": f.get("tag_en"),
            "foreign_net": f.get("foreign_net"), "inst_net": f.get("inst_net"),
            "foreign_5d": f.get("foreign_5d"), "inst_5d": f.get("inst_5d")}


def decide(db, ticker: str) -> dict[str, Any]:
    from services import prediction_service as ps
    from services import trading_brief as tb
    code = str(ticker).zfill(6)
    name = ps.NAMES.get(code, code)

    news = _news(db, code)
    flows = _flows(db, code)
    tech = _technicals(code)
    ml = ps.get_ticker(db, code) or {}

    # Pull the SAME two methods the outlook block shows, so the recommendation is built on
    # BOTH consistently: Method 1 = ML, Method 2 = Analysis (호가/수급/박스권).
    m1, m2 = {}, {}
    try:
        from services.assistant_tools import tool_two_method_view
        tm = tool_two_method_view(ticker=code, db=db) or {}
        m1 = tm.get("method1_ml") or {}
        m2 = tm.get("method2_analysis") or {}
    except Exception:
        pass

    ml_adv = (m1.get("advice") or ml.get("advice") or "").upper()
    ml_score = 1 if ml_adv == "BUY" else -1 if ml_adv == "SELL" else 0
    an_sig = (m2.get("signal") or "").upper()

    # weighted fusion (news 1.0, flows 1.0, technicals 1.2, ML 1.0)
    total = news["score"] * 1.0 + flows["score"] * 1.0 + tech["score"] * 1.2 + ml_score * 1.0
    decision = "BUY" if total >= 2.5 else "SELL" if total <= -2.5 else "HOLD"
    conf = "높음" if abs(total) >= 4 else "보통" if abs(total) >= 2 else "낮음"
    conf_en = {"높음": "high", "보통": "medium", "낮음": "low"}[conf]

    acc = m1.get("backtest_accuracy_pct")
    if acc is None and ml.get("backtest_acc") is not None:
        acc = round(ml["backtest_acc"] * 100, 1)
    acc_txt = f"{acc:.0f}%" if acc is not None else "n/a"
    em = m1.get("expected_move_pct")
    sup, res = tech.get("support"), tech.get("resistance")

    # ---- Method 1 (ML) — verdict + WHY, in words ----
    ml_call_ko = {"BUY": "매수", "SELL": "매도", "HOLD": "보유"}.get(ml_adv, "보유")
    ml_why_ko = {"BUY": "시장 대비 상대강세(아웃퍼폼)를 예측합니다",
                 "SELL": "시장 대비 상대약세(언더퍼폼)를 예측합니다",
                 "HOLD": "시장 대비 뚜렷한 우위를 찾지 못했습니다(신호 약함)"}.get(ml_adv, "신호가 약합니다")
    ml_why_en = {"BUY": "predicts the stock will outperform the market",
                 "SELL": "predicts the stock will underperform the market",
                 "HOLD": "finds no clear edge versus the market (weak signal)"}.get(ml_adv, "weak signal")
    em_ko = f" (5일 예상 ±{abs(em)}%, 정확도 {acc_txt})" if em is not None else f" (정확도 {acc_txt})"
    em_en = f" (5-day move ±{abs(em)}%, accuracy {acc_txt})" if em is not None else f" (accuracy {acc_txt})"

    # ---- Method 2 (Analysis) — verdict + WHY (호가/수급/박스권), in words ----
    an_call_ko = {"BUY": "매수 우위", "SELL": "매도 우위", "WATCH": "관망", "HOLD": "관망"}.get(an_sig, "관망")
    an_call_en = {"BUY": "buy-side", "SELL": "sell-side", "WATCH": "neutral", "HOLD": "neutral"}.get(an_sig, "neutral")
    an_why_ko = ", ".join((m2.get("reasons") or [])[:3]) or tech.get("summary_ko", "중립")
    an_why_en = ", ".join((m2.get("reasons_en") or [])[:3]) or tech.get("summary_en", "neutral")

    # ---- News in words ----
    ns = news["score"]
    news_ko = "호재 우세" if ns > 0 else "악재 우세" if ns < 0 else "중립"
    news_en = "net positive" if ns > 0 else "net negative" if ns < 0 else "neutral"
    top_news = news["titles"][0] if news.get("titles") else ""

    agree = bool(ml_adv and an_sig in ("BUY", "SELL") and ml_adv == an_sig)
    consensus_ko = "두 방법이 같은 방향" if agree else "두 방법이 다소 엇갈림"
    consensus_en = "both methods agree" if agree else "the two methods diverge"

    def _pl(v):                                   # plain price level (no +/- sign)
        try:
            return f"{int(v):,}"
        except Exception:
            return "-"

    # ---- Bottom-line action, in words with trigger levels ----
    if decision == "BUY":
        act_ko = f"신규 매수·비중 확대를 고려할 만합니다. 손절은 지지선 {_pl(sup)}원 이탈 시로 잡으세요."
        act_en = f"Worth a new buy / adding to the position; set a stop if it loses support at ₩{_pl(sup)}."
    elif decision == "SELL":
        act_ko = f"비중 축소·차익 실현을 고려하세요. 저항 {_pl(res)}원을 회복하기 전까지는 보수적으로 보는 게 좋습니다."
        act_en = f"Consider trimming / taking profit; stay cautious until it reclaims resistance at ₩{_pl(res)}."
    else:
        act_ko = (f"지금은 서둘러 사기보다 보유·관망이 적절합니다. 저항 {_pl(res)}원을 강하게 돌파하면 비중 확대, "
                  f"지지 {_pl(sup)}원이 깨지면 비중 축소로 대응하세요.")
        act_en = (f"Hold / wait rather than chase. Add if it breaks resistance ₩{_pl(res)}; "
                  f"reduce if it loses support ₩{_pl(sup)}.")

    en_name = {"SK하이닉스": "SK Hynix", "삼성전자": "Samsung Electronics",
               "삼성전기": "Samsung Electro-Mechanics", "SK스퀘어": "SK Square",
               "한미반도체": "Hanmi Semiconductor", "카카오": "Kakao"}.get(name, name)
    dec_ko = {"BUY": "매수", "SELL": "매도", "HOLD": "보유/관망"}[decision]
    ko = (f"**🎯 {name} — 추천: {dec_ko}** (확신 {conf} · {consensus_ko})\n"
          f"🤖 **방법 1 (머신러닝) → {ml_call_ko}**: {ml_why_ko}{em_ko}.\n"
          f"📈 **방법 2 (분석·호가/수급/박스권) → {an_call_ko}**: {an_why_ko}.\n"
          f"📰 **뉴스: {news_ko}**" + (f" — {top_news}" if top_news else "") + ".\n"
          f"💡 **종합 추천:** {act_ko}\n"
          f"⚠️ 두 방법과 뉴스·수급을 종합한 참고 의견이며, 투자 권유·수익 보장이 아닙니다.")
    en = (f"**🎯 {en_name} — Recommendation: {decision}** (confidence {conf_en} · {consensus_en})\n"
          f"🤖 **Method 1 (Machine Learning) → {ml_adv or 'HOLD'}**: {ml_why_en}{em_en}.\n"
          f"📈 **Method 2 (Analysis · orderbook/flows/box) → {an_call_en}**: {an_why_en}.\n"
          f"📰 **News: {news_en}**" + (f" — {top_news}" if top_news else "") + ".\n"
          f"💡 **Bottom line:** {act_en}\n"
          f"⚠️ A reasoned synthesis of both methods + news/flows — not investment advice or a guarantee.")
    return {"ticker": code, "name": name, "decision": decision, "score": round(total, 1),
            "confidence": conf_en, "news": news, "flows": flows, "technicals": tech,
            "method1_ml": {"call": ml_adv, "accuracy_pct": acc, "expected_move_pct": em},
            "method2_analysis": {"signal": an_sig, "reasons": m2.get("reasons")},
            "ml": {"advice": ml_adv, "accuracy_pct": acc},
            "reasoning_ko": ko, "reasoning_en": en}


def _w(v) -> str:
    if v is None:
        return "-"
    try:
        return f"{int(v):+,}"
    except Exception:
        return str(v)
