"""analyst_answer.py — the SMART-ANALYST lane (boss 2026-07-16).

His complaint: every stock question got squeezed into the buy/hold/sell
recommendation format. "Our chatbot must be smart — first read, then think,
then answer — GPT/Claude quality. And unlike ChatGPT, ours HAS Kiwoom."

So: analytical/explanatory stock questions (ETF rebalancing mechanics, close
quality, supply-demand interpretation, '이게 무슨 의미야?', 'does that mean
tomorrow rises?') go HERE — a strong LLM briefed with a live Kiwoom/Naver/news
data pack, free to shape the answer to the question. Action questions
(살까? 팔까? should I buy?) keep the existing 3-method decision format.

Served from VIP's brain — the AI Advisor relays here, so both surfaces are
identical in meaning and format, KO and EN alike.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("vip.analyst")
KST = ZoneInfo("Asia/Seoul")

# ---- detector ------------------------------------------------------------- #
# stock/market context words — the question must be about the market at all
_MARKET_CTX = re.compile(
    r"(etf|kodex|tiger|레버리지|leverag|인버스|inverse|주식|증시|시장|지수|코스피|코스닥|"
    r"kospi|kosdaq|선물|옵션|futures|option|수급|외국인|외인|기관|프로그램|공매도|"
    r"리밸런싱|rebalanc|종가|시초가|마감|closing|auction|동시호가|호가|orderbook|order book|"
    r"거래량|volume|stock|market|semiconductor|반도체|캔들|candle|윗꼬리|wick|갭|gap|"
    r"차트|그래프|chart|graph|이동평균|이평선|moving average|지지선|저항선|support|resistance|"
    r"추세선|trendline|rsi|macd|볼린저|bollinger|분봉|일봉|주봉)",
    re.IGNORECASE)

# analytical cues — asking to understand/interpret/analyze, not to be told what to do
_ANALYSIS_CUE = re.compile(
    r"(왜|의미|뜻|뜻인가|해석|분석|영향|이유|어떻게 보|어떻게 해석|시나리오|확률|"
    r"~?라는데|이라는데|다는데|하면 .*(오를|내릴|되)|경우|가능성|평가|점수|근거|"
    r"유효|수정|재평가|맞나|맞는지|다시 (봐|평가)|"
    r"차트|그래프|chart|graph|"  # naming the graph IS asking for graph analysis
    r"why|mean(s|ing)?|interpret|analy[sz]e|analysis|explain|impact|effect|implication|"
    r"what happens|does (that|this|it)|if .* (rise|fall|drop|mean)|scenario|probab|"
    r"how do (institutions|quants|funds)|revise|still hold|같은 방식|same way)",
    re.IGNORECASE)

# action words — if he's asking WHAT TO DO, the decision engine keeps the question
_ACTION = re.compile(
    r"(살까|팔까|사도|매수할|매도할|매수해|매도해|사야|팔아야|사면 돼|팔면 돼|홀딩할까|"
    r"들어가도|진입할까|should i (buy|sell|hold)|buy now|sell now|worth buying|"
    r"추천해|추천좀|뭘 사|뭐 사|what should i (buy|trade))",
    re.IGNORECASE)


def is_analysis_question(text: str, has_stock: bool = False) -> bool:
    """has_stock: caller already resolved a stock name in the question — that
    counts as market context ('analyze Samsung Electronics the same way')."""
    t = (text or "").strip()
    if len(t) < 8 or _ACTION.search(t):
        return False
    return bool((has_stock or _MARKET_CTX.search(t)) and _ANALYSIS_CUE.search(t))


# ---- SIMPLE INFO requests (boss 2026-07-16 round 2): "futures investor volume?"
# style questions were answered with essays. These are DATA-FLOW asks — a number
# plus brief context is the right answer. Price-only asks are EXCLUDED (the fast
# deterministic price intercepts own those).
_DATA_NOUN = re.compile(
    r"(수급|순매수|순매도|매수량|매도량|거래량|거래대금|선물|옵션|프로그램|공매도|"
    r"외국인|외인|기관|개인|투자자별|미결제|"
    r"net (buy|sell)|trading volume|투자자|investor|futures|options|program trading|"
    r"short (selling|interest)|open interest|foreign(ers)?|institution)",
    re.IGNORECASE)
_DATA_ASK = re.compile(
    r"(얼마|몇 |알려|현황|어떻게 돼|어떻게 되|보여줘|확인해|정리해|"
    r"how (much|many)|what (is|was|are)|tell me|show me|current|today'?s)",
    re.IGNORECASE)


def is_simple_data_question(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 6 or _ACTION.search(t) or _ANALYSIS_CUE.search(t):
        return False
    return bool(_DATA_NOUN.search(t) and _DATA_ASK.search(t))


# ---- live data pack -------------------------------------------------------- #
def _fmt(n) -> str:
    try:
        return f"{float(n):,.0f}"
    except Exception:
        return str(n)


def _stock_pack(db, code: str, name: str) -> str:
    lines: list[str] = [f"### {name} ({code})"]
    try:
        from services import kiwoom_rest as kr
        q = kr.current_price(code) or {}
        if q.get("price"):
            lines.append(
                f"- 현재/종가 {_fmt(q['price'])}원 ({q.get('change_pct', '?')}% vs 전일 "
                f"{_fmt(q.get('prev_close'))}) · 시가 {_fmt(q.get('open'))} · "
                f"고가 {_fmt(q.get('high'))} · 저가 {_fmt(q.get('low'))} · "
                f"거래량 {_fmt(q.get('volume'))}주")
    except Exception:
        pass
    try:
        from services.micro_trend import micro_read
        m = micro_read(db, code) or {}
        if m.get("line_ko"):
            lines.append(f"- 단기 흐름(5분봉): {m['line_ko']}")
    except Exception:
        pass
    try:
        from services import kiwoom_rest as kr
        ob = kr.order_book(code, ttl=10) or {}
        if ob.get("imbalance") is not None:
            lines.append(
                f"- 호가 잔량: 매수 {_fmt(ob.get('tot_bid'))} vs 매도 {_fmt(ob.get('tot_ask'))} "
                f"(불균형 {ob['imbalance']:+.2f}; +는 매수우위)")
    except Exception:
        pass
    try:
        from services import kiwoom_rest as kr
        fl = kr.investor_flows(code) or {}
        if fl.get("foreign") is not None or fl.get("institution") is not None:
            lines.append(
                f"- 당일 수급(잠정): 외국인 {_fmt(fl.get('foreign'))} · "
                f"기관 {_fmt(fl.get('institution'))} · 개인 {_fmt(fl.get('individual'))}")
    except Exception:
        pass
    try:
        from services.decision_agent import _news
        nw = _news(db, code, name) or {}
        titles = (nw.get("titles") or [])[:3]
        if titles:
            # strip markdown links — the LLM needs the headline text only
            plain = [re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t) for t in titles]
            lines.append("- 최근 뉴스: " + " / ".join(plain))
        if nw.get("score") is not None:
            lines.append(f"- 뉴스 무드 점수: {int(nw['score']):+d} (범위 −3~+3)")
    except Exception:
        pass
    # recent DAILY rows + fundamentals (deep audit 2026-08-25: with no past rows in the
    # pack, "why did it drop yesterday" got an invented 2024 story — now the real
    # yesterday is right here, and valuation questions have real figures too)
    try:
        from services.price_history import rows as _ph_rows
        rws, _src = _ph_rows(db, code, 6)
        if rws:
            lines.append("- 최근 일별(우리 DB): " + " / ".join(
                f"{r.get('date')} 종가 {_fmt(r.get('close'))} "
                f"({r.get('change_pct') if r.get('change_pct') is not None else '?'}%) "
                f"거래량 {_fmt(r.get('volume'))}" for r in rws[:6]))
    except Exception:
        pass
    try:
        from services.naver_stock import fundamentals as _fund
        f = _fund(code) or {}
        info = f.get("info") or {}
        if info:
            lines.append(f"- 기본지표: PER {info.get('per')} · PBR {info.get('pbr')} · "
                         f"시총 {info.get('marketValue')} · 배당수익률 {info.get('dividendYieldRatio')} · "
                         f"52주 {info.get('lowPriceOf52Weeks')}~{info.get('highPriceOf52Weeks')} · "
                         f"외국인 {info.get('foreignRate')}")
        if f.get("target_mean"):
            lines.append(f"- 증권사 컨센서스 목표가 {f.get('target_mean')}원 "
                         f"(평균 투자의견 {f.get('recomm_mean')}/5)")
    except Exception:
        pass
    return "\n".join(lines)


def _market_pack(db) -> str:
    lines: list[str] = ["### 시장 전체"]
    try:
        from services.micro_trend import _market_chg_pct
        mkt = _market_chg_pct()
        if mkt is not None:
            lines.append(f"- KOSPI(KODEX200 기준) 당일 {mkt:+.2f}%")
    except Exception:
        pass
    try:
        from services.market_investor_flows import market_flows
        mf = market_flows() or {}
        ko = mf.get("line_ko") or mf.get("summary_ko") or ""
        if ko:
            lines.append(f"- 시장 수급: {ko}")
        elif mf.get("foreign") is not None:
            lines.append(f"- 시장 수급(원): 외국인 {_fmt(mf.get('foreign'))} · "
                         f"기관 {_fmt(mf.get('institution'))}")
    except Exception:
        pass
    return "\n".join(lines) if len(lines) > 1 else ""


def build_data_pack(db, stocks: list[tuple[str, str]]) -> str:
    now = datetime.now(KST)
    parts = [f"[실시간 데이터 — {now.strftime('%Y-%m-%d %H:%M')} KST, 키움/네이버 실계정 소스. "
             f"장중 09:00-15:30, 잠정치 포함]"]
    for code, name in stocks[:3]:
        try:
            parts.append(_stock_pack(db, code, name))
        except Exception:
            pass
    # 📈 the GRAPH itself (boss 2026-07-16): 1-min + 5-min + daily multi-timeframe
    # read so every analyst answer can reason from the actual chart movement
    for code, name in stocks[:2]:
        try:
            from services.chart_analysis import chart_read
            cr = chart_read(db, code, name)
            if cr.get("ok"):
                parts.append(cr["block_ko"])
        except Exception:
            pass
    mp = _market_pack(db)
    if mp:
        parts.append(mp)
    return "\n\n".join(parts)


# ---- the analyst persona ---------------------------------------------------- #
_SYSTEM = """You are a veteran Korean-market analyst (20 years: 수급, market
microstructure, derivatives, ETF mechanics) answering for a demanding CEO.

