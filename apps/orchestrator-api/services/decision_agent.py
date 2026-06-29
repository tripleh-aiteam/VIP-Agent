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
    ml_adv = (ml.get("advice") or "").upper()
    ml_score = 1 if ml_adv == "BUY" else -1 if ml_adv == "SELL" else 0

    # weighted fusion (news 1.0, flows 1.0, technicals 1.2, ML 1.0)
    total = news["score"] * 1.0 + flows["score"] * 1.0 + tech["score"] * 1.2 + ml_score * 1.0
    decision = "BUY" if total >= 2.5 else "SELL" if total <= -2.5 else "HOLD"
    conf = "높음" if abs(total) >= 4 else "보통" if abs(total) >= 2 else "낮음"
    conf_en = {"높음": "high", "보통": "medium", "낮음": "low"}[conf]

    dec_ko = {"BUY": "매수", "SELL": "매도", "HOLD": "보유/관망"}[decision]
    acc = ml.get("backtest_acc")
    acc_txt = f"{acc*100:.0f}%" if acc is not None else "n/a"
    ko = (f"**🎯 {name} 종합 판단: {dec_ko}** (확신 {conf}, 종합점수 {total:+.1f})\n"
          f"📰 뉴스({news['score']:+d}): " + (" / ".join(news["titles"]) if news["titles"] else "특이 뉴스 없음") + "\n"
          f"💰 수급({flows['score']:+d}): 외국인5일 {(_w(flows['foreign_5d']))}, 기관5일 {(_w(flows['inst_5d']))}"
          + (f" · {flows['tag']}" if flows.get("tag") else "") + "\n"
          f"📈 기술적({tech['score']:+d}): {tech['summary_ko']} (지지 {_w(tech.get('support'))}, 저항 {_w(tech.get('resistance'))})\n"
          f"🤖 ML 모델: {ml.get('advice','-')} (정확도 {acc_txt})\n"
          f"⚠️ 세 요소(뉴스·수급·기술)와 ML을 종합한 판단이며 투자 권유·수익 보장이 아닙니다.")
    en = (f"**🎯 {name} — Decision: {decision}** (confidence {conf_en}, score {total:+.1f})\n"
          f"📰 News({news['score']:+d}): " + (" / ".join(news["titles"]) if news["titles"] else "no notable news") + "\n"
          f"💰 Flows({flows['score']:+d}): foreign 5d {_w(flows['foreign_5d'])}, inst 5d {_w(flows['inst_5d'])}"
          + (f" · {flows['tag_en']}" if flows.get("tag_en") else "") + "\n"
          f"📈 Technicals({tech['score']:+d}): {tech['summary_en']} (support {_w(tech.get('support'))}, resistance {_w(tech.get('resistance'))})\n"
          f"🤖 ML model: {ml.get('advice','-')} (accuracy {acc_txt})\n"
          f"⚠️ A synthesis of News + Flows + Technicals + ML — not investment advice or a guarantee.")
    return {"ticker": code, "name": name, "decision": decision, "score": round(total, 1),
            "confidence": conf_en, "news": news, "flows": flows, "technicals": tech,
            "ml": {"advice": ml.get("advice"), "accuracy_pct": (round(acc * 100, 1) if acc is not None else None)},
            "reasoning_ko": ko, "reasoning_en": en}


def _w(v) -> str:
    if v is None:
        return "-"
    try:
        return f"{int(v):+,}"
    except Exception:
        return str(v)
