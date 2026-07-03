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
            "pos_in_range": round(pos), "ma20": round(ma20) if ma20 else None, "close": round(cur),
            "summary_ko": ", ".join(bits_ko) or "중립", "summary_en": ", ".join(bits_en) or "neutral"}


# ---- factor 1: news (newspaper) ----
def _news(db, code: str, name: Optional[str] = None) -> dict[str, Any]:
    from services import news_impact as ni
    items = list(ni.effective_news(db, code, limit=6) or [])
    # NEWSPAPER LIVE fallback: raw_news is often empty per-ticker, so when the DB is thin
    # pull real recent Korean headlines (Naver News + Google-News) and impact-score them —
    # so the decision genuinely reflects today's 뉴스/신문 flow, not just the sparse table.
    if len(items) < 3 and name and name != code:
        try:
            from services.stock_news import _fetch_items
            seen = {(n.get("title") or "")[:40] for n in items}
            for h in _fetch_items(name, limit=8, days=3):
                t = (h.get("title") or "").strip()
                if not t or t[:40] in seen:
                    continue
                seen.add(t[:40])
                items.append({"title": t, "url": h.get("url") or "",
                              "source": h.get("source"), **ni.score(t, h.get("snippet", ""), None)})
        except Exception:
            pass
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
            emoji = "📈" if dv > 0 else "📉" if dv < 0 else "•"
            title = n["title"][:60].replace("]", "").replace("[", "")   # keep markdown link intact
            url = (n.get("url") or "").strip()
            # clickable headline (markdown link) when we have a URL, else plain text
            titles.append(f"{emoji} [{title}]({url})" if url.startswith("http") else f"{emoji} {title}")
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


# ---- market indicators (KOSPI/KOSDAQ/USDKRW) — richer '시장 상황' than KODEX alone ----
_mi_cache: dict = {"t": 0.0, "v": None}


def _market_indicators() -> dict:
    """KOSPI/KOSDAQ %-change + USD/KRW from Naver mobile API. Cached 120s; every part
    optional (graceful when an endpoint changes)."""
    import time as _t
    if (_t.time() - _mi_cache["t"]) < 120 and _mi_cache["v"] is not None:
        return _mi_cache["v"]
    out: dict = {}
    try:
        import httpx
        hdr = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        with httpx.Client(timeout=6.0, headers=hdr) as cli:
            for idx in ("KOSPI", "KOSDAQ"):
                try:
                    j = cli.get(f"https://m.stock.naver.com/api/index/{idx}/basic").json()
                    r = j.get("fluctuationsRatio")
                    px = j.get("closePrice")
                    if r is not None:
                        out[idx.lower()] = {"pct": float(str(r).replace(",", "")),
                                            "price": px}
                except Exception:
                    pass
            try:
                # NOTE: this endpoint rejects pageSize < 10 ("Too small") — use 10, read row 0.
                j = cli.get("https://m.stock.naver.com/front-api/marketIndex/prices"
                            "?category=exchange&reutersCode=FX_USDKRW&page=1&pageSize=10").json()
                row = ((j.get("result") or [{}])[0]) if isinstance(j.get("result"), list) else {}
                if row.get("closePrice"):
                    out["usdkrw"] = {"price": row["closePrice"],
                                     "pct": float(str(row.get("fluctuationsRatio") or 0).replace(",", ""))}
            except Exception:
                pass
            # US backdrop (prev close): 나스닥 + VIX(공포지수) — world-index API
            for key, idx in (("nasdaq", ".IXIC"), ("vix", ".VIX")):
                try:
                    j = cli.get(f"https://api.stock.naver.com/index/{idx}/basic").json()
                    if j.get("closePrice"):
                        out[key] = {"price": j["closePrice"],
                                    "pct": float(str(j.get("fluctuationsRatio") or 0).replace(",", ""))}
                except Exception:
                    pass
            try:  # WTI 유가
                j = cli.get("https://m.stock.naver.com/front-api/marketIndex/prices"
                            "?category=energy&reutersCode=CLcv1&page=1&pageSize=10").json()
                row = ((j.get("result") or [{}])[0]) if isinstance(j.get("result"), list) else {}
                if row.get("closePrice"):
                    out["wti"] = {"price": row["closePrice"],
                                  "pct": float(str(row.get("fluctuationsRatio") or 0).replace(",", ""))}
            except Exception:
                pass
    except Exception:
        pass
    _mi_cache.update(t=_t.time(), v=out)
    return out