HOW TO ANSWER — read, think, then write:
1. READ the question and the LIVE DATA PACK carefully. The data is real
   (Kiwoom/Naver, timestamps included) — something ChatGPT does not have.
2. THINK about what is actually being asked. Answer THAT question. Do NOT
   force a buy/hold/sell recommendation unless the user asked what to do.
3. SHAPE the answer to fit the question — a mechanics question gets an
   explanation with the cases laid out; a "what does today mean" question
   gets an interpretation grounded in the numbers; a scenario question gets
   scenarios with rough probabilities and the key levels to watch.
4. NEVER invent a date, price, event, or reason that is not in the data pack
   or the conversation. Recent-past facts come from the 최근 일별 rows in the
   pack (the dates there are correct — never restate them with a different
   year). If the pack lacks the asked figure, say exactly that in one sentence
   and give the closest real figure — never apologize into a non-answer.

STYLE:
- Conclusion first, then the reasoning, in clean sections or short numbered
  points. Vary the structure naturally — never a fixed template.
- Weave the ACTUAL numbers from the data pack into the reasoning (prices,
  %, flows, imbalance). Distinguish 사실(data) from 해석(inference).
- Be honest about limits: provisional intraday flows vs final data, what
  cannot be concluded from a single indicator, and revise prior views when
  the user gives new facts.
