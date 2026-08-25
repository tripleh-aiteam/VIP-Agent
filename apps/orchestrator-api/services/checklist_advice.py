"""checklist_advice — "should I buy/sell X?" answered by the 100-item checklist, the
news judge, and the desk's own algorithm laws. NO ML anywhere (boss 2026-08-25: "when
we ask any advise again ML is coming out... it should analyze deeply using 100
checklist, news, then it should give answer").

BUY  : market preflight + the stock's checklist scorecard + daily-chart year zone +
       Qwen news + live flow → one verdict with the failing items named, plus the
       same "process" payload the reco desk animates in chat (the interactive
       investigation the boss asked to SEE).
SELL : the deployed desk laws applied to the holder's case (boss's exact words):
       continuously increasing → hold; selling zone (≥85% of year) → sell at the
       3rd blue; buying zone (≤15%) → do not sell, it is the buying zone.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from services.logger import log

_BUY_KW = ("살까", "사도", "사야", "매수할까", "살만", "매수 어때", "매수해도",
           "should i buy", "worth buying", "buy or not", "is it a buy", "can i buy",
           "good to buy", "ok to buy", "buy now?")
_SELL_KW = ("팔까", "팔아야", "매도할까", "매도해야", "익절", "팔지 말지", "팔아도",
            "should i sell", "sell now", "hold or sell", "sell or hold", "when to sell",
            "take profit", "매도 시점", "매도 타이밍", "정리할까", "던질까")
_HELD_KW = ("bought", "샀", "샀는데", "매수했", "보유 중", "보유중", "holding", "i hold",
            "my position", "들고 있")


def kind(transcript: Optional[str]) -> Optional[str]:
    """'buy' | 'sell' | None — which advice case this question is."""
    t = (transcript or "").lower()
    if not t:
        return None
    sell = any(k in t for k in _SELL_KW)
    buy = any(k in t for k in _BUY_KW)
    if sell and buy:
        return "sell"                       # "hold or sell" is the holder's question
    if sell or (any(k in t for k in _HELD_KW)
                and any(w in t for w in ("어떻게", "어떡", "어쩌", "how", "what", "now",
                                         "지금", "할까", "해야"))):
        return "sell"
    if buy:
        return "buy"
    # typo'd modal ("shgiuld I buy Sasmung") — a bare buy/sell verb plus a should-like
    # word is still an advice ask, never an order (orders are imperative-first)
    import difflib
    toks = re.findall(r"[a-z]+", t)
    if any(difflib.get_close_matches(w, ("should", "shall", "shud"), n=1, cutoff=0.72) for w in toks):
        if "buy" in toks:
            return "buy"
        if "sell" in toks:
            return "sell"
    return None


def _en(transcript, lang) -> bool:
    en = str(lang or "").lower().startswith("en")
    if not en and not re.search(r"[가-힣]", transcript or "") \
            and re.search(r"[a-zA-Z]", transcript or ""):
        en = True
    return en


def _candles(db, code: str) -> dict:
    """Recent daily candles → consecutive up days / consecutive down (blue) candles,
    counting TODAY's live change as the current candle when available."""
    out = {"blues": 0, "ups": 0, "today": None, "last3": []}
    try:
        from services.price_history import rows as _ph
        rws, _s = _ph(db, code, 6)
        changes = [r.get("change_pct") for r in rws if r.get("change_pct") is not None]
        try:
            from services.paper_desk import _chg_cache
            live = _chg_cache.get(code)
            if live is not None:
                out["today"] = float(live)
                if rws and abs(float(live)) < 30:
                    changes = [float(live)] + changes  # today's candle first
        except Exception:
            pass
        out["last3"] = changes[:3]
        for c in changes:                      # newest → oldest
            if c < 0:
                out["blues"] += 1
            else:
                break
        for c in changes:
            if c > 0:
                out["ups"] += 1
            else:
                break
    except Exception:
        pass
    return out


