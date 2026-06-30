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

    # Method 3 (Wave) — independent Elliott/Fibonacci deep-pullback verdict.
    wave = {}
    try:
        from services.wave_method import wave_for
        wave = wave_for(db, code) or {}
    except Exception:
        pass
    wv = (wave.get("verdict") or "").upper()
    wave_score = 1 if wv == "BUY" else -1 if wv == "AVOID" else 0

    # weighted fusion (news 1.0, flows 1.0, technicals 1.2, ML 1.0, Wave 1.0)
    total = (news["score"] * 1.0 + flows["score"] * 1.0 + tech["score"] * 1.2
             + ml_score * 1.0 + wave_score * 1.0)
    decision = "BUY" if total >= 2.5 else "SELL" if total <= -2.5 else "HOLD"
    conf = "높음" if abs(total) >= 4 else "보통" if abs(total) >= 2 else "낮음"
    conf_en = {"높음": "high", "보통": "medium", "낮음": "low"}[conf]

    acc = m1.get("backtest_accuracy_pct")
    if acc is None and ml.get("backtest_acc") is not None:
        acc = round(ml["backtest_acc"] * 100, 1)
    acc_txt = f"{acc:.0f}%" if acc is not None else "n/a"
    em = m1.get("expected_move_pct")
    sup, res = tech.get("support"), tech.get("resistance")

    def _pl(v):                                   # plain price level (no +/- sign)
        try:
            return f"{int(v):,}"
        except Exception:
            return "-"

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

    # ---- Method 3 (Wave) — verdict + WHY (Elliott/Fibonacci), in words ----
    has_wave = wv in ("BUY", "WATCH", "AVOID")
    wv_ko = {"BUY": "매수", "WATCH": "관망", "AVOID": "회피"}.get(wv, "데이터 없음")
    wv_en = {"BUY": "BUY", "WATCH": "WATCH", "AVOID": "AVOID"}.get(wv, "no data")
    _wsc = wave.get("wave_score")
    _wret = wave.get("retrace")
    wave_why_ko = wave.get("reason") or "유효한 상승 파동을 찾지 못했습니다"
    wave_why_en = {
        "BUY": f"strong rally (wave score {_wsc}) pulled back into the deep Fibonacci buy zone",
        "WATCH": f"strong rally (wave score {_wsc}) but not yet in the buy zone"
                 + (f" (pullback {round(_wret*100)}%)" if _wret is not None else ""),
        "AVOID": "the uptrend is too weak or has broken down",
    }.get(wv, "no valid rally detected")
    wave_zone_ko = wave_zone_en = None
    if wv == "BUY" and wave.get("entry"):
        wave_zone_ko = f"진입 {_pl(wave['entry'])}원 / 손절 {_pl(wave['stop'])}원 / 목표 {_pl(wave['target'])}원 (R:R {wave.get('rr')})"
        wave_zone_en = f"entry ₩{_pl(wave['entry'])} / stop ₩{_pl(wave['stop'])} / target ₩{_pl(wave['target'])} (R:R {wave.get('rr')})"

    # ---- News in words ----
    ns = news["score"]
    news_ko = "호재 우세" if ns > 0 else "악재 우세" if ns < 0 else "중립"
    news_en = "net positive" if ns > 0 else "net negative" if ns < 0 else "neutral"
    top_news = news["titles"][0] if news.get("titles") else ""

    agree = bool(ml_adv and an_sig in ("BUY", "SELL") and ml_adv == an_sig)
    # 3-method agreement tag for the headline (ML + Analysis + Wave)
    _dirs = [d for d in (ml_score, (1 if an_sig == "BUY" else -1 if an_sig == "SELL" else 0), wave_score) if d != 0]
    _all_same = len(_dirs) >= 2 and len(set(_dirs)) == 1
    consensus_ko = "3가지 방법 방향 일치" if _all_same else "방법 간 신호 혼조"
    consensus_en = "all methods align" if _all_same else "methods are mixed"

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

    # extra data for the detailed write-up
    algo = m1.get("best_algorithm")
    lv = m2.get("levels") or {}
    pos = tech.get("pos_in_range")

    def _zone(lo, hi):
        return f"{_pl(lo)}~{_pl(hi)}" if lo and hi else None
    buy_zone, sell_zone = _zone(lv.get("buy_lo"), lv.get("buy_hi")), _zone(lv.get("sell_lo"), lv.get("sell_hi"))

    def _clean(t):                                 # strip the 📈/📉/• prefix from a headline
        return (t or "").lstrip("📈📉• ").strip()
    heads = [_clean(t) for t in (news.get("titles") or [])][:3]

    dec_full_ko = {"BUY": "매수 (BUY)", "SELL": "매도 (SELL)", "HOLD": "보유 (HOLD)"}[decision]
    em_txt_ko = (f"예상 5일 변동 ±{abs(em)}%" if em is not None else "예상 변동 추정 불가")
    em_txt_en = (f"expected 5-day move ±{abs(em)}%" if em is not None else "expected move n/a")

    # ① 추천(직접 답변) → ② 방법 1/2/3 + 기술적/뉴스 → ③ 종합 추천 → ④ 요약(끝)
    ko_lines = [
        f"**추천: {dec_full_ko}**  ·  확신 {conf}  ·  {consensus_ko}",
        "",
        f"**방법 1 — 머신러닝 알고리즘" + (f" ({algo})" if algo else "") + "**",
        f"· 판단: {ml_call_ko}",
        f"· 근거: {ml_why_ko}",
        f"· 수치: {em_txt_ko} · 백테스트 정확도 {acc_txt}",
        "",
        "**방법 2 — 분석 (호가·수급·박스권)**",
        f"· 판단: {an_call_ko}",
        f"· 근거: {an_why_ko}",
    ]
    if buy_zone or sell_zone:
        ko_lines.append(f"· 거래 구간: " + " · ".join(filter(None, [
            f"매수 {buy_zone}원" if buy_zone else None, f"매도 {sell_zone}원" if sell_zone else None])))
    if has_wave:
        ko_lines += ["", "**방법 3 — 파동 (엘리엇·피보나치)**",
                     f"· 판단: {wv_ko}" + (f" (파동점수 {_wsc})" if _wsc is not None else ""),
                     f"· 근거: {wave_why_ko}"]
        if wave_zone_ko:
            ko_lines.append(f"· 거래 구간: {wave_zone_ko}")
    ko_lines += [
        "",
        "**기술적 지표**",
        f"· {tech.get('summary_ko','중립')}",
        f"· 지지 {_pl(sup)}원 / 저항 {_pl(res)}원" + (f" · 박스권 내 위치 {pos}%" if pos is not None else ""),
        "",
        f"**뉴스 — {news_ko}**",
    ]
    ko_lines += ([f"· {h}" for h in heads] if heads else ["· 특이 뉴스 없음"])
    ko_lines += [
        "",
        f"**종합 추천** — {act_ko} "
        + ("세 방법의 방향이 일치해 해당 시나리오의 신뢰도가 상대적으로 높습니다." if _all_same
           else "방법별 신호가 엇갈리므로, 한 신호만 보고 서두르기보다 추세 확인 후 대응하는 것이 안전합니다."),
        "",
        f"**요약** — 머신러닝 '{ml_call_ko}' · 분석 '{an_call_ko}'"
        + (f" · 파동 '{wv_ko}'" if has_wave else "") + f" · 뉴스 '{news_ko}'. "
        + ("세 방법이 같은 방향을 가리킵니다." if _all_same
           else "신호가 엇갈리므로 신중한 접근이 필요합니다."),
        "",
        "※ 3가지 방법과 뉴스·수급·기술적 지표를 종합한 참고 의견이며, 투자 권유나 수익 보장이 아닙니다.",
    ]
    ko = "\n".join(ko_lines)

    en_lines = [
        f"**Recommendation: {decision}**  ·  confidence {conf_en}  ·  {consensus_en}",
        "",
        f"**Method 1 — Machine Learning" + (f" ({algo})" if algo else "") + "**",
        f"- Call: {ml_adv or 'HOLD'}",
        f"- Why: {ml_why_en}",
        f"- Figures: {em_txt_en} · backtest accuracy {acc_txt}",
        "",
        "**Method 2 — Analysis (orderbook · flows · box)**",
        f"- Call: {an_call_en}",
        f"- Why: {an_why_en}",
    ]
    if buy_zone or sell_zone:
        en_lines.append("- Trade zones: " + " · ".join(filter(None, [
            f"buy ₩{buy_zone}" if buy_zone else None, f"sell ₩{sell_zone}" if sell_zone else None])))
    if has_wave:
        en_lines += ["", "**Method 3 — Wave (Elliott · Fibonacci)**",
                     f"- Call: {wv_en}" + (f" (wave score {_wsc})" if _wsc is not None else ""),
                     f"- Why: {wave_why_en}"]
        if wave_zone_en:
            en_lines.append(f"- Trade zone: {wave_zone_en}")
    en_lines += [
        "",
        "**Technicals**",
        f"- {tech.get('summary_en','neutral')}",
        f"- support ₩{_pl(sup)} / resistance ₩{_pl(res)}" + (f" · {pos}% through the range" if pos is not None else ""),
        "",
        f"**News — {news_en}**",
    ]
    en_lines += ([f"- {h}" for h in heads] if heads else ["- no notable news"])
    en_lines += [
        "",
        f"**Bottom line** — {act_en} "
        + ("All methods point the same way, so this scenario carries relatively higher conviction." if _all_same
           else "The methods diverge, so confirm the trend before acting rather than chasing one signal."),
        "",
        f"**Summary** — Machine Learning '{ml_adv or 'HOLD'}' · Analysis '{an_call_en}'"
        + (f" · Wave '{wv_en}'" if has_wave else "") + f" · news '{news_en}'. "
        + ("All three point the same way." if _all_same
           else "The signals diverge, so a cautious stance beats betting heavily on one side."),
        "",
        "Note: a reasoned synthesis of all 3 methods + news/flows/technicals — not investment advice or a guarantee.",
    ]
    en = "\n".join(en_lines)
    return {"ticker": code, "name": name, "decision": decision, "score": round(total, 1),
            "confidence": conf_en, "news": news, "flows": flows, "technicals": tech,
            "method1_ml": {"call": ml_adv, "accuracy_pct": acc, "expected_move_pct": em},
            "method2_analysis": {"signal": an_sig, "reasons": m2.get("reasons")},
            "method3_wave": {"verdict": wv or None, "wave_score": _wsc,
                             "entry": wave.get("entry"), "stop": wave.get("stop"),
                             "target": wave.get("target"), "rr": wave.get("rr")},
            "ml": {"advice": ml_adv, "accuracy_pct": acc},
            "reasoning_ko": ko, "reasoning_en": en}


def _w(v) -> str:
    if v is None:
        return "-"
    try:
        return f"{int(v):+,}"
    except Exception:
        return str(v)