- Depth like a human analyst's note (roughly 250-500 words), not a one-liner
  and not a wall of boilerplate. Plain language a non-technical person can
  follow; technical terms briefly unpacked.
- You may close with ONE natural follow-up offer (e.g. a deeper check you
  could run), never a sales pitch.
- ANSWER ENTIRELY IN {LANG}. ({lang_note}) NEVER mix languages in one answer:
  no English sentences inside a Korean answer and vice versa. Stock names,
  tickers and numbers keep their natural form.
- If the conversation history already answered a similar question (even in the
  other language), STILL give the complete standalone answer in {LANG} — never
  reply "as mentioned above" and never shorten because an earlier turn covered it.
  KO and EN versions of the same question must be equally complete.
- Today is {today} (KST). Korean market hours 09:00-15:30.
{depth_rule}"""

_DEPTH_FULL = """DEPTH: this is an ANALYSIS question — full analyst treatment.
English: roughly 250-500 words. Korean: 900-1,800 characters (한국어 답변도
영어와 똑같이 완전하고 깊게 — 한국어라고 짧게 쓰지 마세요). The same question
asked in Korean or English must receive equally complete answers."""

_DEPTH_CONCISE = """DEPTH: this is a SIMPLE INFORMATION request — the user wants
the specific data, not an essay. Lead with the requested number(s)/facts from
the data pack, then 3-5 sentences of essential context (what the number means,
timestamp/provisional caveat, one notable comparison), and STOP.
English: 60-140 words. Korean: 200-400 characters, 4-6 sentences (한 줄짜리
답은 금지 — 숫자 뒤에 맥락 문장 3~5개를 반드시 붙이세요). If the data pack does
not contain the requested figure, say so plainly in one sentence and give the
closest available number instead of speculating."""


def answer(db, question: str, lang: str, stocks: list[tuple[str, str]],
           history: Optional[list] = None, concise: bool = False) -> Optional[str]:
    """The smart-analyst reply, or None so the caller falls through.
    concise=True → simple-information mode (number-first, 60-140 words)."""
    try:
        pack = build_data_pack(db, stocks)
        from services.llm_client import chat_completion_sync
        lang_note = ("한국어로만 답하세요 — 영어 문장 금지" if lang == "ko"
                     else "Answer only in English — no Korean sentences; Korean stock names as-is are fine")
        system = _SYSTEM.replace("{LANG}", "KOREAN" if lang == "ko" else "ENGLISH") \
                        .replace("{lang_note}", lang_note) \
                        .replace("{today}", datetime.now(KST).strftime("%Y-%m-%d %A %H:%M")) \
                        .replace("{depth_rule}", _DEPTH_CONCISE if concise else _DEPTH_FULL)
        msgs: list[dict[str, str]] = []
        for h in (history or [])[-6:]:
            r = h.get("role") if isinstance(h, dict) else None
            c = h.get("content") if isinstance(h, dict) else None
            if r in ("user", "assistant") and c:
                msgs.append({"role": r, "content": str(c)[:1500]})
        msgs.append({"role": "user",
                     "content": f"{pack}\n\n---\n질문: {question}"})
        out = chat_completion_sync(system, msgs,
                                   max_tokens=(600 if concise else 1800),
                                   temperature=0.4,
                                   model="claude-sonnet-4-6", prefer_paid=True)
        # Korean answers come back too short from most models (boss's screenshot:
        # KO version of the same question got a fraction of the EN answer) —
        # one strict retry when under the floor.
        floor = (150 if concise else 880) if lang == "ko" else (200 if concise else 900)
        if out and "[LLM" not in out[:20] and len(out.strip()) < floor:
            msgs.append({"role": "assistant", "content": out})
            msgs.append({"role": "user", "content": (
                "답변이 너무 짧습니다. 위 DEPTH 지침의 분량을 지켜, 데이터 숫자를 녹여서 "
                "완전한 답변으로 다시 작성하세요." if lang == "ko" else
                "That answer is too short. Rewrite it in full, following the DEPTH "
                "instruction's length, weaving in the actual numbers.")})
            out2 = chat_completion_sync(system, msgs,
                                        max_tokens=(600 if concise else 1800),
                                        temperature=0.4,
                                        model="claude-sonnet-4-6", prefer_paid=True)
            if out2 and "[LLM" not in out2[:20] and len(out2.strip()) > len(out.strip()):
                out = out2
        if out and "[LLM" not in out[:20]:
            return out.strip()
        return None
    except Exception as e:
        logger.warning("analyst answer failed: %s", str(e)[:120])
        return None