# ---- factor 5b: YouTube market sentiment (light nudge, per-stock only when discussed) ----
_YT_BULL = ("급등", "상승", "호재", "매수", "강세", "상향", "돌파", "사자", "유망", "수혜", "신고가", "긍정")
_YT_BEAR = ("급락", "하락", "악재", "매도", "약세", "조정", "팔자", "하향", "고점", "경계", "리스크", "부진", "부정")


def _youtube(db, name: Optional[str]) -> dict[str, Any]:
    """Latest market-wide YouTube (grounded) report → a LIGHT sentiment nudge, applied
    only when the stock is explicitly discussed; otherwise neutral market context. Never
    fabricates a per-stock view when the name isn't mentioned."""
    if not name:
        return {"score": 0, "mentioned": False, "note_ko": "", "note_en": ""}
    try:
        import json as _json
        from services.youtube_grounded import latest_payload
        blob = _json.dumps((latest_payload(db) or {}).get("report") or {}, ensure_ascii=False)
    except Exception:
        blob = ""
    if not blob or name not in blob:
        from urllib.parse import quote
        return {"score": 0, "mentioned": False,
                "links": [f"https://www.youtube.com/results?search_query={quote((name or '') + ' 주식')}"],
                "note_ko": "시장 심리 참고(개별 언급 없음)", "note_en": "market sentiment only (not singled out)"}
    score, idx = 0, 0
    while True:
        i = blob.find(name, idx)
        if i < 0:
            break
        w = blob[max(0, i - 160): i + 160]
        b = sum(w.count(k) for k in _YT_BULL)
        s = sum(w.count(k) for k in _YT_BEAR)
        score += (1 if b > s else -1 if s > b else 0)
        idx = i + len(name)
    score = max(-1, min(1, score))
    tag_ko = "긍정" if score > 0 else "부정" if score < 0 else "중립"
    tag_en = "positive" if score > 0 else "negative" if score < 0 else "neutral"
    # 1-2 clickable video links: real ones from the grounded report near a mention when
    # present; else a YouTube search link so the user can always watch for themselves.
    import re as _re2
    links = []
    for m in _re2.finditer(r"https?://(?:www\.)?(?:youtube\.com/watch\?v=[\w-]+|youtu\.be/[\w-]+)", blob):
        u = m.group(0)
        if u not in links and abs(m.start() - blob.find(name)) < 4000:
            links.append(u)
        if len(links) >= 2:
            break
    if not links:
        from urllib.parse import quote
        links = [f"https://www.youtube.com/results?search_query={quote(name + ' 주식')}"]
    return {"score": score, "mentioned": True, "links": links,
            "note_ko": f"유튜브에서 언급 — 논조 {tag_ko}", "note_en": f"discussed on YouTube — {tag_en} tone"}