def build(db, transcript: Optional[str], lang: str) -> Optional[dict]:
    k = kind(transcript)
    if not k:
        return None
    from services.assistant_agent import _all_stocks_in_query
    stocks = _all_stocks_in_query(transcript)
    if not stocks:
        return None
    code, name = stocks[0]
    en = _en(transcript, lang)
    if en:
        try:
            from services.stock_resolver import display_name_en
            name = display_name_en(code) or name
        except Exception:
            pass
    try:
        if k == "buy":
            return _buy(db, code, name, en)
        return _sell(db, code, name, en)
    except Exception as e:
        log.warning(f"checklist_advice failed ({code}): {str(e)[:120]}")
        return None


def _zone_line(z, en: bool) -> str:
    if not z:
        return "일봉 위치: 데이터 부족" if not en else "Daily-chart zone: insufficient data"
    lab = {"buy": ("🟢 매수구간(연중 바닥권 ≤15%)", "🟢 BUYING zone (≤15% of the year range)"),
           "sell": ("🔴 매도구간(연중 고점권 ≥85%)", "🔴 SELLING zone (≥85% of the year range)"),
           "mid": ("중간 구간", "mid-range")}[z["zone"]]
    return (f"일봉 위치: 연중 범위의 {z['pos']}% — {lab[0]}" if not en
            else f"Daily-chart zone: {z['pos']}% of the year range — {lab[1]}")


def _buy(db, code: str, name: str, en: bool) -> dict:
    from services.checklist_engine import stock_scorecard
    from services.checklist_reco import _year_zone
    card = stock_scorecard(db, code)
    z = _year_zone(code)
    cn = _candles(db, code)
    mk = card.get("market") or {}
    st = card.get("stock") or {}
    breakers = card.get("deal_breakers") or []
    pct = st.get("pct")
    # news (Qwen judge — real, no ML)
    news_line = None
    try:
        from services.decision_agent import _news
        nw = _news(db, code, name) or {}
        if nw.get("score") is not None:
            _sc = int(nw["score"])
            news_line = (f"뉴스 판정(Qwen): {_sc:+d} (범위 −3~+3)" if not en
                         else f"News judge (Qwen): {_sc:+d} (range −3~+3)")
    except Exception:
        pass
    # ---- the THREE GATES, shown as the boss stated them (2026-08-25: "if score is
    # normal and if not in the selling zone and no bad news it should say final
    # decision as a buy"): score ≥55 · not the selling zone · no bad news
    _news_sc = None
    try:
        _news_sc = int(re.search(r"([+-]?\d+)", news_line or "").group(1)) if news_line else None
    except Exception:
        _news_sc = None
    g_score = pct is not None and pct >= 55
    g_zone = not (z and z["zone"] == "sell")
    g_news = _news_sc is None or _news_sc > -2
    _zp = f"연중 {z['pos']}%" if z else "확인 불가"
    _zp_en = f"{z['pos']}% of year" if z else "unknown"
    gates_ko = (f"{'✅' if g_score else '❌'} 점수 {'정상' if g_score else '미달'}"
                f"({pct if pct is not None else '?'}% {'≥' if g_score else '<'} 55) · "
                f"{'✅' if g_zone else '❌'} 매도구간 {'아님' if g_zone else '⚠️ 매도구간'}({_zp}) · "
                f"{'✅' if g_news else '❌'} 악재 {'없음' if g_news else '있음'}"
                f"(뉴스 {_news_sc:+d})" if _news_sc is not None else
                f"{'✅' if g_score else '❌'} 점수 {'정상' if g_score else '미달'}"
                f"({pct if pct is not None else '?'}% {'≥' if g_score else '<'} 55) · "
                f"{'✅' if g_zone else '❌'} 매도구간 {'아님' if g_zone else '⚠️ 매도구간'}({_zp}) · "
                f"✅ 악재 없음(수집 뉴스 없음)")
    gates_en = (f"{'✅' if g_score else '❌'} score {'OK' if g_score else 'below bar'}"
                f"({pct if pct is not None else '?'}% {'≥' if g_score else '<'} 55) · "
                f"{'✅' if g_zone else '❌'} {'not the selling zone' if g_zone else 'IN the selling zone'}({_zp_en}) · "
                + (f"{'✅' if g_news else '❌'} {'no bad news' if g_news else 'bad news'}(news {_news_sc:+d})"
                   if _news_sc is not None else "✅ no bad news (none collected)"))
    if breakers:
        verdict = (f"{gates_ko}\n🚫 **최종 판단: 매수 금지** — 결격 사유가 있습니다" if not en
                   else f"{gates_en}\n🚫 **FINAL DECISION: DO NOT BUY** — deal-breakers present")
        picked = False
    elif g_score and g_zone and g_news:
        _zx = (" (매수구간 — 법칙상 최적 자리)" if (z and z["zone"] == "buy") else "")
        verdict = ((f"{gates_ko}\n✅ **최종 판단: 매수**{_zx}") if not en
                   else (f"{gates_en}\n✅ **FINAL DECISION: BUY**"
                         + (" (buying zone — the law's best spot)" if (z and z["zone"] == "buy") else "")))
        picked = True
    else:
        _why = ("매도구간" if not g_zone else "점수 미달" if not g_score else "악재 뉴스")
        _why_en = ("the selling zone" if not g_zone else "the score" if not g_score else "bad news")
        verdict = ((f"{gates_ko}\n⚠️ **최종 판단: 대기** — {_why} 때문에 지금은 사지 않습니다") if not en
                   else (f"{gates_en}\n⚠️ **FINAL DECISION: WAIT** — {_why_en} blocks the buy for now"))
        picked = False
    # worst failing stock items (why)
    fails = [it for it in (st.get("items") or []) if it.get("ok") is False][:3]
    L = [f"🧭 **{'매수 판단' if not en else 'BUY decision'} — {name} ({code})**", "", verdict, ""]
    L.append((f"**1) 시장 체크(#11~40)**: {mk.get('score')}/{mk.get('max')}점 · "
              f"{'결격 없음' if not (mk.get('deal_breakers')) else '🚫 결격 ' + str(len(mk['deal_breakers'])) + '건'}")
             if not en else
             (f"**1) Market check (#11–40)**: {mk.get('score')}/{mk.get('max')} · "
              f"{'no deal-breakers' if not (mk.get('deal_breakers')) else '🚫 ' + str(len(mk['deal_breakers'])) + ' deal-breaker(s)'}"))
    _f_bit = ""
    if fails:
        _f_bit = (" — 급소: " if not en else " — weak spots: ") + \
            ", ".join(f"❌ #{it['no']} {it['q'] if not en else (it.get('q_en') or it['q'])}"[:60] for it in fails)
    L.append((f"**2) 종목 체크(#41~100)**: {st.get('score')}/{st.get('max')}점 ({pct}%)"
              if not en else
              f"**2) Stock check (#41–100)**: {st.get('score')}/{st.get('max')} ({pct}%)") + _f_bit)
    if breakers:
        for b in breakers[:3]:
            L.append(("   🚫 " if True else "") + f"#{b['no']} {b['q']} — {b['detail']}"[:110])
    L.append(f"**3) {_zone_line(z, en)}**")
    if news_line:
        L.append(f"**4) {news_line}**")
    if z and z.get("cur"):
        _t3 = " ".join(("🔴" if c > 0 else "🔵" if c < 0 else "⚪") for c in cn["last3"][::-1]) or "-"
        L.append((f"**5) 지금 흐름**: ₩{z['cur']:,.0f}"
                  + (f" ({z['chg']:+.2f}%)" if z.get("chg") is not None else "")
                  + f" · 최근 캔들 {_t3}") if not en else
                 (f"**5) Right now**: ₩{z['cur']:,.0f}"
                  + (f" ({z['chg']:+.2f}%)" if z.get("chg") is not None else "")
                  + f" · recent candles {_t3}"))
    L.append("")
    if picked and z and z.get("cur"):
        # the smart-assistant OFFER (boss 2026-08-25: "then as a smart people it
        # should say do you wanna help to buy") — "네" opens the order confirmation
        try:
            from services.chat_trade import advise_qty, budget, stash_offer
            q = advise_qty(z["cur"])
            stash_offer(code, name, en)
            L.append((f"🤝 **매수 도와드릴까요?** \"네\" 하시면 {q:,}주(예산 ₩{budget():,.0f} 기준) "
                      f"주문 확인을 바로 띄워드립니다 — 수량을 바꾸려면 \"{name} 10주 매수\"처럼 말씀하세요.")
                     if not en else
                     (f"🤝 **Want me to help you buy?** Say \"yes\" and I'll bring up the order "
                      f"confirmation for {q:,} shares (₩{budget():,.0f} budget) — or say "
                      f"\"buy {name} 10 shares\" for a custom size."))
        except Exception:
            pass
    L.append((f"📋 [전체 100문항 근거 🔍](evidence:{code}) · [차트 보기](chart:{code})" if not en
              else f"📋 [Full 100-item evidence 🔍](evidence:{code}) · [Open chart](chart:{code})"))
    # the same animated "investigation" the reco desk shows (boss: "we can see it is
    # using all checklist then according to checklist it is advising us")
    proc = None
    try:
        score = pct
        groups = None
        try:
            from services.checklist_reco import _ranking
            row = next((r for r in (_ranking() or {}).get("rows", []) if r.get("code") == code), None)
            if row:
                score = row.get("score") or pct
                groups = row.get("groups")
        except Exception:
            pass
        proc = {"mode": "advice",
                "market": [{"no": it.get("no"), "ok": it.get("ok"), "q": it.get("q"),
                            "q_en": it.get("q_en"), "detail": it.get("detail")}
                           for it in (mk.get("items") or [])],
                # the stock's OWN checked items — the real 100-item walk the boss
                # wants to WATCH happening (2026-08-25: "show real process in the
                # chat that our agent is checking 100 checklist")
                "stock_items": [{"no": it.get("no"), "ok": it.get("ok"), "q": it.get("q"),
                                 "q_en": it.get("q_en"), "detail": it.get("detail")}
                                for it in (st.get("items") or [])],
                "zone": ({"pos": z.get("pos"), "zone": z.get("zone")} if z else None),
                "news": _news_sc,
                "verdict": ("매수" if picked else ("매수 금지" if breakers else "대기")),
                "verdict_en": ("BUY" if picked else ("DO NOT BUY" if breakers else "WAIT")),
                "candidates": [{"code": code, "name": name, "score": score, "groups": groups}],
                "picked": [code] if picked else [], "n": 1}
    except Exception:
        proc = None
    return {"reply": "\n".join(L), "process": proc}