def decide(db, ticker: str, focus: Optional[str] = None) -> dict[str, Any]:
    """focus=None → the buy/sell/hold recommendation. focus='sell' → lead with SELL/exit
    timing (익절/손절 levels) for someone who already holds — same engine, exit framing."""
    from services import prediction_service as ps
    from services import trading_brief as tb
    code = str(ticker).zfill(6)
    name = ps.NAMES.get(code, code)

    news = _news(db, code, name)
    flows = _flows(db, code)
    tech = _technicals(code)
    yt = _youtube(db, name)
    ml = ps.get_ticker(db, code) or {}

    # market backdrop — today's live KODEX200 move, so the advice names the tape it's given in
    mkt = None
    try:
        mkt = tb._mkt_ret_today(db)
    except Exception:
        pass
    # richer market line: KOSPI + KOSDAQ + USD/KRW alongside the KODEX read
    mi = _market_indicators()
    _parts_ko, _parts_en = [], []
    if mi.get("kospi"):
        _parts_ko.append(f"코스피 {mi['kospi']['pct']:+.2f}%")
        _parts_en.append(f"KOSPI {mi['kospi']['pct']:+.2f}%")
    if mi.get("kosdaq"):
        _parts_ko.append(f"코스닥 {mi['kosdaq']['pct']:+.2f}%")
        _parts_en.append(f"KOSDAQ {mi['kosdaq']['pct']:+.2f}%")
    if mi.get("usdkrw"):
        _fx = mi["usdkrw"]
        _parts_ko.append(f"환율 {_fx['price']}원 ({_fx['pct']:+.2f}%)")
        _parts_en.append(f"USD/KRW {_fx['price']} ({_fx['pct']:+.2f}%)")
    if not _parts_ko and mkt is not None:
        _parts_ko.append(f"KODEX200 {mkt:+.2f}%")
        _parts_en.append(f"KODEX200 {mkt:+.2f}%")
    _mref = (mi.get("kospi") or {}).get("pct", mkt)
    if _parts_ko:
        _mtone_ko = ("시장 우호적" if (_mref or 0) >= 0.3 else "시장 급락 경계" if (_mref or 0) <= -1.5
                     else "시장 약세 부담" if (_mref or 0) < 0 else "시장 중립")
        _mtone_en = ("supportive tape" if (_mref or 0) >= 0.3 else "market plunging — caution" if (_mref or 0) <= -1.5
                     else "soft tape" if (_mref or 0) < 0 else "neutral tape")
        mkt_line_ko = " · ".join(_parts_ko) + f" — {_mtone_ko}"
        mkt_line_en = " · ".join(_parts_en) + f" — {_mtone_en}"
    else:
        mkt_line_ko = mkt_line_en = None
    # second line: overseas backdrop — 나스닥(전일) + VIX(공포지수, 수준 해석) + WTI 유가
    _g_ko, _g_en = [], []
    if mi.get("nasdaq"):
        _g_ko.append(f"나스닥(전일) {mi['nasdaq']['price']} ({mi['nasdaq']['pct']:+.2f}%)")
        _g_en.append(f"NASDAQ (prev close) {mi['nasdaq']['price']} ({mi['nasdaq']['pct']:+.2f}%)")
    if mi.get("vix"):
        _v = float(str(mi["vix"]["price"]).replace(",", ""))
        _vt_ko = "안정" if _v < 20 else "경계" if _v < 30 else "공포"
        _vt_en = "calm" if _v < 20 else "elevated" if _v < 30 else "fear"
        _g_ko.append(f"VIX {mi['vix']['price']} ({_vt_ko})")
        _g_en.append(f"VIX {mi['vix']['price']} ({_vt_en})")
    if mi.get("wti"):
        _g_ko.append(f"WTI 유가 ${mi['wti']['price']} ({mi['wti']['pct']:+.2f}%)")
        _g_en.append(f"WTI oil ${mi['wti']['price']} ({mi['wti']['pct']:+.2f}%)")
    mkt_line2_ko = " · ".join(_g_ko) if _g_ko else None
    mkt_line2_en = " · ".join(_g_en) if _g_en else None

    # Pull the SAME two methods the outlook block shows, so the recommendation is built on
    # BOTH consistently: Method 1 = ML, Method 2 = Analysis (호가/수급/박스권).
    m1, m2, price = {}, {}, None
    try:
        from services.assistant_tools import tool_two_method_view
        tm = tool_two_method_view(ticker=code, db=db) or {}
        m1 = tm.get("method1_ml") or {}
        m2 = tm.get("method2_analysis") or {}
        price = tm.get("live_price")
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

    # weighted fusion (news 1.0, flows 1.0, technicals 1.2, ML 1.0, Wave 1.0, YouTube 0.5)
    total = (news["score"] * 1.0 + flows["score"] * 1.0 + tech["score"] * 1.2
             + ml_score * 1.0 + wave_score * 1.0 + yt["score"] * 0.5)
    decision = "BUY" if total >= 2.5 else "SELL" if total <= -2.5 else "HOLD"
    conf = "높음" if abs(total) >= 4 else "보통" if abs(total) >= 2 else "낮음"
    conf_en = {"높음": "high", "보통": "medium", "낮음": "low"}[conf]

    # --- Confidence gate (M1.3): a decisive BUY/SELL must be BACKED BY THE METHODS,
    # not carried by news/technicals alone. Count how many of the 3 methods point the
    # decision's way; if none do (or 2+ oppose), ABSTAIN → 관망(신호 불충분). This is the
    # "only act when confident" rule — fewer but more trustworthy calls. ---
    gated = False
    _dir = 1 if decision == "BUY" else -1 if decision == "SELL" else 0
    if _dir != 0:
        _mdirs = [ml_score,
                  (1 if an_sig == "BUY" else -1 if an_sig == "SELL" else 0),
                  wave_score]
        agree_n = sum(1 for d in _mdirs if d == _dir)
        disagree_n = sum(1 for d in _mdirs if d == -_dir)
        if agree_n == 0 or disagree_n >= 2:
            decision, conf, conf_en, gated = "HOLD", "낮음", "low", True
        elif agree_n == 1 and conf == "높음":
            conf, conf_en = "보통", "medium"       # only 1 method backs it → cap confidence

    # --- Checklist gate (the boss's 100-item checklist, agent-run): conditions check on
    # top of the direction call. A deal-breaker (급락일/공매도 과열/악재 압도/손익비 미달)
    # vetoes a BUY the way the scalp tape-veto downgrades ENTER — direction may be right,
    # but conditions aren't. Score is appended to the answer; never breaks decide(). ---
    checklist = None
    chk_veto = False
    try:
        from services.checklist_engine import stock_scorecard
        checklist = stock_scorecard(db, code, news=news)
        if decision == "BUY" and checklist.get("deal_breakers"):
            decision, conf, conf_en, gated, chk_veto = "HOLD", "낮음", "low", True, True
    except Exception:
        checklist = None

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
        # trigger-only (the headline already says "don't buy now" — no need to repeat it)
        act_ko = (f"저항 {_pl(res)}원을 강하게 돌파하면 그때 매수를 검토하고, "
                  f"지지 {_pl(sup)}원이 깨지면 보유분은 비중 축소로 대응하세요.")
        act_en = (f"Revisit buying only if it clearly breaks resistance ₩{_pl(res)}; "
                  f"if support ₩{_pl(sup)} breaks, reduce any existing position.")

    en_name = {"SK하이닉스": "SK Hynix", "삼성전자": "Samsung Electronics",
               "삼성전기": "Samsung Electro-Mechanics", "SK스퀘어": "SK Square",
               "한미반도체": "Hanmi Semiconductor", "카카오": "Kakao"}.get(name, name)

    # extra data for the detailed write-up
    algo = m1.get("best_algorithm")
    lv = m2.get("levels") or {}
    pos = tech.get("pos_in_range")

    def _zone(lo, hi):
        # "–" not "~": two digit~digit ranges in one paragraph pair up as markdown
        # strikethrough in the chat widget and the tildes vanish from the display
        return f"{_pl(lo)}–{_pl(hi)}" if lo and hi else None
    buy_zone, sell_zone = _zone(lv.get("buy_lo"), lv.get("buy_hi")), _zone(lv.get("sell_lo"), lv.get("sell_hi"))

    def _clean(t):                                 # strip the 📈/📉/• prefix from a headline
        return (t or "").lstrip("📈📉• ").strip()
    heads = [_clean(t) for t in (news.get("titles") or [])][:3]

    dec_full_ko = {"BUY": "매수 (BUY)", "SELL": "매도 (SELL)", "HOLD": "보유 (HOLD)"}[decision]
    dec_full_en = {"BUY": "BUY", "SELL": "SELL", "HOLD": "HOLD"}[decision]
    if chk_veto:                    # checklist deal-breaker vetoed the BUY
        dec_full_ko = "관망 (체크리스트 결격)"
        dec_full_en = "WATCH (checklist deal-breaker)"
    elif gated:                     # abstained: methods didn't back the fusion
        dec_full_ko = "관망 (신호 불충분)"
        dec_full_en = "WATCH (insufficient method backing)"
    em_txt_ko = (f"예상 5일 변동 ±{abs(em)}%" if em is not None else "예상 변동 추정 불가")
    em_txt_en = (f"expected 5-day move ±{abs(em)}%" if em is not None else "expected move n/a")

    # FRIEND-STYLE direct answer to 'should I buy?' + POSITION SIZING ('how many').
    head_ko = {"BUY": "네 — 지금 분할로 매수하기 괜찮은 자리예요.",
               "SELL": "아니요 — 지금은 사지 말고, 오히려 비중을 줄일 때예요.",
               "HOLD": "지금 당장 사는 건 권하지 않아요 — 조금 더 확인하고 들어가는 게 좋아요."}[decision]
    head_en = {"BUY": "Yes — this is a reasonable spot to start buying (in tranches).",
               "SELL": "No — don't buy here; it's more a time to trim.",
               "HOLD": "Not right now — better to wait for confirmation before buying."}[decision]
    # price for share math: prefer the live price, else fall back to the last close so the
    # '몇 주?' answer always has a number even when live_price is momentarily unavailable.
    _px = price or lv.get("close") or tech.get("close")
    _bud = 10_000_000                                   # ₩10M reference budget
    _pct = {"높음": 15, "보통": 10, "낮음": 5}.get(conf, 8) if decision == "BUY" else 10

    def _size_ko(pct: int, buy_ctx: bool):
        if not _px:
            return None
        won = int(_bud * pct / 100)
        n = int(won // float(_px))
        if n >= 1:
            base = f"1,000만원의 {pct}%(≈{won // 10000:,}만원)면 약 {n}주 (현재가 {_pl(_px)}원)"
            return base + ("를 2~3회 나눠서 담으세요." if buy_ctx else " 정도가 적정 비중이에요.")
        return (f"1주가 {_pl(_px)}원으로 비싸 예산의 {pct}%({won // 10000:,}만원)로는 1주도 안 돼요 — "
                f"매수한다면 최소 1주(약 {_pl(_px)}원)부터 소액으로 시작하세요.")

    def _size_en(pct: int, buy_ctx: bool):
        if not _px:
            return None
        won = int(_bud * pct / 100)
        n = int(won // float(_px))
        if n >= 1:
            base = f"{pct}% of ₩10M (≈₩{won:,}) is ~{n} shares (at ₩{_pl(_px)})"
            return base + (", scaled in over 2–3 buys." if buy_ctx else " — a sensible size.")
        return (f"at ₩{_pl(_px)}/share it's pricey — {pct}% of ₩10M (₩{won:,}) buys less than 1 share; "
                f"start with just 1 share (~₩{_pl(_px)}) if you buy.")

    if decision == "BUY":
        _s, _se = _size_ko(_pct, True), _size_en(_pct, True)
        size_ko = (f"확신이 {conf}이라 종목당 투자금의 약 {_pct}%가 적당해요."
                   + (f" {_s}" if _s else "")
                   + f" 손절은 지지선 {_pl(sup)}원이 깨질 때로 잡으세요.")
        size_en = (f"Confidence is {conf_en}, so about {_pct}% of your stock budget per position."
                   + (f" {_se}" if _se else "")
                   + f" Put a stop if it loses support ₩{_pl(sup)}.")
    else:
        # Even when we say "don't buy now", answer the '몇 주?' question directly with a
        # REFERENCE size (10% of a ₩10M budget) so the user always gets a concrete number.
        _s, _se = _size_ko(10, False), _size_en(10, False)
        size_ko = ("지금은 신규 매수 보류를 권해요 (0주). "
                   + (f"참고로 매수한다면 {_s} " if _s else "")
                   + f"저항 {_pl(res)}원을 확실히 돌파하면 분할 진입하거나, 지지 {_pl(sup)}원에서 반등을 확인한 뒤 소량부터 담으세요.")
        size_en = ("Hold off on new buying for now (0 shares). "
                   + (f"For reference, {_se} " if _se else "")
                   + f"Only scale in once it clears resistance ₩{_pl(res)}, or after it holds support ₩{_pl(sup)}.")

    # SELL-TIMING focus ('언제 팔아야 해?'): override the headline + sizing block with an
    # EXIT plan built from the same levels — take-profit at the sell zone / resistance,
    # stop at support. So a holder gets 'when to sell', not the buy framing.
    _sell_focus = (focus == "sell")
    if _sell_focus:
        _tp = sell_zone or (f"{_pl(res)}" if res else None)
        head_ko = "매도(익절) 타이밍은 이렇게 잡으세요 — 목표가에서 분할 매도, 지지 이탈 시 손절."
        head_en = "Here's how to time your exit — scale out at the target, cut if support breaks."
        size_ko = ((f"1차 익절 목표 {_tp}원" if _tp else "1차 익절은 저항 부근") +
                   f", 최종 목표는 저항 {_pl(res)}원 부근이에요. 목표 도달 시 분할로 매도하고, "
                   f"지지 {_pl(sup)}원이 깨지면 미련 없이 손절하세요. "
                   + ("추세가 아직 살아 있으니 서둘러 전량 팔 필요는 없어요." if decision in ("BUY", "HOLD")
                      else "추세가 약해 반등 시 비중을 줄이는 걸 권해요."))
        size_en = ((f"First take-profit around ₩{_tp}" if _tp else "First take-profit near resistance") +
                   f", final target near resistance ₩{_pl(res)}. Scale out into the target and "
                   f"cut without hesitation if support ₩{_pl(sup)} breaks. "
                   + ("The trend still holds, so no need to dump it all at once." if decision in ("BUY", "HOLD")
                      else "The trend is weak — trim into any bounce."))

    # ---- COMPACT per-method write-up: verdict first, ONE brief reason, key numbers.
    # (Boss feedback: nobody wants the how-it-works lecture every answer — just
    # "did it say buy/sell/hold, and why, briefly.") ----
    ml_tag_ko = {"BUY": "매수", "SELL": "매도", "HOLD": "보유"}.get(ml_adv, "보유")
    ml_para_ko = (f"**→ {ml_tag_ko} ({ml_adv or 'HOLD'})** — {ml_why_ko}."
                  + (f" (5일 예상 ±{abs(em)}%" if em is not None else " (")
                  + (f" · 정확도 {acc_txt})" if acc is not None else ")")).replace(" ()", "")
    ml_para_en = (f"**→ {ml_adv or 'HOLD'}** — {ml_why_en}."
                  + (f" (5-day ±{abs(em)}%" if em is not None else " (")
                  + (f" · accuracy {acc_txt})" if acc is not None else ")")).replace(" ()", "")

    if pos is None:
        _box_ko = _box_en = ""
    elif pos > 70:
        _box_ko = f"현재가는 박스권의 {pos}% 지점(고점권)이라 추격 매수는 부담스럽고 눌림을 기다리는 편이 유리합니다."
        _box_en = f"price sits at {pos}% of the box (upper end), so chasing is risky — better to wait for a pullback."
    elif pos < 30:
        _box_ko = f"현재가는 박스권의 {pos}% 지점(저점권)이라 반등 여지가 있는 자리입니다."
        _box_en = f"price sits at {pos}% of the box (lower end), leaving room to bounce."
    else:
        _box_ko = f"현재가는 박스권의 {pos}% 지점(중간권)입니다."
        _box_en = f"price is around {pos}% of the box (mid-range)."
    _zones_ko = " ".join(filter(None, [f"매수 적정 구간은 {buy_zone}원," if buy_zone else None,
                                        f"차익 실현(매도) 구간은 {sell_zone}원입니다." if sell_zone else None]))
    _zones_en = " ".join(filter(None, [f"A reasonable buy zone is ₩{buy_zone};" if buy_zone else None,
                                       f"a take-profit zone is ₩{sell_zone}." if sell_zone else None]))
    an_tag_ko = {"BUY": "매수", "SELL": "매도", "WATCH": "관망", "HOLD": "관망"}.get(an_sig, "관망")
    an_tag_en = {"BUY": "BUY", "SELL": "SELL", "WATCH": "WATCH", "HOLD": "WATCH"}.get(an_sig, "WATCH")
    an_para_ko = (f"**→ {an_tag_ko} ({an_tag_en})** — {an_why_ko}. {_box_ko} {_zones_ko}").strip()
    an_para_en = (f"**→ {an_tag_en}** — {an_why_en}. {_box_en} {_zones_en}").strip()

    wv_tag_ko = {"BUY": "매수", "WATCH": "관망", "AVOID": "회피"}.get(wv, "관망")
    wave_para_ko = (f"**→ {wv_tag_ko} ({wv or 'WATCH'})**" + (f" · 파동점수 {_wsc}" if _wsc is not None else "")
                    + f" — {wave_why_ko}." + (f" {wave_zone_ko}." if wave_zone_ko else "")).strip()
    wave_para_en = (f"**→ {wv or 'WATCH'}**" + (f" · wave score {_wsc}" if _wsc is not None else "")
                    + f" — {wave_why_en}." + (f" {wave_zone_en}." if wave_zone_en else "")).strip()

    # NOTE (boss feedback 2026-07-03): the direct answer at the TOP is the one and only
    # conclusion — no repeated "최종 종합 판단" synthesis at the bottom. Methods follow as
    # proof. And a not-buy answer must NOT show a "how many" block (no-buy = 0 shares).

    # ---- plain-words WHY, right after the direct answer (boss: "add a little more
    # explanation with easy words"). 2-3 friendly sentences: how many signals back the
    # call, what the resistance('천장')/support('바닥') levels mean, what we're waiting for.
    _m_buy = sum(1 for v in (ml_score == 1, an_sig == "BUY", wave_score == 1) if v)
    _m_sell = sum(1 for v in (ml_score == -1, an_sig == "SELL", wave_score == -1) if v)
    if decision == "BUY":
        plain_ko = (f"쉽게 설명하면, 저희 3가지 방법 중 {_m_buy}개가 '사도 좋다'는 쪽을 가리키고 있어요. "
                    f"다만 아무리 좋아 보여도 한 번에 다 사지 말고 2~3번에 나눠 담는 게 안전하고, "
                    f"들어간 뒤에는 지지선 {_pl(sup)}원(최근 주가가 계속 버텨준 '바닥')이 깨지면 "
                    f"미련 없이 계획대로 손절하는 게 핵심이에요.")
        plain_en = (f"In plain words: {_m_buy} of our 3 methods point to 'buy'. Even so, don't buy "
                    f"everything at once — split it into 2–3 purchases. And once you're in, the most "
                    f"important rule is the exit: if support ₩{_pl(sup)} (the 'floor' the price has kept "
                    f"bouncing off) breaks, cut the loss as planned, no hesitation.")
    elif decision == "SELL":
        plain_ko = (f"쉽게 설명하면, 3가지 방법 중 {_m_sell}개가 '내림' 쪽을 가리키고 있어서 지금 사는 건 "
                    f"불리하고, 이미 갖고 있다면 반등이 나올 때 조금씩 줄이는 게 좋아요. "
                    f"저항 {_pl(res)}원(최근 계속 막혔던 '천장')을 다시 회복하기 전까지는 보수적으로 보세요.")
        plain_en = (f"In plain words: {_m_sell} of our 3 methods point down, so buying here is fighting "
                    f"the current. If you already hold it, trim gradually into any bounce, and stay "
                    f"cautious until the price reclaims resistance ₩{_pl(res)} (the 'ceiling' it kept "
                    f"failing at).")
    else:
        _why_bit_ko = ("특히 오늘은 체크리스트에서 결격 사유(시장 악재 등)가 발견돼 신규 매수를 막았어요. "
                       if chk_veto else "")
        _why_bit_en = ("On top of that, today's checklist found a deal-breaker (market-wide bad news etc.), "
                       "so new buying is blocked. " if chk_veto else "")
        plain_ko = (f"쉽게 설명하면, 저희 3가지 방법 중 확실하게 '사라'는 신호는 {_m_buy}개뿐이고 나머지는 "
                    f"관망이에요. 신호가 이렇게 엇갈릴 때 들어가면 결과를 운에 맡기는 셈이라, 방향이 "
                    f"확인될 때까지 기다리는 게 유리해요. {_why_bit_ko}"
                    f"저항 {_pl(res)}원은 최근 주가가 계속 막혔던 '천장'이고 지지 {_pl(sup)}원은 계속 "
                    f"버텨준 '바닥'인데 — 천장을 힘있게 뚫으면 상승 힘이 확인된 것이니 그때 매수를 다시 "
                    f"검토하면 되고, 반대로 바닥이 깨지면 더 내려갈 위험이 커졌다는 뜻이에요.")
        plain_en = (f"In plain words: only {_m_buy} of our 3 methods clearly say 'buy' right now — the rest "
                    f"say wait. Buying while the signals disagree is basically gambling on luck, so it pays "
                    f"to wait until the direction confirms. {_why_bit_en}"
                    f"Resistance ₩{_pl(res)} is the 'ceiling' the price kept failing at, and support "
                    f"₩{_pl(sup)} is the 'floor' it kept bouncing off — a strong break above the ceiling "
                    f"means real buying power (that's when to look again), while a break below the floor "
                    f"means the risk of falling further just grew.")

    # ① 직접 답변(= 유일한 최종 결론) + 대응 가이드 → ②(매수일 때만) 얼마나 / (매도타이밍) 언제
    # → ③ 근거(증거): 시장 → 뉴스 → 유튜브 → 방법 1/2/3 → 기술적 → 체크리스트. 끝에 요약 반복
    # 없음. (보스 피드백: 안 사는 답에 '얼마나'는 논리 모순 — 0주; 결론은 맨 위 한 번만.)
    _yt_links_md = " · ".join(f"[영상 보기 {i}]({u})" for i, u in enumerate(yt.get("links") or [], 1))
    _yt_links_md_en = " · ".join(f"[watch {i}]({u})" for i, u in enumerate(yt.get("links") or [], 1))
    ko_lines = [f"**{head_ko}**  ·  (추천: {dec_full_ko} · 확신 {conf})"]
    if _sell_focus:
        ko_lines += ["", "**언제 팔까? (매도 타이밍)**", f"· {size_ko}"]
    elif decision == "BUY":
        ko_lines += ["", "**얼마나 살까?**", f"· {size_ko}", "", plain_ko]
    else:
        # not-buy: NO sizing — the plain-words paragraph (with the trigger levels) instead
        ko_lines += ["", plain_ko]
    ko_lines += ["", f"**근거 — 방법별 증거 (확신 {conf} · {consensus_ko})**"]
    if mkt_line_ko:
        ko_lines += ["", "**① 시장 상황**", f"· {mkt_line_ko}"]
        if mkt_line2_ko:
            ko_lines += [f"· {mkt_line2_ko}"]
    ko_lines += ["", f"**② 뉴스 — {news_ko}**"]
    ko_lines += ([f"· {h}" for h in heads] if heads else ["· 특이 뉴스 없음"])
    if yt.get("note_ko"):
        ko_lines += ["", "**③ 유튜브 (시장 심리)**",
                     f"· {yt['note_ko']}" + (f" — {_yt_links_md}" if _yt_links_md else "")]
    ko_lines += [
        "", f"**④ 방법 1 — 머신러닝" + (f" ({algo})" if algo else "") + "**", ml_para_ko,
        "", "**⑤ 방법 2 — 분석 (호가·수급·박스권)**", an_para_ko,
    ]
    if has_wave:
        ko_lines += ["", "**⑥ 방법 3 — 파동 (엘리엇·피보나치)**", wave_para_ko]
    ko_lines += [
        "", "**기술적 지표**",
        f"{tech.get('summary_ko','중립')} · 지지 {_pl(sup)}원 / 저항 {_pl(res)}원"
        + (f" · 박스권 내 위치 {pos}%" if pos is not None else ""),
    ]
    if checklist:
        try:
            from services.checklist_engine import summary_line
            ko_lines += ["", summary_line(checklist) + "  _(전체는 \"종목명 체크리스트\"로 확인)_"]
        except Exception:
            pass
    ko_lines += ["",
                 "※ 3가지 방법과 뉴스·수급·기술적 지표를 종합한 참고 의견이며, 투자 권유나 수익 보장이 아닙니다."]
    ko = "\n".join(ko_lines)

    en_lines = [f"**{head_en}**  ·  (Recommendation: {dec_full_en} · confidence {conf_en})"]
    if _sell_focus:
        en_lines += ["", "**When to sell? (exit timing)**", f"- {size_en}"]
    elif decision == "BUY":
        en_lines += ["", "**How many?**", f"- {size_en}", "", plain_en]
    else:
        en_lines += ["", plain_en]
    en_lines += ["", f"**The evidence — method by method (confidence {conf_en} · {consensus_en})**"]
    if mkt_line_en:
        en_lines += ["", "**① Market**", f"- {mkt_line_en}"]
        if mkt_line2_en:
            en_lines += [f"- {mkt_line2_en}"]
    en_lines += ["", f"**② News — {news_en}**"]
    en_lines += ([f"- {h}" for h in heads] if heads else ["- no notable news"])
    if yt.get("note_en"):
        en_lines += ["", "**③ YouTube (market sentiment)**",
                     f"- {yt['note_en']}" + (f" — {_yt_links_md_en}" if _yt_links_md_en else "")]
    en_lines += [
        "", f"**④ Method 1 — Machine Learning" + (f" ({algo})" if algo else "") + "**", ml_para_en,
        "", "**⑤ Method 2 — Analysis (orderbook · flows · box)**", an_para_en,
    ]
    if has_wave:
        en_lines += ["", "**⑥ Method 3 — Wave (Elliott · Fibonacci)**", wave_para_en]
    en_lines += [
        "", "**Technicals**",
        f"{tech.get('summary_en','neutral')} · support ₩{_pl(sup)} / resistance ₩{_pl(res)}"
        + (f" · {pos}% through the range" if pos is not None else ""),
    ]
    if checklist:
        try:
            from services.checklist_engine import summary_line
            en_lines += ["", summary_line(checklist, en=True) + '  _(full card: ask "SK Hynix checklist")_']
        except Exception:
            pass
    en_lines += ["",
                 "Note: a reasoned synthesis of all 3 methods + news/flows/technicals — not investment advice or a guarantee."]
    en = "\n".join(en_lines)
    return {"ticker": code, "name": name, "decision": decision, "score": round(total, 1),
            "price": _px, "confidence": conf_en, "news": news, "flows": flows, "technicals": tech,
            "youtube": yt,
            "checklist": ({"score": checklist["score"], "max": checklist["max"],
                           "pct": checklist["pct"],
                           "deal_breakers": checklist["deal_breakers"]} if checklist else None),
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