def _sell(db, code: str, name: str, en: bool) -> dict:
    from services.checklist_reco import _year_zone
    z = _year_zone(code)
    cn = _candles(db, code)
    blues, rising = cn["blues"], cn["ups"] >= 1 and (cn["today"] is None or cn["today"] > 0)
    # my position on the desk (if any) — entry, size, P&L
    pos_line = None
    try:
        from sqlalchemy import text
        r = db.execute(text("SELECT qty, avg_price FROM paper_desk_positions WHERE ticker=:t"),
                       {"t": code}).fetchone()
        if r and int(r[0] or 0) > 0 and z and z.get("cur"):
            qy, avg = int(r[0]), float(r[1])
            pnl = (z["cur"] / avg - 1) * 100
            pos_line = ((f"내 포지션: {qy:,}주 @ ₩{avg:,.0f} → 현재 ₩{z['cur']:,.0f} ({pnl:+.2f}%)")
                        if not en else
                        (f"My position: {qy:,} shares @ ₩{avg:,.0f} → now ₩{z['cur']:,.0f} ({pnl:+.2f}%)"))
    except Exception:
        pass
    zone = z["zone"] if z else "mid"
    # ---- the boss's own laws, verbatim logic
    if zone == "buy":
        verdict = ("🟢 **팔지 마세요 — 매수구간입니다** (연중 바닥권 ≤15%). 법칙상 바닥권은 "
                   "3번째 빨간 캔들에 '사는' 구간이지 파는 구간이 아닙니다."
                   if not en else
                   "🟢 **DO NOT SELL — it is the BUYING zone** (≤15% of the year range). "
                   "The law buys at the 3rd red here; it never sells the bottom.")
    elif zone == "sell":
        if blues >= 3:
            verdict = ("🔴 **매도** — 매도구간(연중 ≥85%)에서 3번째 파란 캔들입니다. "
                       "법칙: 전량 매도(분할 없이)."
                       if not en else
                       "🔴 **SELL** — 3rd blue candle inside the SELLING zone (≥85% of year). "
                       "The law: sell ALL at once, no slicing.")
        elif rising:
            verdict = ("✋ **아직 보유** — 계속 오르는 중입니다. 오름이 멈추고 파란 캔들 "
                       "3개째가 나오면 그때 전량 매도합니다 (매도구간 법칙)."
                       if not en else
                       "✋ **HOLD for now** — it is still rising. When the rise stops, sell ALL "
                       "at the 3rd blue candle (the selling-zone law).")
        else:
            verdict = ((f"⚠️ **매도 준비** — 매도구간에서 하락이 시작됐습니다 (파란 캔들 {max(blues,1)}개째). "
                        f"3번째 파란 캔들에 전량 매도하세요.")
                       if not en else
                       (f"⚠️ **PREPARE TO SELL** — the drop has started inside the selling zone "
                        f"(blue candle #{max(blues,1)}). Sell ALL at the 3rd blue."))
    else:
        if rising:
            verdict = ("✋ **보유** — 계속 오르는 동안은 팔지 않습니다 (알고3 법칙: 상승이 멈추고 "
                       "3번째 파란 캔들에 전량 매도)."
                       if not en else
                       "✋ **HOLD** — while it keeps rising we do not sell (Algo-3 law: sell ALL "
                       "at the 3rd blue after the rise stops).")
        elif blues >= 3:
            verdict = ("🔴 **매도** — 파란 캔들 3개째, 3rd-blue 법칙 충족."
                       if not en else
                       "🔴 **SELL** — 3rd blue candle; the 3rd-blue law is met.")
        else:
            verdict = ((f"✋ **관망 보유** — 추세 미확정 (파란 캔들 {blues}개째). "
                        f"3번째 파란 캔들 전까지는 보유가 법칙입니다.")
                       if not en else
                       (f"✋ **HOLD & WATCH** — trend not decided yet (blue candle #{blues}). "
                        f"The law holds until the 3rd blue."))
    _t3 = " ".join(("🔴" if c > 0 else "🔵" if c < 0 else "⚪") for c in cn["last3"][::-1]) or "-"
    L = [f"🧭 **{'매도 판단' if not en else 'SELL decision'} — {name} ({code})**", "", verdict, ""]
    if pos_line:
        L.append(f"**1) {pos_line}**")
    L.append((f"**{'2' if pos_line else '1'}) 지금 흐름**: "
              + (f"오늘 {cn['today']:+.2f}% · " if cn.get("today") is not None else "")
              + f"연속 상승 {cn['ups']}일 · 파란 캔들 연속 {blues}개 · 최근 캔들 {_t3}")
             if not en else
             (f"**{'2' if pos_line else '1'}) Right now**: "
              + (f"today {cn['today']:+.2f}% · " if cn.get("today") is not None else "")
              + f"{cn['ups']} straight up days · {blues} straight blue candles · recent {_t3}"))
    L.append(f"**{'3' if pos_line else '2'}) {_zone_line(z, en)}**")
    L.append((f"**{'4' if pos_line else '3'}) 적용 법칙**: 계속 오르면 보유 → 상승이 멈추면 3번째 파란 캔들에 매도 · "
              f"매도구간(≥85%)은 전량 매도 · 매수구간(≤15%)은 매도 금지")
             if not en else
             (f"**{'4' if pos_line else '3'}) The laws applied**: keep holding while it rises → sell at the "
              f"3rd blue after the rise stops · selling zone (≥85%) sells ALL · buying zone (≤15%) never sells"))
    L.append("")
    if "매도" in verdict.split("\n")[0] or "SELL**" in verdict:
        L.append((f"🧾 팔려면: **\"{name} 매도\"** — 보유 전량 기준 주문 확인이 뜨고 \"네\"로 체결됩니다."
                  if not en else
                  f"🧾 To sell: say **\"sell {name}\"** — a whole-position confirmation appears; \"yes\" executes."))
    L.append((f"📋 [근거 🔍](evidence:{code}) · [차트 보기](chart:{code})" if not en
              else f"📋 [Evidence 🔍](evidence:{code}) · [Open chart](chart:{code})"))
    return {"reply": "\n".join(L), "process": None}
