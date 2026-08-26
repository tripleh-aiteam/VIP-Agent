"""
assistant_agent — the tool-calling loop that powers /chat/agent.

Flow:
  1. Receive user transcript + optional page context.
  2. Pass the FULL tool catalog (from assistant_tools.TOOL_REGISTRY)
     + manifest summary to the LLM as a system prompt.
  3. LLM returns either:
       - {"tool": "<name>", "args": {...}}     ← invoke a tool
       - {"answer": "<text>"}                  ← direct answer (no tool needed)
  4. If a tool is picked, execute it server-side.
  5. Feed the tool result back to the LLM for final answer composition.
  6. Return: { reply, action, tool_used, tool_result, intent }

The frontend widget receives the same shape as /chat/voice so it can use
this endpoint as a drop-in upgrade.

This module is provider-agnostic via llm_client. Defaults to Groq Llama
3.3 70B for sub-second latency.
"""

from __future__ import annotations

import json
import os
import re as _re

# ⚡ Stock delegation guard (boss 2026-07-22 perf): this server delegates some stock
# questions to the Render Stock backend, which RELAYS BACK to its own VIP_ORCHESTRATOR
# (the now-suspended Render VIP) — so a delegated call could hang up to 45s × 2 = 90s.
# On this local server VIP answers price/history/prediction/advice itself, so:
#   • STOCK_DELEGATION=off  → skip the Render delegation entirely (default here), and
#   • every remaining delegation call is capped at STOCK_DELEGATION_TIMEOUT seconds with
#     a graceful local fallback, so a slow Render can never hold an answer hostage.
_STOCK_DELEGATE = os.environ.get("STOCK_DELEGATION", "on").strip().lower() not in ("off", "0", "no", "false")
try:
    _STOCK_TIMEOUT = max(2.0, float(os.environ.get("STOCK_DELEGATION_TIMEOUT", "5")))
except Exception:
    _STOCK_TIMEOUT = 5.0
from typing import Any, Optional

from sqlalchemy.orm import Session

from services.logger import log
from services.llm_client import chat_completion_sync
from services.assistant_tools import (
    TOOL_REGISTRY, list_tool_schemas, execute_tool,
)
from services.assistant_manifest import (
    pages_summary_for_llm, agents_summary_for_llm, get_agent_identity,
)


# ============================================================================
#  Agent profiles — site map + capabilities + data-freshness notes
# ============================================================================
# Per-agent context the LLM gets at the top of every system prompt, so it
# knows the full surface area of whichever app the user is in: every page
# that exists, what each page shows, what data is real-time vs persisted,
# and what the user can ask the assistant to do here. Lets the assistant
# behave like a Claude Chrome extension that has been pre-briefed on the
# app it's embedded in — no guessing, no "I'm not sure if this page
# exists" hedging.
AGENT_PROFILES: dict[str, dict[str, Any]] = {
    "vip": {
        "name": "VIP AI Platform",
        "tagline": "Enterprise multi-agent orchestration with digital twins for each employee.",
        "pages": [
            "/chatbot — Assistant (this chat) + Messages (DMs with each twin) + Calls (voice) + Add knowledge",
            "/dashboard — high-level KPIs across all twins",
            "/twins — list of every digital twin, each with mode (shadow/active/handoff), specialty, readiness tier",
            "/control-room — live twin status grid + traffic indicators",
            "/task-board — task queue across all twins, kanban-style",
            "/agents — registered domain agents (Asset / Stock / Realty / AIGlass)",
            "/workflows — saved multi-step automations",
            "/reports — daily / weekly summaries (boss + per-twin)",
            "/judgement — pending approval decisions",
            "/a2a — agent-to-agent coordination monitor",
            "/meetings — multi-twin meeting rooms + meeting-notes",
            "/settings — account + system configuration",
        ],
        "data_freshness": "Persisted state from Supabase + recent activity log, refreshed every page load. Twin modes auto-switch every 1 minute via scheduler.",
        "user_role": "the boss / CEO — has full read+write on every twin.",
    },
    "realty": {
        "name": "제주 영교도시 부동산 에이전트 (Realty Agent)",
        "tagline": "Jeju English Town real-estate intelligence — market dashboard, evaluation, cashflow.",
        "pages": [
            "/ — home",
            "/market — 시장 대시보드 (market dashboard, district-level prices and yields)",
            "/evaluate — 분양성 평가 (property salability / yield evaluation)",
            "/cashflow — 💸 Cash Flow builder (rental scenarios, NOI, IRR)",
            "/pnl-builder — P&L builder",
            "/sources — 📚 근거자료 (data sources)",
            "/chatbot — Assistant (this chat) + KakaoTalk inbox + Add knowledge",
            "/monitor — 시장 모니터 (market monitor)",
        ],
        "data_freshness": "Persisted market snapshot from Q1 2026. Reference report dated 2026-04-20 (국토부 / 네이버 / 호갱노노 transactions).",
        "user_role": "real-estate consultant.",
    },
    "asset": {
        "name": "Asset Agent — 종합 자산관리 대시보드",
        "tagline": "Integrated asset-management: portfolio + leases + tenants + cash + tax + legal.",
        "pages": [
            "/chatbot — Assistant + Messages + Calls + Add knowledge",
            "/ — dashboard (asset status, contract expirations, rent income, delinquencies)",
            "/ops — operations center",
            "/portfolio — 자산현황 (whole-portfolio overview)",
            "/portfolio/evaluation — 월별 평가 (monthly evaluation)",
            "/portfolio/analysis — 포트폴리오 분석 (portfolio analysis)",
            "/commercial-analysis — 상권/MD 분석",
            "/properties — 건물관리 (building management)",
            "/maintenance — 유지보수",
            "/lease — 임대관리 (lease management)",
            "/tenants — 임차인 (tenants list)",
            "/renewal — 재계약/매물 (renewals + listings)",
            "/cash — 자금관리 (cashflow + bank balances)",
            "/bank — 수납 관리",
            "/tax — 세금관리 (tax records and filings)",
            "/legal — 법적관리 (contracts + compliance)",
            "/meetings — 회의록 (meeting notes)",
            "/approvals — 결재 (approvals)",
            "/notifications — 알림 이력",
            "/settings — system settings",
        ],
        "data_freshness": "Persisted from Postgres (Drizzle), refreshed every page load. Multi-tenant by tenant_id.",
        "user_role": "owner / manager of an asset-management portfolio.",
    },
    "stock": {
        "name": "OASIS Stock Advisor",
        "tagline": "AI investment advisory: market signals, recommendations, investor flow, intraday signals, journal analysis.",
        "pages": [
            "/chatbot — Assistant (this chat)",
            "/recommendations — 추천 기록 (recommendation history of past picks)",
            "/investment/investor-flow — 투자자 수급 (외국인 / 기관 / 개인 net buy/sell, by symbol)",
            "/investment/intraday — 장중 신호 (intraday signals — bullish/bearish/risk score)",
            "/investment/journal — 거래일지 분석 (your trade journal, analysis of past trades)",
            "/news — 시장 뉴스 (market news feed)",
            "/data-download — 시세 다운로드 (historical price downloads)",
            "/settings — 운영 설정",
            "/feedback — 의견 보내기",
        ],
        "data_freshness": (
            "⚠ CRITICAL — STOCK DATA IS REAL-TIME AND CHANGES CONTINUOUSLY.\n"
            "   Korean market hours: 09:00-15:30 KST Mon-Fri. During market hours the\n"
            "   following all refresh every few seconds: KOSPI / KOSDAQ / KOSPI200 indices,\n"
            "   individual stock prices, intraday signals, investor flow tallies, top movers.\n\n"
            "   ➤ YOU HAVE LIVE-DATA TOOLS — USE THEM. Do NOT answer about current numbers\n"
            "     from the page snapshot alone (it is a moment stale). Instead CALL the\n"
            "     matching tool to fetch fresh data:\n"
            "       • picks / what to buy / open positions → stock_get_recommendations\n"
            "       • SHOULD I BUY/SELL X? / X 어때? / 전망 → CHAIN: stock_quote +\n"
            "         stock_get_investor_flow + stock_get_intraday_signals +\n"
            "         stock_get_recommendations + stock_get_news, then give a reasoned\n"
            "         BUY/HOLD/SELL view (price + 수급 + momentum + news + risk). NEVER\n"
            "         reply with only the price for an advice question.\n"
            "       • my holdings / P&L                    → stock_get_portfolio\n"
            "       • live signals right now               → stock_get_intraday_signals\n"
            "       • is monitoring on?                    → stock_get_intraday_status\n"
            "       • market / index overview              → stock_get_market_summary\n"
            "       • 외국인 net buy/sell, top movers       → stock_get_foreign_flow\n"
            "       • 수급 (foreign/inst/retail) by symbol → stock_get_investor_flow\n"
            "       • unusual volume                       → stock_get_volume_spikes\n"
            "       • watchlist / alerts / 뉴스             → stock_get_watchlist / _alerts / _news\n"
            "       • 대량보유 변동                          → stock_get_ownership_changes\n"
            "     Every tool result carries a `fetched_at` (KST) timestamp — ALWAYS quote it\n"
            "     ('as of 14:32 KST') so the user knows the freshness. If a tool returns\n"
            "     ok:false (backend asleep/unreachable), say so plainly and fall back to the\n"
            "     page snapshot with a clear '(as of last refresh)' caveat.\n"
            "   For after-hours / weekend questions you can answer confidently — markets are\n"
            "   closed and the latest values are the last close."
        ),
        "user_role": "individual or professional investor using OASIS for trade ideas + reviewing their own trade journal.",
    },
    "aiglass": {
        "name": "AIGlass Realty Agent",
        "tagline": "AI-powered property listings + customer lead intelligence + 360° virtual tours.",
        "pages": [
            "/chatbot — Assistant + Messages + Calls + Add knowledge",
            "/dashboard — broker KPIs",
            "/properties — property listings + AI photo analysis (defects, finishes, PII blur)",
            "/customers — customer leads + A/B/C/D scoring",
            "/contracts — contract management",
            "/aiglass — 360° virtual tour (coming soon)",
        ],
        "data_freshness": "Persisted via tRPC + Drizzle. Multi-tenant.",
        "user_role": "real-estate broker / agency owner.",
    },
}


def _agent_profile_block(agent_id: Optional[str]) -> str:
    """Render the agent's site map + capabilities into a prompt block."""
    if not agent_id:
        return ""
    p = AGENT_PROFILES.get(agent_id.lower())
    if not p:
        return ""
    pages = "\n".join(f"  - {pg}" for pg in p["pages"])
    return (
        "\n■■■ THIS APP — FULL SURFACE AREA ■■■\n"
        f"App: {p['name']}\n"
        f"Purpose: {p['tagline']}\n"
        f"The user is: {p['user_role']}\n\n"
        "ALL PAGES (use navigate(path) to take the user to any of them):\n"
        f"{pages}\n\n"
        f"DATA FRESHNESS:\n  {p['data_freshness']}\n"
        "\nYou know this entire surface area. Don't hedge with 'I'm not sure if "
        "that page exists' — it does. When the user asks about a section, point "
        "them at the right page or navigate them there directly.\n"
    )


# ============================================================================
#  System prompt builder
# ============================================================================

def _is_open_intent(transcript: Optional[str]) -> bool:
    """True only when the user EXPLICITLY wants to open/navigate (a command) or
    confirms an open offer. Questions are NOT open intents."""
    t = (transcript or "").strip().lower()
    if not t:
        return False
    open_cmds = ("open ", "go to", "take me", "navigate", "launch ", "bring up",
                 "열어", "열어줘", "이동", "가줘", "가자", "띄워", "페이지로",
                 "open it", "open the", "go there")
    if t.startswith("open") or any(c in t for c in open_cmds):
        return True
    confirms = ("yes", "yeah", "yep", "sure", "ok", "okay", "do it", "go ahead",
                "please do", "응", "네", "그래", "해줘", "좋아", "open it")
    if len(t.split()) <= 3 and any(t == c or t.startswith(c + " ") or t == c + "." for c in confirms):
        return True
    return False


def _cross_agent_route_hint(transcript: Optional[str], agent_id: Optional[str]) -> str:
    """Deterministic pre-router. When the VIP assistant gets a question that
    clearly belongs to another agent's domain (stock / asset), prepend a
    MANDATORY directive so the LLM routes via ask_agent(...) instead of
    web_search or guessing. Keeps VIP as the smart hub. Realty property
    auctions/prices are already handled by onbid_search / realprice_search."""
    if (agent_id or "vip").lower() != "vip":
        return ""
    t = (transcript or "").strip().lower()
    if not t:
        return ""

    # --- STOCK ---
    stock_kw = ("주가", "kospi", "kosdaq", "코스피", "코스닥", "증시", "종목", "시세",
                "현재가", "ticker", "watchlist", "관심종목", "주식", "stock", "shares",
                "순매수", "수급", "증권", "배당", "dividend", "etf", "나스닥", "nasdaq",
                "s&p", "dow")
    is_stock = any(k in t for k in stock_kw)
    if not is_stock:
        try:
            from services.stock_data_tools import _NAME_TO_TICKER
            is_stock = any(name in t for name in _NAME_TO_TICKER if len(name) >= 3)
        except Exception:
            pass
    if is_stock and _is_report_question(transcript):
        return ("■ [ROUTING] This asks about OUR reports / past analysis / knowledge "
                "base. ANSWER FROM THE KNOWLEDGE BASE excerpts provided in this "
                "prompt — synthesize the concrete report findings (companies, "
                "numbers, recommendations). Do NOT call ask_agent; do NOT delegate; "
                "do NOT say you cannot access internal reports.\n\n")
    if is_stock:
        return ("■ [ROUTING — MANDATORY] This is a STOCK question. You MUST call "
                "ask_agent(agent='stock', question=<the user's exact question>) to "
                "get the answer from the Stock agent (it has live quotes & market "
                "data). Do NOT use web_search. Do NOT guess. After it returns, "
                "state the answer and cite the Stock agent.\n\n")

    # --- ASSET ---
    asset_kw = ("portfolio", "자산", "임대수익", "rental income", "occupancy",
                "점유율", "임차인", "tenant", "asset value", "수익률", "yield",
                "보유 자산", "valuation", "임대료")
    if any(k in t for k in asset_kw):
        return ("■ [ROUTING — MANDATORY] This is an ASSET-management question. You "
                "MUST call ask_agent(agent='asset', question=<the user's exact "
                "question>) to get the answer from the Asset agent. Do NOT use "
                "web_search. Cite the Asset agent.\n\n")

    return ""


# Advice/opinion markers that mean the user wants ANALYSIS, not just a price.
_STOCK_ADVICE_KW = (
    "어때", "어떄", "사도", "살까", "팔까", "사야", "팔아야", "매수", "매도", "전망",
    "추천해", "추천 해", "괜찮", "들어가도", "진입", "보유해", "담아", "의견", "분석해",
    "사면", "팔면", "투자해", "사도돼", "사도 돼",
    # buy/sell phrasings the old list missed ('사는 게 좋아?', '살 만해?', '담을까' …)
    "사는 게 좋", "사는게 좋", "사는 것이 좋", "사는것이 좋", "사는 게 맞", "사는게 맞",
    "살 만", "살만", "사길", "사 둘", "사둘", "사 둬", "담을까", "담아도", "들어갈까",
    "들어갈 만", "좋을까", "매수 타이밍", "매도 타이밍", "팔 때", "팔아도", "사는 거",
    "should i buy", "should i sell", "should i hold", "should i get", "worth buying", "worth it",
    "good buy", "good to buy", "ok to buy", "good time to", "time to buy", "entry point",
    "buy or sell", "sell or hold", "hold or sell", "is it a good", "is it good to",
    "is now a good time", "invest in", "go long", "thoughts on", "a good buy", "worth a buy",
)


# Scalp / short-term intraday cues → the live Scalp Signal (M3).
_SCALP_KW = (
    "단타", "초단타", "스캘핑", "스켈핑", "지금 사서", "지금사서", "몇 분", "몇분",
    "분 안에", "분안에", "분 뒤", "분뒤", "짧게 먹", "짧게 치", "30분", "빠르게 먹",
    "scalp", "in 30 min", "in a few min", "quick trade", "quick 1", "right now and sell",
)


_WATCHLIST_KW = (
    "단타 종목", "단타종목", "단타 추천", "단타할 종목", "오늘 뭐 살", "오늘 뭐살",
    "뭐 살까", "뭐 사면", "뭐 사야", "무슨 종목", "어떤 종목", "종목 추천", "추천 종목",
    "종목 추천해", "오늘의 종목", "몇 주 살", "몇주 살",
    "watchlist", "what to scalp", "what to trade", "what should i buy", "what stock",
    "which stock", "what to buy", "today's picks", "today picks", "any picks",
    "how many stock", "how many share", "short time trade", "short-term trade",
    # natural phrasings ('short time trading… advise top 3 stock' fell to delegation)
    "short time trading", "short term trading", "short-term trading", "day trading",
    "top 3 stock", "top3 stock", "top three stock", "top stocks", "advise top",
    "recommend top", "best stocks", "stocks to buy", "추천해줘 종목", "종목 3개", "3종목",
)


# Pattern-based picks ask — catches natural phrasings the keyword list misses ('tell me 3
# stock which i can buy', 'give me five stocks to trade', '살 만한 종목 3개', 'analyze market
# and recommend stocks'). Keyword misses here fell to the generic ask_agent LLM (stale
# textbook answers, no methods, no crash veto — 2026-07-07 screenshot).
_PICKS_PATTERN_RE = _re.compile(
    r"(tell me|give me|recommend|advise|suggest|pick|찾아|알려|추천).{0,24}\b(\d+|three|five|two|몇)\s*(stocks?|종목|주식)"
    r"|\b(\d+|three|five)\s*(stocks?|종목|주식).{0,30}(buy|trade|살|매수|사)"
    r"|(stocks?|종목|주식).{0,16}(i can|to)\s*(buy|trade)"
    r"|analy[sz]e (the )?market.{0,30}(stock|buy|recommend|종목)"
    r"|(살|매수할|투자할)\s*만한\s*(종목|주식)"
    # 'is there any (korean) stock which i can buy…' / '살 주식 있어?' — scan asks, no single stock
    r"|is there any.{0,24}(stocks?|종목|주식)"
    r"|(살|사서|매수할?|딸|이길|벌).{0,20}(주식|종목).{0,8}(있어|있나|있을까|없어|없나)",
    _re.IGNORECASE)


def _is_watchlist_question(transcript: Optional[str]) -> bool:
    """A 'what should I day-trade today?' question (no single stock needed)."""
    if _is_movers_q(transcript):     # 'which stock increased most?' = a DATA question
        return False
    t = (transcript or "").lower()
    return any(k in t for k in _WATCHLIST_KW) or bool(_PICKS_PATTERN_RE.search(t))


# Phase C — the real-money readiness gate, askable in chat.
_READINESS_KW = (
    "실전 준비", "실전매매 준비", "실전 매매 준비", "준비됐어", "준비 됐어", "진짜 돈",
    "실거래 시작", "실전 시작해도", "믿어도 돼", "믿을 수 있어", "성적 어때", "성적 얼마",
    "승률 어때", "승률 얼마", "트랙레코드", "채점 결과",
    # 'how accurate are you' natural phrasings ('요즘 네 예측 잘 맞아?')
    "잘 맞아", "잘 맞춰", "잘 맞니", "잘 맞나", "예측 잘", "적중률", "적중 률",
    "얼마나 맞", "맞긴 해", "예측 성적", "예측 정확",
    "ready for real", "real money", "are we ready", "readiness", "track record",
    "can i trust you", "how accurate are you", "your win rate", "are you accurate",
    "accuracy rate", "prediction accuracy", "how good are your predictions",
    "how often are you right",
)


def _is_readiness_question(transcript: Optional[str]) -> bool:
    t = (transcript or "").lower()
    return any(k in t for k in _READINESS_KW)


# B3 — "what's moving RIGHT NOW" (live movers), distinct from the pick-based watchlist.
_MOVERS_KW = (
    "지금 움직이는", "지금 움직이", "움직이는 종목", "급등주", "급등 종목", "오늘 급등",
    "급락 종목", "특징주", "거래량 급증", "거래량 터진", "지금 뜨는", "달리는 종목",
    # natural phrasings ('뭐가 제일 많이 움직여?', '오늘 많이 오른 종목')
    "뭐가 움직", "많이 움직", "제일 움직", "가장 움직", "많이 오른", "제일 오른",
    "가장 오른", "많이 내린", "제일 내린", "많이 빠진",
    "movers", "what's moving", "whats moving", "what is moving", "volume spike",
    "unusual volume", "big movers", "hot stocks right now", "moving the most",
    "top gainers", "top losers", "biggest gainers", "biggest losers",
)


def _is_movers_question(transcript: Optional[str]) -> bool:
    t = (transcript or "").lower()
    return any(k in t for k in _MOVERS_KW)


# Intraday setup scanner — 'what should I trade NOW?' The boss's 1-hour scalp system:
# scan the watchlist → ACT_NOW / FORMING / NOTHING with entry/target-band/stop zones.
_SETUP_KW = (
    "지금 뭐 살까", "지금 뭐 사", "지금 살 종목", "지금 매수할", "지금 살만한", "지금 진입",
    "오늘 뭐 살까", "뭐 사면 좋을까", "지금 매매할", "단타 자리", "단타 종목", "지금 좋은 자리",
    "매수 자리 있", "지금 타이밍", "스캔", "셋업", "지금 뭐 사면",
    "what should i trade", "what to trade now", "what to buy now", "any setup",
    "today's setups", "todays setups", "scan setups", "trade setups", "good setup now",
    "what should i buy now", "any good trade", "show me setups", "intraday setup",
)


def _is_setup_question(transcript: Optional[str]) -> bool:
    if _is_movers_q(transcript):     # 'which stock increased most?' = a DATA question
        return False
    t = (transcript or "").lower()
    return any(k in t for k in _SETUP_KW)


# Dip-bounce hunter — '많이 떨어진 종목 중 반등할 거?' / 'buy the dip'. The boss's core
# day-trading pattern: buy a big 1h dip, sell the bounce ~1h later.
_DIP_BOUNCE_KW = (
    "반등할", "반등 할", "반등주", "반등 종목", "반등할만", "반등 후보", "낙폭과대", "낙폭 과대",
    "떨어진 종목", "빠진 종목", "급락 후 반등", "저점 매수 종목", "눌린 종목",
    "buy the dip", "dip buy", "dip bounce", "rebound candidate", "bounce candidate",
    "oversold bounce", "likely to rebound", "will rebound", "bounce back soon",
    "fallen stocks", "dipped stocks",
)


def _is_scalp_question(transcript: Optional[str]) -> bool:
    t = (transcript or "").lower()
    if not any(k in t for k in _SCALP_KW):
        return False
    try:
        from services.stock_resolver import find_all
        return bool(find_all(transcript or ""))
    except Exception:
        return False


def _stock_in_query(transcript: Optional[str]) -> Optional[str]:
    """Return a known stock ticker/name found in the text, else None. Uses the
    comprehensive resolver (all 51 tracked + slang like 하닉/삼전 + codes) first."""
    try:
        from services.stock_resolver import find_all, resolve_one
        hits = find_all(transcript or "")
        if hits:
            return hits[0][0]
        c, _n = resolve_one(transcript or "")     # fuzzy fallback for typos (삼썽전자→삼성전자)
        if c:
            return c
    except Exception:
        pass
    t = (transcript or "").lower()
    try:
        from services.stock_data_tools import _NAME_TO_TICKER
        for name in _NAME_TO_TICKER:
            if len(name) >= 2 and name in t:
                return name
    except Exception:
        pass
    m = _re.search(r"\b\d{6}\b", transcript or "")
    return m.group(0) if m else None


def _is_stock_advice(transcript: Optional[str], agent_id: Optional[str]) -> bool:
    """True when the user wants ADVICE on a SPECIFIC stock (not just its price).

    Requires BOTH an advice verb AND a resolvable stock name/code, so pure
    price questions ('X 현재가/얼마') and generic market chat never trigger it."""
    t = (transcript or "").strip().lower()
    if not t or not any(k in t for k in _STOCK_ADVICE_KW):
        return False
    return _stock_in_query(transcript) is not None


# Past-date markers + price words → a factual PAST-price lookup (never from memory).
_PAST_DATE_KW = (
    "어제", "전일", "전날", "엊그제", "그저께", "지난주", "지난 주", "지난달", "지난 달",
    "일 전", "일전", "주 전", "달 전", "개월 전", "전 종가", "전 주가",
    "yesterday", "last week", "last month", "days ago", "day ago", "weeks ago",
    "week ago", "month ago",
)
_PRICEY_KW = ("주가", "종가", "가격", "시세", "얼마", "price", "close", "closing", "cost", "거래량")

# An EXPLICIT calendar date — '2026년 6월 10일', '2026-06-10', '6월 10일', 'June 10',
# '10th June' — also marks a past-date price question (the relative _PAST_DATE_KW
# list above does NOT cover these). Compiled lazily (the `re` alias is imported
# later in this module).
_EXPLICIT_DATE_PATTERN = (
    r"(20\d{2}\s*[-./년]\s*\d{1,2}\s*[-./월]\s*\d{1,2})"
    r"|(\d{1,2}\s*월\s*\d{1,2}\s*일)"
    r"|((?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2})"
    r"|(\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*)"
)
_EXPLICIT_DATE_RE = None


def _has_explicit_date(transcript: Optional[str]) -> bool:
    global _EXPLICIT_DATE_RE
    if _EXPLICIT_DATE_RE is None:
        import re as _re2
        _EXPLICIT_DATE_RE = _re2.compile(_EXPLICIT_DATE_PATTERN, _re2.IGNORECASE)
    return bool(_EXPLICIT_DATE_RE.search(transcript or ""))


# ── Smart date understanding (shared logic, kept identical on the Stock side) ──
_WDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4,
          "saturday": 5, "sunday": 6, "월요일": 0, "화요일": 1, "수요일": 2,
          "목요일": 3, "금요일": 4, "토요일": 5, "일요일": 6,
          "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_MONTHS = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
           "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
           "december": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
           "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12}
_MONTH_STOP = {"maybe", "mayor", "mayer", "marche", "augment", "octopus"}
_ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
        "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11,
        "twelfth": 12, "thirteenth": 13, "fourteenth": 14, "fifteenth": 15,
        "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19,
        "twentieth": 20, "twenty-first": 21, "twenty-second": 22, "twenty-third": 23,
        "twenty-fourth": 24, "twenty-fifth": 25, "twenty-sixth": 26,
        "twenty-seventh": 27, "twenty-eighth": 28, "twenty-ninth": 29,
        "thirtieth": 30, "thirty-first": 31}
_CARD = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
         "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
         "twelve": 12}


def _fuzzy_num(w: str, table: dict, cutoff: float):
    if w in table:
        return table[w]
    import difflib
    hit = difflib.get_close_matches(w, list(table.keys()), n=1, cutoff=cutoff)
    return table[hit[0]] if hit else None


def _relative_date_iso(text: Optional[str]) -> Optional[str]:
    """Resolve almost any date phrase to 'YYYY-MM-DD' (KST). Understands:
      • 'N days/weeks/months ago|past|before|earlier' (digit OR word — 'one week ago',
        '7 days past', '2주 전')
      • weekday names ('this/last week monday', 'on monday', '지난주 화요일')
      • month + day, typo-tolerant, optional year ('12th of June', 'twelfth of June',
        'tweleve th of Juni', 'June 12', '15th may 2026')
      • numeric ('2026-06-12', '2026년 6월 12일') and '어제/그제/지난주/지난달'.
    Returns None when there's no date (e.g. 'current price', 'now')."""
    from datetime import date as _date, timedelta as _td
    t = (text or "").lower().strip()
    if not t:
        return None
    today = _dt_now_kst().date()
    # 1) N (days/weeks/months) ago/past — digit or word number
    m = (_re.search(r"\b(\d{1,3}|[a-z]+)\s+(day|days|week|weeks|month|months|일|주|개월)\s*"
                    r"(ago|past|before|earlier|prior|back|전)\b", t)
         or _re.search(r"\b(\d{1,3})\s*(일|주|개월)\s*전", t))
    if m:
        nraw, unit = m.group(1), m.group(2)
        n = int(nraw) if nraw.isdigit() else _fuzzy_num(nraw, _CARD, 0.8)
        if n:
            if unit.startswith("w") or unit == "주":
                return (today - _td(weeks=n)).isoformat()
            if unit.startswith("m") or unit == "개월":
                return (today - _td(days=30 * n)).isoformat()
            return (today - _td(days=n)).isoformat()
    # 2) weekday (+ this/last week)
    for name in sorted(_WDAYS, key=len, reverse=True):
        if _re.search(rf"(?<![a-z]){_re.escape(name)}(?![a-z])", t):
            wd = _WDAYS[name]
            d = (today - _td(days=today.weekday())) + _td(days=wd)
            before = t.split(name)[0]
            if ("last week" in t or "지난주" in t or "지난 주" in t
                    or "last " in before[-7:] or "지난" in before[-4:]):
                d -= _td(days=7)
            elif d > today:
                d -= _td(days=7)
            return d.isoformat()
    # 3) month name (typo-tolerant) + day (digit / ordinal word / cardinal word)
    month = None
    for tok in _re.finditer(r"[a-z]{3,}", t):
        w = tok.group()
        if w in _MONTH_STOP:
            continue
        mm = _fuzzy_num(w, _MONTHS, 0.75)
        if mm:
            month = mm
            break
    if month:
        day = None
        dm = _re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\b", t)
        if dm and 1 <= int(dm.group(1)) <= 31:
            day = int(dm.group(1))
        if day is None:
            for w in _re.findall(r"[a-z\-]{3,}", t):
                day = _fuzzy_num(w, _ORD, 0.82)
                if day:
                    break
        if day is None:
            for w in _re.findall(r"[a-z]{2,}", t):
                c = _fuzzy_num(w, _CARD, 0.82)
                if c:
                    day = c
                    break
        if day:
            ym = _re.search(r"\b(20\d{2})\b", t)
            yr = int(ym.group(1)) if ym else today.year
            try:
                d = _date(yr, month, day)
                if not ym and d > today:
                    d = _date(yr - 1, month, day)
                return d.isoformat()
            except ValueError:
                pass
    # 4) numeric explicit
    m = _re.search(r"(20\d{2})[.\-/년\s]+(\d{1,2})[.\-/월\s]+(\d{1,2})", t)
    if m:
        try:
            return _date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            pass
    # 5) single relative keywords
    if "그제" in t or "그저께" in t or "엊그제" in t:
        return (today - _td(days=2)).isoformat()
    if "어제" in t or "yesterday" in t or "전날" in t:
        return (today - _td(days=1)).isoformat()
    if "지난주" in t or "last week" in t:
        return (today - _td(days=7)).isoformat()
    if "지난달" in t or "지난 달" in t or "last month" in t:
        return (today - _td(days=30)).isoformat()
    return None


def _names_weekday(text: Optional[str]) -> bool:
    """True if the text names a SPECIFIC weekday (Mon–Sun / 월–일, incl. 3-letter
    abbreviations) as a standalone word. Used to treat 'last week Friday' as a
    single day rather than a whole-week range. Uses the same word-boundary match
    as `_relative_date_iso` so the two stay consistent (e.g. 'fri' inside 'friday'
    or 'mon' inside 'monitor' does NOT count)."""
    t = (text or "").lower()
    if not t:
        return False
    for name in _WDAYS:
        if _re.search(rf"(?<![a-z]){_re.escape(name)}(?![a-z])", t):
            return True
    return False


def _inject_relative_date(transcript: Optional[str]) -> Optional[str]:
    """For a stock question with a date phrase (relative OR worded, no explicit ISO
    date yet), append the resolved 'YYYY-MM-DD' so past-price routing + history lookup
    work for any phrasing ('12th of June', 'one week ago', 'this week monday')."""
    if not transcript:
        return transcript
    if _re.search(r"20\d{2}[.\-/]\s*\d{1,2}[.\-/]\s*\d{1,2}", transcript):
        return transcript  # already has an explicit ISO date
    if not _is_stock_question(transcript):
        return transcript
    iso = _relative_date_iso(transcript)
    return f"{transcript} ({iso})" if iso else transcript


def _is_past_price(transcript: Optional[str]) -> bool:
    """True when the user asks for a PAST-date price/volume of a specific stock —
    e.g. '삼성전자 어제 종가', 'X 10일 전 주가', '2026년 6월 10일 SK하이닉스 종가',
    'this week monday SK Hynix price'. These MUST be answered from real daily history,
    never the LLM's memory."""
    t = (transcript or "").strip().lower()
    if not t:
        return False
    has_past = any(k in t for k in _PAST_DATE_KW) or _has_explicit_date(transcript)
    if not has_past or not any(k in t for k in _PRICEY_KW):
        return False
    return _stock_in_query(transcript) is not None


# A question ABOUT our reports / knowledge base / past analysis — answer from VIP's
# own RAG (the report KB lives here), NOT by delegating to the Stock backend (which
# has only live data, no reports).
_REPORT_KW = (
    "리포트", "레포트", "보고서", "report", "우리 분석", "우리 리포트", "우리가 분석",
    "our report", "our analysis", "최근 분석에서", "분석에서", "분석했", "뭐라고 했",
    "어떻게 봤", "어떻게 분석", "추천 종목", "추천한 종목", "추천했", "코멘트",
    "knowledge base", "데이터 사전", "지식베이스",
)


def _is_report_question(transcript: Optional[str]) -> bool:
    """True when the user asks about OUR reports / past analysis / knowledge base —
    these are answered from VIP's RAG, not delegated to the live-only Stock agent."""
    t = (transcript or "").strip().lower()
    return bool(t) and any(k in t for k in _REPORT_KW)


# 'What is X / explain X' CONCEPT questions with NO specific stock — answer directly
# from VIP's own RAG+LLM (fast, the data-dictionary seed has these) instead of the
# slow round-trip to the Stock backend (which runs a full analysis).
_CONCEPT_DEF_KW = (
    "뭐야", "뭔가요", "뭡니까", "뭔데", "무엇", "뜻", "의미", "개념", "설명",
    "차이", "what is", "what's", "what are", "explain", "define", "meaning",
    "difference between",
)


def _is_concept_question(transcript: Optional[str]) -> bool:
    t = (transcript or "").strip().lower()
    if not t or not any(k in t for k in _CONCEPT_DEF_KW):
        return False
    # 'what is the price of apple' is a US-stock DATA question, not a concept — let it
    # delegate to the Stock backend (real price) instead of an LLM general answer.
    if _is_us_stock_query(transcript) or any(w in t for w in _PRICE_WORDS):
        return False
    return _stock_in_query(transcript) is None


# Bare CURRENT-PRICE question (현재가/시세/주가/얼마/price) — VIP answers it LOCALLY
# (Kiwoom during market, Naver after). Handles ONE stock, MULTIPLE stocks, and a
# bare 'what is the current stock price' (→ default watchlist).
_PRICE_WORDS = ("현재가", "시세", "주가", "얼마", "가격", "price", "quote",
                "시가", "고가", "저가", "opening", "open price", "high price", "low price",
                "거래량", "거래대금", "volume",
                # English current-price phrasings (so 'what's X trading at' routes LOCAL,
                # matching the Korean path — same detailed table both languages).
                "trading at", "trade at", "how much", "worth", "going for", "trading for")
# Generic stock-price phrasing with NO specific company → show the watchlist.
_GENERIC_STOCK_WORDS = ("stock", "주가", "주식", "종목", "시세", "현재가")
# Bare English quote-field words. Korean says '고가/저가/종가' — single tokens that are
# already in _PRICE_WORDS — but English says "today's HIGH", "what was the CLOSE", with no
# 'price' anywhere. Before 2026-08-03 those missed the local quote lane and fell through to
# the LLM tool-chain, which answered from the DAILY bar: "오늘 고가는?" returned the live
# Kiwoom high while "what about today's high?" returned a different number from Naver daily.
# Kept deliberately narrow (no bare 'open' — "open the dashboard" must not look like a
# quote) and only honoured when a specific stock is on the table.
_BARE_FIELD_RE = _re.compile(r"\b(high|low|close|closing|highest|lowest)\b", _re.IGNORECASE)
# Pronoun references that point at the stock under discussion rather than naming it.
_PRONOUN_REF_RE = _re.compile(
    r"\b(it|that one|this one|that stock|this stock|the stock|them|those)\b"
    r"|그거|그것|이거|이것|저거|그 ?종목|이 ?종목|해당 ?종목|얘|걔",
    _re.IGNORECASE)
_DEFAULT_WATCHLIST = (("000660", "SK하이닉스"), ("005930", "삼성전자"), ("035420", "NAVER"))


def _kr_market_open_now() -> bool:
    """KRX regular session: Mon-Fri 09:00-15:30 KST."""
    now = _dt_now_kst()
    if now.weekday() >= 5:
        return False
    cur = now.hour * 100 + now.minute
    return 900 <= cur <= 1530


def _dt_now_kst():
    from datetime import datetime as _d, timezone as _z, timedelta as _t
    return _d.now(_z(_t(hours=9)))


# Generic words that must NOT identify a company on their own — they appear inside
# many company names ('Samsung Electronics', 'LG Electronics', 'POSCO Holdings'), so
# fuzzy-matching them adds the WRONG stock (e.g. 'electronics' from 'samsung
# electronics' wrongly resolving to LG전자).
_STOCK_FUZZY_STOP = {
    "electronics", "electronic", "electric", "elec", "stock", "stocks", "share",
    "shares", "price", "prices", "quote", "current", "today", "value", "corp",
    "corporation", "inc", "incorporated", "group", "holdings", "holding", "company",
    "co", "ltd", "limited", "industries", "industry", "tech", "technology",
    "technologies", "motors", "motor", "chemical", "chem", "energy", "solution",
    "solutions", "전자", "전기", "주식", "주가", "현재가", "시세", "가격", "그룹",
    "지주", "화학", "에너지", "현재",
}


# Curated English / short-form aliases for common stocks the resolver's Korean-name
# scan + fuzzy (≥4 chars) would otherwise miss — e.g. bare 'lg', 'sk', or English
# 'samsung electronics'. Longest alias wins (so 'lg electronics'/'lg화학' beat bare
# 'lg' → LG전자 default). Multi-word aliases are consumed whole so leftover words
# ('electronics') can't fuzzy-match the wrong company.
_STOCK_ALIASES = {
    "lg electronics": ("066570", "LG전자"), "엘지전자": ("066570", "LG전자"),
    "lg전자": ("066570", "LG전자"), "엘지": ("066570", "LG전자"), "lg": ("066570", "LG전자"),
    "lg화학": ("051910", "LG화학"), "lg chem": ("051910", "LG화학"),
    "lg에너지솔루션": ("373220", "LG에너지솔루션"), "lg energy": ("373220", "LG에너지솔루션"),
    "samsung electronics": ("005930", "삼성전자"), "samsung": ("005930", "삼성전자"),
    "삼성": ("005930", "삼성전자"),
    "samsung sdi": ("006400", "삼성SDI"), "samsung biologics": ("207940", "삼성바이오로직스"),
    "sk hynix": ("000660", "SK하이닉스"), "skhynix": ("000660", "SK하이닉스"),
    "hynix": ("000660", "SK하이닉스"), "하이닉스": ("000660", "SK하이닉스"),
    "sk telecom": ("017670", "SK텔레콤"),
    "samsung electro-mechanics": ("009150", "삼성전기"), "samsung electro mechanics": ("009150", "삼성전기"),
    "samsung electromechanics": ("009150", "삼성전기"), "삼성전기": ("009150", "삼성전기"),
    "sk square": ("402340", "SK스퀘어"), "sksquare": ("402340", "SK스퀘어"), "sk스퀘어": ("402340", "SK스퀘어"),
    "naver": ("035420", "NAVER"), "네이버": ("035420", "NAVER"),
    "kakao": ("035720", "카카오"), "카카오": ("035720", "카카오"),
    "hyundai motor": ("005380", "현대차"), "hyundai": ("005380", "현대차"),
    "현대차": ("005380", "현대차"), "kia": ("000270", "기아"), "기아": ("000270", "기아"),
    "posco": ("005490", "POSCO홀딩스"), "포스코": ("005490", "POSCO홀딩스"),
    "celltrion": ("068270", "셀트리온"), "셀트리온": ("068270", "셀트리온"),
}


def _all_stocks_in_query(transcript: Optional[str]) -> list[tuple[str, str]]:
    """All KR stocks (6-digit code, display name) named in the text, in order,
    deduped. SCANS for every known stock name (so space-separated 'SK하이닉스 네이버'
    all resolve, not just comma/and), applies curated English/short aliases ('lg',
    'samsung electronics'), matches 6-digit codes, and fuzzy-matches leftover words
    to catch typos like 'Skhynoix' → SK하이닉스."""
    import re as _re
    t = transcript or ""
    low = t.lower()
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    _resolve_one = None
    # Comprehensive resolver first (all 51 names + slang 하닉/삼전 + codes). SEED with its
    # hits but DO NOT return early — a correctly-spelled stock must not hide a MISSPELLED
    # one in the same query (bug 2026-07-22: 'skyhix and samsung' returned only samsung).
    # The alias/code/fuzzy passes below still run on the leftover so typo'd names resolve.
    try:
        from services.stock_resolver import find_all as _find_all, resolve_one as _resolve_one
        for _hc, _hn in (_find_all(t) or []):
            _hc = str(_hc)
            if _hc.isdigit() and _hc not in seen:
                seen.add(_hc)
                out.append((_hc, _hn))
                low = low.replace(_hn.lower(), " ")   # consume so it isn't re-scanned
    except Exception:
        pass
    try:
        from services.stock_data_tools import _NAME_TO_TICKER
        names = sorted(_NAME_TO_TICKER, key=len, reverse=True)
    except Exception:
        _NAME_TO_TICKER, names = {}, []

    consumed = low
    for name in names:
        nl = name.lower()
        if len(nl) >= 2 and nl in consumed:
            code = str(_NAME_TO_TICKER[name])
            if code.isdigit() and code not in seen:
                seen.add(code)
                out.append((code, name))
            consumed = consumed.replace(nl, " ")  # so 'SK' inside 'SK Hynix' won't re-match
    # Curated aliases (whole-word), longest first so multi-word aliases consume fully.
    for alias, (code, dname) in sorted(_STOCK_ALIASES.items(), key=lambda kv: -len(kv[0])):
        pat = rf"(?<![a-z0-9가-힣]){_re.escape(alias)}(?![a-z0-9가-힣])"
        if _re.search(pat, consumed):
            if code not in seen:
                seen.add(code)
                out.append((code, dname))
            consumed = _re.sub(pat, " ", consumed)
    for m in _re.findall(r"\b(\d{6})\b", t):
        if m not in seen:
            seen.add(m)
            out.append((m, m))
    # Fuzzy fallback for typos on leftover words (e.g. 'skhynoix', '삼송전자').
    if names:
        import difflib
        norm = {_re.sub(r"\s+", "", n.lower()): n for n in names}  # spaceless name -> name
        # resolver aliases (incl. ENGLISH official names) join the fuzzy space —
        # _NAME_TO_TICKER alone lacks 'hanwha ocean', so 'Hana ocean' had no target
        # to fuzzy-match and the third stock silently vanished (boss 2026-08-25)
        _extra_code: dict[str, str] = {}
        try:
            from services.stock_resolver import _ALIAS as _RAL, _build as _rb
            _rb()
            for _al, _cd2 in _RAL.items():
                _k2 = _re.sub(r"\s+", "", _al.lower())
                if len(_k2) >= 4 and _k2 not in norm:
                    _extra_code[_k2] = str(_cd2)
        except Exception:
            pass
        keys = list(norm.keys()) + list(_extra_code.keys())

        def _fuzzy_code(hit_key: str):
            if hit_key in norm:
                _nm3 = norm[hit_key]
                return str(_NAME_TO_TICKER[_nm3]), _nm3
            _cd3 = _extra_code.get(hit_key)
            try:
                from services.stock_resolver import display_name as _dn3
                return _cd3, (_dn3(_cd3) if _cd3 else hit_key)
            except Exception:
                return _cd3, hit_key
        # prefer the longest Hangul name per code so a fuzzy hit on an english alias
        # displays cleanly ('skyhix' -> 'SK하이닉스', not the lowercase 'sk hynix').
        canon: dict[str, str] = {}
        for _nm, _cd in _NAME_TO_TICKER.items():
            _cd = str(_cd)
            if _re.search(r"[가-힣]", _nm) and len(_nm) > len(canon.get(_cd, "")):
                canon[_cd] = _nm
        for w in _re.split(r"[\s,/&]+", consumed):
            w = w.strip()
            if len(w) < 4 or w in _STOCK_FUZZY_STOP:
                continue
            # cutoff 0.72: real stock typos ('skyhix'0.77, 'samsng'0.92, 'naiver'0.91)
            # all score >=0.77; non-stock words ('current','stock','price','need') top
            # out at 0.44 — the gap is wide, so 0.72 catches typos without false hits.
            hit = difflib.get_close_matches(w, keys, n=1, cutoff=0.72)
            if hit:
                code, name = _fuzzy_code(hit[0])
                if code and code.isdigit() and code not in seen:
                    seen.add(code)
                    out.append((code, canon.get(code, name)))
        # BIGRAM fuzzy for typo'd TWO-WORD English names ('Hana ocean' → Hanwha Ocean —
        # boss 2026-08-25: the third stock silently vanished from a 3-stock question).
        _wtoks = [w for w in _re.split(r"[\s,/&]+", consumed) if len(w) >= 3
                  and w not in _STOCK_FUZZY_STOP]
        for _bi in range(len(_wtoks) - 1):
            pair = _wtoks[_bi] + _wtoks[_bi + 1]
            if len(pair) < 8:
                continue
            hit2 = difflib.get_close_matches(pair, keys, n=1, cutoff=0.78)
            if hit2:
                code, name = _fuzzy_code(hit2[0])
                if code and code.isdigit() and code not in seen:
                    seen.add(code)
                    out.append((code, canon.get(code, name)))
    # last resort: if NOTHING resolved at all, one fuzzy single-stock guess (typos)
    if not out and _resolve_one:
        try:
            _rc, _rn = _resolve_one(t)
            if _rc:
                out.append((str(_rc), _rn))
        except Exception:
            pass
    return out


def _requested_price_fields(transcript: Optional[str]) -> list[str]:
    """Which quote values the user asked for, in display order. 'opening and current
    price' -> ['open','price']; 'high and low' -> ['high','low']; bare price -> ['price'].
    Lets the answer cover MULTIPLE fields, not just current."""
    t = (transcript or "").lower()
    fields: list[str] = []
    if "시가" in t or "open price" in t or "opening" in t or _re.search(r"\bopen\b", t):
        fields.append("open")
    if ("고가" in t or "highest" in t or "high price" in t or "day high" in t
            or "intraday high" in t or _re.search(r"\bhigh\b", t)):
        fields.append("high")
    if ("저가" in t or "lowest" in t or "low price" in t or "day low" in t
            or "intraday low" in t or _re.search(r"\blow\b", t)):
        fields.append("low")
    cur = (any(k in t for k in ("current", "현재", "지금", "real-time", "실시간", "latest", "now"))
           or any(k in t for k in ("price", "주가", "시세", "얼마", "가격", "quote")))
    if cur:
        fields.append("price")
    if "거래량" in t or "volume" in t or "거래대금" in t:
        fields.append("volume")
    # Only default to price when the user asked for NOTHING specific — a pure
    # '거래량' (volume-only) question must NOT get the price tacked on.
    if not fields:
        fields.append("price")
    return fields


def _is_price_field_followup(transcript: Optional[str]) -> bool:
    """A pure quote-field request with NO stock named — e.g. '시가, 고가, 저가,
    거래량' as a follow-up to a prior stock turn. Must borrow the stock from history."""
    t = (transcript or "").lower()
    if not any(w in t for w in _PRICE_WORDS) and not _BARE_FIELD_RE.search(t):
        return False
    if _all_stocks_in_query(transcript):
        return False  # already has its own stock
    if _is_past_price(transcript) or _requested_history_dates(transcript):
        return False  # a dated/history question, handled elsewhere
    return True


def _recent_stock_name(history: Optional[list[dict]]) -> Optional[str]:
    """Most recent stock NAME mentioned in the conversation, for resolving a
    field follow-up ('시가, 거래량 알려줘') that omits the stock."""
    for h in reversed(history or []):
        body = h.get("content") or h.get("text") or h.get("transcript") or ""
        st = _all_stocks_in_query(body)
        if st:
            return st[0][1]
    return None


_HISTORY_RANGE_RE = _re.compile(
    r"(?:last|past|recent|over\s+the\s+(?:last|past))\s+(?:\d+\s+)?(?:few\s+)?"
    r"(?:day|days|week|weeks|month|months)\b"
    r"|(?:지난|최근)\s*\d*\s*(?:일|주|개월|달)\s*(?:간|동안|치)?"
    r"|\d+\s*(?:일|days?)\s*(?:간|동안|치)"
    r"|최근\s*(?:며칠|몇\s*일|몇\s*주)"
    r"|(?:trend|history|movement|추이|흐름|동향|며칠)",
    _re.IGNORECASE)


def _is_history_range_query(transcript: Optional[str]) -> bool:
    """A RANGE / over-time question — 'last 4 days', 'past 3 weeks', '최근 5일',
    'price trend', '4일간'. These want a multi-day history, so they go to the
    history/LLM path (which reasons over the range), not the single current-price
    shortcut. NOT triggered by 'last price' (no time unit) or a single date."""
    return bool(transcript) and bool(_HISTORY_RANGE_RE.search(transcript))


# FUTURE-outlook markers. A question like '앞으로 5일 전망' / 'outlook for next week' is
# about the FUTURE — it must go to the two-method FORECAST path, NOT be parsed as a past
# history range (the '5일' was wrongly read as a past window → stale single-row table).
_FUTURE_OUTLOOK_KW = (
    "앞으로", "향후", "전망", "다음 주", "다음주", "내일", "모레", "이번 주 남은",
    "오를까", "내릴까", "오를", "내릴", "상승할", "하락할", "예상돼", "예상되",
    # soft future phrasings ('어떻게 될까/어떨까/N일 후') — these mean 'how WILL it do',
    # so they must route to the two-method outlook like the English 'outlook', not to
    # stock-delegation/history (the EN/KO inconsistency the user hit).
    "어떻게 될까", "어떻게 될지", "어떨까", "어찌될까", "어찌 될까", "될까요", "될지",
    "어떻게 될", "될 것 같", "될것 같", "될거 같", "될 거 같",   # '오늘 오후 어떻게 될 것 같아?'
    "일 후", "일후", "이후 전망", "전망 어때", "전망은", "오를지", "내릴지", "갈까",
    "forecast", "outlook", "next week", "coming days", "going to", "will it",
    "expect", "predict", "future",
    # soft EN future phrasings ('what will X do', 'over the next N days')
    "what will", "over the next", "in the next", "next few", "days ahead",
    "will rise", "will fall", "will go", "near future", "do next",
)


def _is_future_outlook(transcript: Optional[str]) -> bool:
    t = (transcript or "").lower()
    return any(k in t for k in _FUTURE_OUTLOOK_KW)


# A day-trade / stop follow-up ('손절은?', 'where's the stop', 'target?', 'buy zone?') —
# usually asked WITHOUT re-naming the stock, so we borrow it from the recent context and
# answer with the real day_trade stop/levels instead of an LLM guess.
_DAYTRADE_FOLLOWUP_KW = (
    "손절", "손절가", "익절", "목표가", "매수가", "매도가",
    "stop", "stop-loss", "stop loss", "stoploss", "target", "buy zone", "sell zone",
    "buy price", "sell price", "entry",
)


def _is_daytrade_followup(transcript: Optional[str]) -> bool:
    t = (transcript or "").lower()
    return any(k in t for k in _DAYTRADE_FOLLOWUP_KW)


# Market-WIDE investor flow question ('who's buying KOSPI today', '오늘 외국인 순매수?') —
# answered from the real market_flows tool, identically in EN + KO (don't let it slip into
# stock-delegation or an LLM guess). Per-stock flow ('삼성전자 외국인 순매수') is excluded.
_MFLOW_KW = ("순매수", "순매도", "수급", "투자자별", "net buy", "net sell", "net buying",
             "net selling", "buying the market", "who is buying", "who's buying")
_MFLOW_MKT_KW = ("시장", "코스피", "코스닥", "market", "kospi", "kosdaq")
_MFLOW_INV_KW = ("외국인", "기관", "개인", "foreign", "institution", "individual", "연기금", "금융투자")


def _is_market_flow_q(transcript: Optional[str]) -> bool:
    t = (transcript or "").lower()
    if not any(k in t for k in _MFLOW_KW):
        return False
    if any(k in t for k in _MFLOW_MKT_KW):
        return True
    # investor-type word with NO specific stock named → market-wide
    return any(k in t for k in _MFLOW_INV_KW) and _stock_in_query(transcript) is None


# A live market overview is not a request for a canned morning brief. Its
# factual claims expire quickly, so it needs evidence before interpretation.
# Keep this narrow: evergreen questions such as "what is KOSPI?" stay fast.
_FRESH_MARKET_TIME_KW = (
    "오늘", "지금", "현재", "실시간", "장 시작", "장초반", "개장", "장중",
    "today", "now", "current", "live", "market open", "opening", "intraday",
)
_MARKET_OVERVIEW_KW = (
    "코스피", "코스닥", "증시", "주식시장", "시장 흐름", "시장 상황", "장 분위기",
    "kospi", "kosdaq", "stock market", "market flow", "market overview", "market mood",
)


def _requires_fresh_market_evidence(transcript: Optional[str]) -> bool:
    """Return whether a market overview needs live evidence before answering.

    This is an evidence policy, not a presentation policy. It deliberately does
    not prescribe sections, headings, or answer length.
    """
    text = (transcript or "").lower()
    return (
        any(keyword in text for keyword in _FRESH_MARKET_TIME_KW)
        and any(keyword in text for keyword in _MARKET_OVERVIEW_KW)
    )


# A BARE stock-switch follow-up ('how about NAVER?', '그럼 네이버는?', 'NAVER는?') — names a
# new stock but carries NO intent of its own, so it should INHERIT the previous turn's
# intent (price→price, outlook→outlook), not start a fresh long analysis.
_SWITCH_PREFIX_RE = _re.compile(
    r"^\s*(how about|what about|and how about|and what about|and also|and then|and|also"
    r"|그럼|그러면|그리고|그 다음|다음으로|then|how about you|또한|또는|또)\b", _re.I)
_BARE_STOCK_RE = _re.compile(r"^\s*\S{1,20}\s*(는|은|도)\s*[?？]?\s*$")   # 'X는?' / 'X은?' / 'X도?'
# English bare follow-up naming just a stock — 'SK Hynix?' / 'and Samsung?' — mirrors the
# Korean 'X는?' form so an EN switch follow-up hits the deterministic price/forecast lane
# instead of falling to the LLM (boss 2026-07-29: 'And SK Hynix?' hallucinated a wrong price
# while the KO '그럼 SK하이닉스는?' returned the correct live quote). The downstream lane still
# requires the phrase to resolve to a real stock, so non-stock 'really?' safely falls through.
_BARE_STOCK_EN_RE = _re.compile(r"^\s*(and|also|and also)?\s*[A-Za-z][A-Za-z0-9.\- ]{0,24}\?\s*$", _re.I)


def _prev_user_msg(history: Optional[list[dict]]) -> str:
    for h in reversed(history or []):
        if (h.get("role") or "") == "user":
            return h.get("content") or h.get("text") or h.get("transcript") or ""
    return ""


def _strip_stock_names(text: str) -> str:
    """Blank every known stock alias out of the text — for a switch follow-up that
    re-runs the previous question with the stocks swapped ('how about S-Oil?' after
    'last 6 days Skhynix ... table' must not keep SK하이닉스 in the rebuilt question)."""
    try:
        from services import stock_resolver as _sr
        out = " " + (text or "") + " "
        low = out.lower()
        for alias in _sr._aliases_longest_first():
            if len(alias) < 2:
                continue
            i = low.find(alias)
            while i != -1:
                j = i + len(alias)
                # short ascii aliases (gs/kt) need word edges — don't eat 'gs' from 'things'
                if alias.isascii() and (low[i - 1: i].isalnum() or low[j: j + 1].isalnum()):
                    i = low.find(alias, i + 1)
                    continue
                out = out[:i] + " " * (j - i) + out[j:]
                low = low[:i] + " " * (j - i) + low[j:]
                i = low.find(alias, j)
        # typo'd names ("Samsun") aren't exact aliases — drop any leftover token that
        # still fuzzy-resolves to a stock, so the rebuilt question names ONLY the new one
        kept = []
        for w in _re.split(r"\s+", out.strip()):
            if len(w) >= 5 and w.isascii() and _sr.resolve_one(w)[0]:
                continue
            kept.append(w)
        return " ".join(kept).strip()
    except Exception:
        return text or ""


# An explicit BUY/SELL/HOLD DECISION or advice ask ('사야 할까/팔까/hold or sell/advise').
_DECISION_KW = (
    "사야", "팔까", "팔아야", "사도 될", "매수해", "매도해", "보유할까", "보유 vs",
    "종합 판단", "종합판단", "종합적으로", "조언", "추천해", "어떻게 할까", "어찌할까",
    "buy or sell", "hold or sell", "sell or hold", "should i buy", "should i sell",
    "should i hold", "is it a buy", "buy hold sell", "buy/sell", "your advice",
    "your advise", "what should i do", "recommend",
    # 'can/may I buy X (today)?' phrasings — same decision intent as 'should I buy'
    "can i buy", "can we buy", "may i buy", "could i buy", "is it okay to buy",
    "is it ok to buy", "good idea to buy", "possible to buy",
)


def _is_decision_q(transcript: Optional[str]) -> bool:
    return any(k in (transcript or "").lower() for k in _DECISION_KW)


# A SELL-TIMING ask ('언제 팔아야 해?/when to sell?') → run decide() with focus='sell' so the
# answer leads with EXIT levels (익절/손절), not the buy framing. Distinct from a fresh
# buy/sell decision: here the user already holds (or plans to) and wants the exit plan.
_SELL_TIMING_KW = (
    "언제 팔", "언제 매도", "언제 파는", "언제쯤 팔", "팔 타이밍", "매도 타이밍", "매도 시점",
    "매도시점", "익절 언제", "언제 익절", "팔 때", "언제 나와", "언제 나올", "언제 정리",
    "when to sell", "when should i sell", "when do i sell", "when to exit", "exit timing",
    "when to take profit", "sell target", "target to sell",
)


def _is_sell_timing_q(transcript: Optional[str]) -> bool:
    return any(k in (transcript or "").lower() for k in _SELL_TIMING_KW)


def _pending_clarify_decision(history: Optional[list[dict]]) -> bool:
    """True if the most recent assistant turn was the 'which stock? — then I'll run the
    buy/sell analysis' clarify prompt (boss 2026-07-20: a bare stock-name reply to it was
    falling through to a truncating LLM path instead of the deterministic decision)."""
    for h in reversed(history or []):
        role = str(h.get("role") or h.get("who") or "").lower()
        if role in ("assistant", "chatbot", "bot"):
            c = str(h.get("content") or h.get("text") or "")
            return ("buy/sell analysis with sizing" in c or "매수/매도 판단과 수량" in c)
        if role in ("user", "human"):
            continue
    return False


def _recent_sell_intent(history: Optional[list[dict]]) -> bool:
    """Recent user turn expressed a SELL/exit intent (so a follow-up decide uses focus=sell)."""
    _kw = ("sell", "팔", "매도", "익절", "손절", "exit", "정리")
    n = 0
    for h in reversed(history or []):
        if str(h.get("role") or h.get("who") or "").lower() in ("user", "human"):
            c = str(h.get("content") or h.get("text") or "").lower()
            if any(k in c for k in _kw):
                return True
            n += 1
            if n >= 4:
                break
    return False


# A RECOMMENDATION ('should I buy?/살까/팔까') asks for a buy/sell/hold ACTION → the friend-
# style 'decide' report. A pure OUTLOOK ('전망/향후/outlook/어때') asks WHERE it's headed → the
# detailed forecast. '전망' + '어때' both live in the advice keywords, so we split explicitly:
# only an ACTION word routes to a recommendation; everything else outlook → forecast.
_RECO_ACTION_KW = (
    "살까", "팔까", "사야", "팔아야", "사도", "사도 돼", "사도돼", "사도 될", "매수", "매도",
    "보유할까", "담을까", "담아도", "들어갈까", "들어가도", "사는 게 좋", "사는게 좋", "사는 것이 좋",
    "살 만", "살만", "매수 타이밍", "매도 타이밍", "팔 때", "팔아도", "손절", "익절", "조언", "추천",
    "팔면", "팔아", "더 살", "더 담", "이득일까", "이익일까", "손해일까", "물렸", "지금 팔",
    "어떻게 하는 게", "어떻게 해야", "어쩌지", "어쩌면 좋",
    "should i buy", "should i sell", "should i hold", "should i get", "should i add", "buy or sell",
    "sell or hold", "hold or sell", "worth buying", "worth a buy", "good buy", "good to buy",
    "ok to buy", "time to buy", "is it a buy", "invest in", "what should i do", "your advice",
    "your advise", "recommend", "go long", "sell now", "sell it", "sell them", "if i sell",
    "will i win", "will i profit", "will i make", "will i lose", "what do you advise",
    "what do you recommend", "add more", "cut my loss", "cut the loss", "take profit",
    "lock in", "get out", "hold it", "keep it", "dump it", "offload",
    "chance of winning", "chance to win", "chance to winning", "winning chance",
    "chance of profit", "odds of winning", "승산", "이길 확률", "먹을 확률",
)


def _wants_recommendation(transcript: Optional[str]) -> bool:
    """True for a buy/sell/hold ACTION ask (→ friend-style decide). Pure outlook is False.

    TYPO-TOLERANT tier: exact phrases like 'should i buy' break on typos ('shpuld i buy'),
    which silently dropped the question to the per-bot LLM fallbacks — and two LLM
    generations are never identical, so VIP and AI Advisor answered DIFFERENTLY (boss
    complaint 2026-07-03). If the sentence contains a bare trade word (buy/sell) AND a
    resolvable stock, that alone is a recommendation ask — the deterministic decide()
    composer answers it identically everywhere."""
    t = (transcript or "").lower()
    if _is_decision_q(transcript) or any(k in t for k in _RECO_ACTION_KW):
        return True
    if _re.search(r"\b(buy|buying|bought\?|sell|selling)\b", t):
        try:
            if _stock_in_query(transcript) is not None:
                return True
            # FUZZY fallback: 'skynix' etc. — substring matching misses misspellings the
            # resolver's difflib tier catches ('I wanna buy skynix' got a daily-price
            # table instead of a decision, boss complaint 2026-07-06)
            from services.stock_resolver import resolve_one
            return (resolve_one(transcript or "") or (None,))[0] is not None
        except Exception:
            return False
    return False


def _is_bare_switch_followup(transcript: Optional[str]) -> bool:
    t = (transcript or "").strip()
    if not t or len(t.split()) > 6:
        return False
    tl = t.lower()
    # if it carries its OWN explicit intent, it's not a bare switch — handle normally
    if (any(w in tl for w in _PRICE_WORDS) or _is_past_price(t) or _is_future_outlook(t)
            or _is_stock_advice(t, "vip") or _is_daytrade_followup(t)
            or any(w in tl for w in ("news", "뉴스", "chart", "차트", "report", "리포트", "공매도"))):
        return False
    # TYPO-TOLERANT starter ("how abiut S-Oil?" — boss 2026-08-25 — missed the exact
    # 'how about' and fell to the LLM advice chain): fuzzy-fix the first few English
    # words against the known starter vocabulary before matching.
    toks = t.split()
    if toks and any(w.isascii() for w in toks[:3]):
        import difflib as _dl
        fixed = [(m[0] if w.isascii() and
                  (m := _dl.get_close_matches(w.lower(), ("how", "what", "about"), n=1, cutoff=0.75))
                  else w) for w in toks[:3]]
        t = " ".join(fixed + toks[3:])
    return bool(_SWITCH_PREFIX_RE.search(t) or _BARE_STOCK_RE.match(t)
                or _BARE_STOCK_EN_RE.match(t))


def _is_vip_current_price_q(transcript: Optional[str], agent_id: Optional[str]) -> bool:
    if (agent_id or "vip").lower() == "stock":
        return False
    # a fundamentals ask ("배당금 얼마야?") contains price words but is NOT a quote
    # request — it was answered with the current price (deep audit 2026-08-25)
    if _is_fundamentals_q(transcript):
        return False
    t = (transcript or "").lower()
    # A multi-stock COMPARISON ('compare X, Y, Z now' / 'X vs Y / 비교') is a present-price
    # ask even without an explicit price word — route it to the local comparison TABLE
    # (deterministic) instead of letting it flake through stock-delegation.
    _compare = (any(w in t for w in ("compare", "비교", " vs ", "versus", "대비"))
                and not _is_future_outlook(transcript)
                and len(_all_stocks_in_query(transcript)) >= 2)
    # A bare English field word ("today's high") counts as a price ask ONLY when a
    # specific stock is named — so it reads the same as Korean '고가', while
    # "open the stock page" still can't masquerade as a quote request.
    _bare_field = bool(_BARE_FIELD_RE.search(t)) and _stock_in_query(transcript) is not None
    # TYPO-TOLERANT price word ('current prtice' — boss 2026-08-24 — fell past the exact
    # match, and the LLM answered a price question with the full 4-algorithm advice).
    _fuzzy_price = False
    if not any(w in t for w in _PRICE_WORDS):
        import difflib as _dl
        for _tok in _re.findall(r"[a-z]{4,8}", t):
            if _dl.get_close_matches(_tok, ("price", "quote", "volume"), n=1, cutoff=0.8):
                _fuzzy_price = True
                break
    if not any(w in t for w in _PRICE_WORDS) and not _compare and not _bare_field \
            and not _fuzzy_price:
        return False
    if (_is_past_price(transcript) or _is_stock_advice(transcript, agent_id)
            or _wants_recommendation(transcript)      # 'from which price should I BUY' = a decision, not a quote
            or _is_history_range_query(transcript) or _is_us_stock_query(transcript)):
        return False
    if any(w in t for w in ("뉴스", "news", "유튜브", "youtube", "리포트", "report")):
        return False
    # Real-estate guard: '제주 토지 시세', '향남 아파트 가격' share price words with
    # stocks. When a property keyword is present and NO specific stock resolves, it's
    # a real-estate question — don't answer it with the stock watchlist.
    if (any(k in t for k in _REALESTATE_Q_KW)
            and _stock_in_query(transcript) is None):
        return False
    # Fire if a specific stock is named OR it's a generic stock-price ask (→ watchlist).
    return (_stock_in_query(transcript) is not None
            or any(w in t for w in _GENERIC_STOCK_WORDS))


def _live_price_for_code(code: str, fallback_name: Optional[str]) -> Optional[dict]:
    """One stock's live quote (price + open/high/low + volume): Kiwoom REST during
    market, Naver after. None on fail."""
    price = chg = None
    open_ = high = low = volume = None
    name = fallback_name
    source = None
    if _kr_market_open_now():
        try:
            from services import kiwoom_rest
            _k, _s = kiwoom_rest._creds()
            if _k and _s:
                kq = kiwoom_rest.current_price(code)
                if kq and kq.get("price"):
                    price, chg = kq["price"], kq.get("change_pct")
                    open_, high, low = kq.get("open"), kq.get("high"), kq.get("low")
                    volume = kq.get("volume")
                    name = kq.get("name") or name
                    source = "키움증권 실시간 시세"
        except Exception as e:
            log.warning(f"vip kiwoom price {code} failed: {str(e)[:120]}")
    if price is None:  # after-market / weekend / Kiwoom miss → Naver
        try:
            from services import naver_stock
            nq = naver_stock.realtime_quote(code)
            if nq and nq.get("price"):
                open_, high, low = nq.get("open"), nq.get("high"), nq.get("low")
                volume = nq.get("volume")
                # After the regular session, show the 시간외(NXT) price when that
                # after-market session is active; otherwise the regular close.
                if (not _kr_market_open_now() and nq.get("nxt_price")
                        and (nq.get("nxt_status") or "").upper() == "OPEN"):
                    price, chg = nq["nxt_price"], nq.get("nxt_change_pct")
                    source = "NAVER 시간외(NXT) 시세"
                else:
                    price, chg = nq["price"], nq.get("change_pct")
                    source = "NAVER 실시간 시세"
        except Exception as e:
            log.warning(f"vip naver price {code} failed: {str(e)[:120]}")
    if price is None:
        return None
    return {"code": code, "name": (name or code).upper(), "price": float(price),
            "change_pct": chg, "source": source,
            "open": open_, "high": high, "low": low, "volume": volume}


def _canon_price_src(s: Optional[str]) -> str:
    """VIP's Korean source label → the small canonical vocabulary the shared
    price_format formatter understands (kiwoom / naver_nxt / naver). Keeps VIP and
    the Stock app reading IDENTICALLY (Stock maps its own codes to the same set)."""
    s = s or ""
    if "키움" in s:
        return "kiwoom"
    if "시간외" in s or "NXT" in s.upper():
        return "naver_nxt"
    return "naver"


def _vip_live_price_reply(transcript: Optional[str], lang: str, db=None) -> Optional[dict]:
    """VIP current-price answered locally (Kiwoom during market / Naver after).
    Handles one stock, several stocks, or a bare ask (→ default watchlist).
    Returns a reply dict, or None to fall back to delegation."""
    stocks = _all_stocks_in_query(transcript)
    used_watchlist = False
    if not stocks:
        t = (transcript or "").lower()
        if any(w in t for w in _GENERIC_STOCK_WORDS):
            stocks = list(_DEFAULT_WATCHLIST)  # 'what is the current stock price'
            used_watchlist = True
        else:
            return None  # US / unresolved → let delegation handle it

    quotes = [q for q in (_live_price_for_code(c, n) for c, n in stocks) if q]
    if not quotes:
        return None

    # Kiwoom on Render: the chatbot runs on Render, whose IP often can't reach Kiwoom REST
    # directly → _live_price_for_code falls back to Naver (price OK but null OHLCV). During
    # market, the PC collector writes Kiwoom data to the snapshot (realtime_snapshot), which
    # Render CAN read — so prefer that for a real Kiwoom price + label.
    if db is not None and _kr_market_open_now():
        try:
            from services.trading_brief import _read_snapshots as _rs
            codes = [q["code"] for q in quotes if "키움" not in (q.get("source") or "")]
            # 6-min window (not 4) so a brief collector gap doesn't flicker back to Naver.
            snaps = _rs(db, codes, max_age_sec=360) if codes else {}
            for q in quotes:
                sp = (snaps.get(q["code"]) or {}).get("price")
                if sp:
                    q["price"] = float(sp)
                    q["source"] = "키움증권 실시간 시세"
        except Exception:
            pass

    now = _dt_now_kst()
    sources = sorted({q["source"] for q in quotes})

    # Answer in the user's language: English if lang='en' OR the question is
    # English (latin letters, no Hangul); Korean otherwise.
    _en = (lang or "").lower().startswith("en")
    if not _en and not _re.search(r"[가-힣]", transcript or "") \
            and _re.search(r"[a-zA-Z]", transcript or ""):
        _en = True

    # Which values did the user ask for? ('opening and current' -> [open, price]).
    fields = _requested_price_fields(transcript)

    # Backfill OHLCV from the daily endpoint whenever the realtime quote is missing them
    # (Naver's realtime `basic` returns null open/high/low/volume; the snapshot has none).
    # ALWAYS backfill all four — the answer table always shows 시가/고가/저가/거래량, so they
    # must never render as '-' when today's daily bar has the values.
    _need = ["open", "high", "low", "volume"]
    if any(q.get(f) is None for q in quotes for f in _need):
        try:
            from services import naver_stock as _ns
            for q in quotes:
                if any(q.get(f) is None for f in _need):
                    rows = _ns.daily_history(q["code"], days=1)
                    if rows:
                        for f in _need:
                            if q.get(f) is None:
                                q[f] = rows[0].get(f)
        except Exception:
            pass

    # Format via the shared canonical formatter so VIP and the AI Advisor (which
    # relays this answer) phrase it the same way.
    from services import price_format
    fmt_quotes = [{"name": q["name"], "code": q.get("code"), "price": q["price"],
                   "change_pct": q["change_pct"], "market": "KR",
                   "open": q.get("open"), "high": q.get("high"), "low": q.get("low"),
                   "volume": q.get("volume"),
                   "source": _canon_price_src(q["source"])} for q in quotes]
    reply = price_format.format_current(
        fmt_quotes, lang=("en" if _en else "ko"),
        used_watchlist=used_watchlist, as_of=now, fields=fields)

    # If the user asked for a SPECIFIC clock time (e.g. '9시 54분', 'at 9:54') we
    # have no minute-bar (분봉) data — don't silently pass off the current price as
    # that time's price. Say so honestly, then give the current price as closest.
    _tm = _requested_time_kst(transcript)
    if _tm and not _is_past_price(transcript):
        hh, mm = _tm
        note = (f"⚠️ {hh:02d}:{mm:02d} 시점의 분단위 시세는 제공되지 않습니다 (분봉 데이터 미연동). "
                f"가장 가까운 현재가를 안내드립니다:\n\n" if not _en else
                f"⚠️ Minute-level price at {hh:02d}:{mm:02d} isn't available (no intraday "
                f"minute feed). Showing the closest current price instead:\n\n")
        reply = note + reply

    _ah = _afterhours_note(_en)
    if _ah:
        reply = _ah + "\n\n" + reply

    return {"intent": "stock_price", "language": lang, "reply": reply,
            "action": None, "speak": True, "transcript": transcript,
            "tool_used": "stock_quote",
            "tool_result": {"quotes": quotes, "sources": sources}}


def _requested_history_dates(q: Optional[str]):
    """What PAST dates the question wants. Returns ('range', n_days) for 'last 4 days',
    ('dates', [date,...]) for specific days ('18th, 17th, 16th and 15th of June' or a
    single past date), or None for a non-history question."""
    from datetime import date as _date
    t = (q or "").lower()
    today = _dt_now_kst().date()
    m = _re.search(r"(?:last|past|recent|지난|최근)\s*(\d+)\s*(day|days|week|weeks|일|주)", t)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        days = n * 7 if (unit.startswith("w") or unit == "주") else n
        return ("range", max(1, min(days, 40)))
    # Korean explicit date(s): '6월 10일', '6월 10일·9일' — the English month scan below
    # only matches a-z, so Korean dates need their own parse (else they mis-route to the
    # current-price handler and answer TODAY's price instead of the asked date).
    kdays = _re.findall(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", t)
    if kdays:
        ym = _re.search(r"\b(20\d{2})\b", t)
        yr = int(ym.group(1)) if ym else today.year
        kdates = []
        for mo, dd in kdays:
            try:
                d = _date(yr, int(mo), int(dd))
                if not ym and d > today:
                    d = _date(yr - 1, int(mo), int(dd))
                kdates.append(d)
            except ValueError:
                pass
        if kdates:
            return ("dates", sorted(set(kdates), reverse=True))
    month = None
    for tok in _re.finditer(r"[a-z]{3,}", t):
        w = tok.group()
        if w in _MONTH_STOP:
            continue
        mm = _fuzzy_num(w, _MONTHS, 0.75)
        if mm:
            month = mm
            break
    if month:
        # Strip any explicit/injected ISO date ('(2026-06-18)') first so its digits
        # (06/18) aren't mistaken for extra requested days.
        t_days = _re.sub(r"\b20\d{2}[-/.]\s*\d{1,2}[-/.]\s*\d{1,2}\b", " ", t)
        dnums = sorted({int(d) for d in _re.findall(r"\b(\d{1,2})(?:st|nd|rd|th)?\b", t_days)
                        if 1 <= int(d) <= 31}, reverse=True)
        if dnums:
            ym = _re.search(r"\b(20\d{2})\b", t)
            yr = int(ym.group(1)) if ym else today.year
            dates = []
            for dd in dnums:
                try:
                    d = _date(yr, month, dd)
                    if not ym and d > today:
                        d = _date(yr - 1, month, dd)
                    dates.append(d)
                except ValueError:
                    pass
            if dates:
                return ("dates", dates)
    # A specific weekday ('last week Friday', '지난주 금요일', 'on Monday') is a
    # SINGLE-DAY question — even though 'last week' ALSO matches the range pattern
    # below. Resolve it to that one date FIRST, so "last Friday's closing price"
    # returns just that day instead of the whole week's OHLCV table
    # (boss 2026-07-27: "I asked exactly one day but it showed all days of the week").
    if _names_weekday(t):
        iso = _relative_date_iso(q)
        if iso:
            try:
                return ("dates", [_date.fromisoformat(iso)])
            except ValueError:
                pass
    # NUMBERLESS "last week / last month" ("what was the price last week" answered with
    # the CURRENT price — boss 2026-08-25): the range pattern above needs a digit, so
    # the bare phrases fell straight through to the live-price lane.
    if _re.search(r"\b(?:last|past)\s+week\b", t) or "지난주" in t or "지난 주" in t:
        return ("range", 7)
    if _re.search(r"\b(?:last|past)\s+month\b", t) or "지난달" in t or "지난 달" in t:
        return ("range", 23)
    if _is_history_range_query(q):
        return ("range", 7)
    iso = _relative_date_iso(q)
    if iso:
        try:
            _ds = [_date.fromisoformat(iso)]
            # "yesterday AND today" in one question (boss 2026-08-26: 'yesterday and
            # todays min, max of NAVER' answered only yesterday) — today's running
            # session joins the same table
            if ("오늘" in t or _re.search(r"\btoday", t)) and today not in _ds:
                _ds.append(today)
            return ("dates", _ds)
        except ValueError:
            pass
    return None


# KRX regular session — used to answer time-of-day price questions correctly.
_MKT_OPEN = (9, 0)
_MKT_CLOSE = (15, 30)


def _requested_time_kst(q: Optional[str]) -> Optional[tuple[int, int]]:
    """Parse a time-of-day from the question → (hour, minute) 24h KST, or None.
    Handles '5pm', '6 pm', '오후 5시', '오전 9시 30분', '17:00', '15시 30분'."""
    t = (q or "").lower()
    m = _re.search(r"\b([01]?\d|2[0-3])\s*:\s*([0-5]\d)\b", t)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    m = _re.search(r"(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분)?", t)
    if m:
        h, mi = int(m.group(1)), int(m.group(2) or 0)
        if h < 12 and ("오후" in t or "pm" in t):
            h += 12
        if h == 12 and ("오전" in t or "am" in t):
            h = 0
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return (h, mi)
    m = _re.search(r"\b(\d{1,2})\s*(a\.?m\.?|p\.?m\.?)\b", t)
    if m:
        h, ap = int(m.group(1)), m.group(2).replace(".", "")
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        if 0 <= h <= 23:
            return (h, 0)
    return None


def _won_str(v) -> str:
    try:
        return f"₩{int(round(float(v))):,}"
    except Exception:
        return str(v)


_FIELD_FAMILIES = (
    ("volume", ("거래량", "volume", "volumn", "volme")),
    ("open", ("시가", "open price", "opening price")),
    ("high", ("고가", "high price", "highest price")),
    ("low", ("저가", "low price", "lowest price")),
    ("close", ("종가", "close price", "closing price")),
    ("change_pct", ("등락", "변동률", "변화율", "변동", "changes", "chang")),
)


def _single_field_asked(transcript: Optional[str]) -> Optional[str]:
    """EXACTLY ONE price field asked ('SK하이닉스 어제 거래량?') → that field's name, so
    the answer is the one number + a natural offer for the rest — not the whole OHLCV
    table (boss 2026-08-25: 'I am asking only volume but it is showing other info').
    Counts BARE English words too (open/close/max/min/high/low) — 'open, close, max,
    min and volume' asked FIVE fields but only 'volume' matched a family, so the answer
    wrongly showed volume alone (boss bug report, same day)."""
    tl = (transcript or "").lower()
    if any(w in tl for w in ("전체", "모두", "다 ", "all", "ohlc", "table", "표")):
        return None
    fams = {k for k, kws in _FIELD_FAMILIES if any(w in tl for w in kws)}
    for w, k in (("open", "open"), ("close", "close"), ("high", "high"), ("low", "low"),
                 ("max", "high"), ("min", "low")):
        if _re.search(rf"\b{w}\b", tl):
            fams.add(k)
    for w, k in (("최고", "high"), ("최저", "low")):
        if w in tl:
            fams.add(k)
    return next(iter(fams)) if len(fams) == 1 else None


def _also_wants_current_price(transcript: Optional[str]) -> bool:
    """True when a past-date question ALSO explicitly asks for the CURRENT price in the
    same breath ('현재가랑 12월 10일 종가 둘 다', 'price now and its Dec 10 close'). Needs an
    explicit live-price cue so a plain '12월 10일 종가' is NOT treated as wanting current."""
    t = (transcript or "").lower()
    return (any(k in t for k in ("현재가", "현재 가격", "현재 시세", "실시간", "지금 얼마",
                                 "지금 가격", "지금 주가", "current price", "price now",
                                 "right now"))
            or bool(_re.search(r"\bcurrent\b", t)))


def _vip_history_reply(transcript: Optional[str], lang: str, hist=None,
                       history: Optional[list[dict]] = None, db=None) -> Optional[str]:
    """Deterministic multi-day OHLCV table from Naver daily history — past specific
    dates AND ranges ('last 4 days'). Single source, so VIP and the relaying AI Advisor
    read IDENTICALLY. None → caller falls through. A bare date follow-up ('7월 2일은?')
    inherits the stock from the conversation history so it gets the SAME table format.
    When the SAME sentence also asks for the current price ('현재가랑 12월 10일 종가 둘 다'),
    the live quote is prepended so BOTH asks are answered (not just the past date)."""
    hist = hist or _requested_history_dates(transcript)
    if not hist:
        return None, None
    stocks = _all_stocks_in_query(transcript)
    if not stocks and history:
        for h in reversed(history):
            s = _all_stocks_in_query(str(h.get("content") or h.get("text") or ""))
            if s:
                stocks = s[:1]
                break
    if not stocks:
        return None, None
    from services import naver_stock, price_format
    _en = (lang or "").lower().startswith("en")
    if not _en and not _re.search(r"[가-힣]", transcript or "") \
            and _re.search(r"[a-zA-Z]", transcript or ""):
        _en = True
    kind, payload = hist
    # A specific time-of-day on a single date ('yesterday at 5pm') needs a precise,
    # honest answer — not just the daily table. KRX trades 09:00–15:30 KST.
    tm = _requested_time_kst(transcript)
    single_date = kind == "dates" and len({d.isoformat() for d in payload}) == 1
    notes = []
    out = []
    # How far back to pull: a RANGE needs its length; a specific DATE needs enough
    # rows to REACH it (Dec 10 asked in July = ~160 trading days back — 60 wasn't
    # nearly enough, so old dates silently fell through to a "not available" relay).
    if kind == "range":
        _days = payload + 3
    else:
        _oldest = min(payload)
        _span = (_dt_now_kst().date() - _oldest).days
        _days = max(60, min(_span + 7, 400))
    _h_trace = None
    _h_src = None
    for code, name in stocks[:6]:
        try:
            from services.price_history import rows_traced as _phr
            rows, _h_src, _tr2 = _phr(db, code, _days)
            if _h_trace is None:
                _h_trace = _tr2
                if _h_trace is not None:
                    _h_trace["en"] = _en
        except Exception as e:
            log.warning(f"vip history {code} failed: {str(e)[:120]}")
            rows = []
        sel = []
        if rows:
            if kind == "range":
                sel = rows[:payload]
            else:
                for ds in sorted({d.isoformat() for d in payload}, reverse=True):
                    row = next((r for r in rows if r.get("date") == ds), None)
                    if not row:
                        earlier = [r for r in rows if r.get("date") and r["date"] <= ds]
                        row = earlier[0] if earlier else None
                        # OFF-DAY: the asked date had no trading (weekend/holiday) — say WHY
                        # and clearly label the substituted last-trading-day close (smart, not
                        # silently showing the wrong date's data).
                        if row:
                            try:
                                import datetime as _dt
                                _d = _dt.date.fromisoformat(ds)
                                _wd = _d.weekday()
                                _wd_ko = "월화수목금토일"[_wd]
                                _why_ko = "주말(휴장일)" if _wd >= 5 else "휴장일(공휴일)"
                                _why_en = "a weekend — market closed" if _wd >= 5 else "a market holiday"
                                notes.append(
                                    f"📅 {(name or code).upper()}: {_d.month}월 {_d.day}일({_wd_ko})은 {_why_ko}이라 거래가 없었습니다. "
                                    f"직전 거래일({row.get('date')}) 종가 {_won_str(row.get('close'))} 기준으로 안내드립니다."
                                    if not _en else
                                    f"📅 {(name or code).upper()}: {ds} was {_why_en}, so there was no trading. "
                                    f"Showing the last trading day ({row.get('date')}) instead — close {_won_str(row.get('close'))}.")
                            except Exception:
                                pass
                    if row:
                        sel.append(row)
        out.append({"name": (name or code).upper(), "code": code, "rows": sel})

        # TREND SUMMARY for a range ask (boss 2026-08-25: "overall trend last 6 days,
        # how many % changed" got only the raw table) — one line above the table:
        # net % close→close across the window + up/down day counts.
        if kind == "range" and len(sel) >= 2:
            try:
                newest, oldest = sel[0], sel[-1]
                _chg = (newest["close"] / oldest["close"] - 1) * 100
                _ups = sum(1 for r2 in sel if (r2.get("change_pct") or 0) > 0)
                _dns = sum(1 for r2 in sel if (r2.get("change_pct") or 0) < 0)
                _tr_ko = "상승 추세" if _chg > 1 else "하락 추세" if _chg < -1 else "보합권"
                _tr_en = "uptrend" if _chg > 1 else "downtrend" if _chg < -1 else "sideways"
                notes.append(
                    f"📈 **{(name or code).upper()}** {len(sel)}거래일 종합: 종가 {_won_str(oldest['close'])} → "
                    f"{_won_str(newest['close'])} = **{_chg:+.1f}%** ({_tr_ko} · 상승 {_ups}일 / 하락 {_dns}일)"
                    if not _en else
                    f"📈 **{(name or code).upper()}** over {len(sel)} trading days: close {_won_str(oldest['close'])} → "
                    f"{_won_str(newest['close'])} = **{_chg:+.1f}%** ({_tr_en} · {_ups} up / {_dns} down days)")
            except Exception:
                pass

        # Time-aware precise line (after-close → that day's close; before open →
        # previous close; intraday → no minute data on free feed).
        if tm and single_date and sel:
            row, nm, (hh, mm) = sel[0], (name or code).upper(), tm
            close, d = row.get("close"), row.get("date")
            if tm >= _MKT_CLOSE:
                if tm < (16, 0):
                    # 15:30–16:00 시간외 종가 — trades AT the regular close, so it IS the close.
                    notes.append(
                        f"{nm}: {hh:02d}:{mm:02d}는 정규장 마감(15:30) 직후 시간외 종가 시간대로, 가격은 {d} 종가와 동일한 {_won_str(close)}입니다."
                        if not _en else
                        f"{nm}: {hh:02d}:{mm:02d} is the post-close session (trades at the close), so it equals the {d} close, {_won_str(close)}.")
                else:
                    # 16:00–20:00 시간외 단일가 / 넥스트레이드(NXT): prices MOVE after 15:30. We
                    # only have the regular-session close on the free feed — be honest, don't
                    # pretend the close is the after-hours price (so 6pm ≠ 7pm is acknowledged).
                    notes.append(
                        f"{nm}: 정규장은 15:30에 마감하지만 {hh:02d}:{mm:02d}에는 시간외 단일가·넥스트레이드(NXT) 거래로 가격이 변동됩니다. 다만 과거 특정 시각의 시간외 체결가는 무료 데이터로 제공되지 않아, 확보 가능한 값은 {d} 정규장 종가 {_won_str(close)}뿐입니다."
                        if not _en else
                        f"{nm}: the regular session closes at 15:30, but at {hh:02d}:{mm:02d} after-hours / NXT trading moves the price. The historical after-hours price for that exact time isn't on the free feed — the only figure we have is the {d} regular-session close, {_won_str(close)}.")
            elif tm < _MKT_OPEN:
                try:
                    idx = rows.index(row)
                    prev = rows[idx + 1] if idx + 1 < len(rows) else None
                except ValueError:
                    prev = None
                pc, pd = (prev.get("close"), prev.get("date")) if prev else (close, d)
                notes.append(
                    f"{nm}: {hh:02d}:{mm:02d}는 장 시작(09:00) 전이라, 직전 거래일({pd}) 종가 {_won_str(pc)} 기준입니다."
                    if not _en else
                    f"{nm}: {hh:02d}:{mm:02d} is before the open (09:00) — it reflects the previous close ({pd}), {_won_str(pc)}.")
            else:
                notes.append(
                    f"{nm}: {d} 장중 {hh:02d}:{mm:02d}의 분단위 시세는 무료 데이터로 제공되지 않습니다. 당일 종가 {_won_str(close)} (고가 {_won_str(row.get('high'))} / 저가 {_won_str(row.get('low'))})."
                    if not _en else
                    f"{nm}: minute-level price for {d} {hh:02d}:{mm:02d} isn't on the free feed. Day close {_won_str(close)} (H {_won_str(row.get('high'))} / L {_won_str(row.get('low'))}).")
    if not any(s["rows"] for s in out):
        return None, None
    # ONE FIELD ASKED → ONLY that field answered (boss 2026-08-25: "I asked only changes
    # but it is showing all days' info, which is token consumption").
    _fld = _single_field_asked(transcript)
    # RANGE + one field: compact per-day list of just that field, plus the verdict note.
    if _fld and kind == "range" and not tm:
        _F_KO2 = {"volume": "거래량", "open": "시가", "high": "고가", "low": "저가",
                  "close": "종가", "change_pct": "등락"}
        _fl2 = list(notes)
        for s in out:
            if not s["rows"]:
                continue
            if len(out) > 1 or not notes:
                _fl2.append(f"**{s['name']}** — {_F_KO2[_fld] if not _en else _fld.replace('_pct', '')}:")
            for row in s["rows"]:
                v = row.get(_fld)
                if v is None:
                    continue
                if _fld == "change_pct":
                    _val = f"**{v:+.2f}%**"
                elif _fld == "volume":
                    _val = f"**{int(v):,}{'주' if not _en else ' shares'}**"
                else:
                    _val = f"**{_won_str(v)}**"
                _fl2.append(f"- {row.get('date')}: {_val}")
        if len(_fl2) > len(notes):
            _fl2 += ["", ("Want the full daily table (open/high/low/close/volume)? Just ask."
                          if _en else
                          "전체 표(시가·고가·저가·종가·거래량)가 필요하시면 말씀해 주세요.")]
            return "\n".join(_fl2), _h_trace
    # single past date + one field
    if _fld and kind == "dates" and len({d.isoformat() for d in payload}) == 1 and not tm:
        _F_KO = {"volume": "거래량", "open": "시가", "high": "고가", "low": "저가",
                 "close": "종가", "change_pct": "등락"}
        _fl = list(notes)
        for s in out:
            if not s["rows"]:
                continue
            row = s["rows"][0]
            v = row.get(_fld)
            if v is None:
                continue
            if _fld == "change_pct":
                _val = f"{v:+.2f}%"
            elif _fld == "volume":
                _val = f"{int(v):,}" + (" shares" if _en else "주")
            else:
                _val = _won_str(v)
            _lbl = _fld.replace("_pct", "") if _en else _F_KO[_fld]
            _fl.append(f"**{s['name']}** — {row.get('date')} {_lbl}: **{_val}**")
        if len(_fl) > len(notes):
            _fl += ["", ("Want that day's open/high/low/close too, or another date? Just ask."
                         if _en else
                         "원하시면 그날의 시가·고가·저가·종가 전체나 다른 날짜도 바로 알려드릴게요.")]
            result = "\n".join(_fl)
            if _also_wants_current_price(transcript):
                try:
                    cur = _vip_live_price_reply(transcript, lang, db)
                    if cur and cur.get("reply"):
                        result = cur["reply"].rstrip() + "\n\n---\n\n" + result
                except Exception:
                    pass
            return result, _h_trace
    table = price_format.format_history(out, lang=("en" if _en else "ko"))
    # the table footer must name OUR source when our data served it (boss 2026-08-25)
    if _h_src and ("자체" in _h_src or "데이터 PC" in _h_src):
        table = table.replace("Source: Naver Finance (daily OHLCV)",
                              f"Source: {_h_src} — our own collected data")
        table = table.replace("출처: 네이버 금융 (일봉 OHLCV)",
                              f"출처: {_h_src} — 우리 서버 수집 데이터")
    result = ("\n".join(notes) + "\n\n" + table) if notes else table
    # 'current price AND the 12/10 close, both' — prepend the live quote so the
    # current-price half isn't dropped by the past-date route (boss test 2026-07-22).
    if _also_wants_current_price(transcript):
        try:
            cur = _vip_live_price_reply(transcript, lang, db)
            if cur and cur.get("reply"):
                result = cur["reply"].rstrip() + "\n\n---\n\n" + result
        except Exception:
            pass
    return result, _h_trace


# ===== PERIOD STATS — "삼성바이오 최근 6개월 최저/최고/거래량" / "last 6 months min, max,
# volume of Samsung Bio" → deterministic summary table + monthly breakdown + a written
# explanation from Naver daily history (up to ~18 months). The old lanes couldn't answer
# this: _requested_history_dates only parses day/week ranges (≤40 rows) and would either
# dump a raw table or fall through to the live-price route (boss ask 2026-08-24). =====

_PERIOD_STATS_KW = ("최저", "최고", "저가", "고가", "거래량", "평균", "요약", "변동", "추이", "흐름",
                    "min", "max", "low", "high", "volume", "average", "avg", "summary",
                    "range", "stats", "statistic", "trend", "movement")


def _period_stats_window(q: Optional[str]):
    """A months-long window in the question → (cutoff_date, months, label_ko, label_en),
    else None. Day/week ranges stay with the existing history-table lane."""
    from datetime import date as _date
    t = (q or "").lower()
    today = _dt_now_kst().date()

    def _back(months: int):
        y, m = today.year, today.month - months
        while m <= 0:
            y, m = y - 1, m + 12
        try:
            return _date(y, m, min(today.day, 28))
        except ValueError:
            return _date(y, m, 28)

    # 'mo/mon/mont/month/months/monthes' — the boss types fast ('last 6 mont' returned
    # the CURRENT price because the window parse missed, 2026-08-25)
    # cap raised 18 → 132 months (2026-08-25): history now serves from OUR OWN DB
    # (raw_daily_prices, 2015→) instead of Naver's ~18-month window
    m = _re.search(r"(\d+)\s*(?:개\s*월|달)", t) or _re.search(r"(\d+)\s*mo(?:n(?:th?e?s?)?)?\b", t)
    if m:
        n = max(1, min(int(m.group(1)), 132))
        return _back(n), n, f"최근 {n}개월", f"last {n} months"
    m = _re.search(r"(\d+)\s*년", t) or _re.search(r"(\d+)\s*(?:years?|yrs?)\b", t)
    if m:
        n = max(1, min(int(m.group(1)) * 12, 132))
        return _back(n), n, f"최근 {n // 12}년" if n % 12 == 0 else f"최근 {n}개월", \
            f"last {n // 12} year(s)" if n % 12 == 0 else f"last {n} months"
    if _re.search(r"반\s*년|half\s*(?:a\s*)?year", t):
        return _back(6), 6, "최근 6개월(반년)", "last 6 months"
    if _re.search(r"일\s*년|한\s*해|a\s+year|one\s+year", t):
        return _back(12), 12, "최근 1년", "last 1 year"
    if _re.search(r"올해|금년|this\s+year|ytd", t):
        cut = _date(today.year, 1, 1)
        n = max(1, today.month)
        return cut, n, "올해(연초 이후)", "this year (YTD)"
    return None


def _is_period_stats_q(transcript: Optional[str]) -> bool:
    t = (transcript or "").lower()
    return bool(t) and _period_stats_window(t) is not None \
        and any(k in t for k in _PERIOD_STATS_KW)


def _vol_str(v, en: bool) -> str:
    try:
        return f"{int(v):,}" + ("" if en else "주")
    except Exception:
        return str(v)


def _period_stats_reply(transcript: Optional[str], lang: str,
                        history: Optional[list[dict]] = None, db=None) -> Optional[str]:
    """Deterministic period summary (no LLM — same speed rule as the price lanes):
    headline table (high/low/volume with dates) + per-month breakdown + explanation."""
    if not _is_period_stats_q(transcript):
        return None, None
    win = _period_stats_window(transcript)
    if not win:
        return None, None
    cutoff, months, label_ko, label_en = win
    stocks = _all_stocks_in_query(transcript)
    if not stocks and history:            # bare follow-up: "그럼 최근 3개월 최저가는?"
        for h in reversed(history):
            s = _all_stocks_in_query(str(h.get("content") or h.get("text") or ""))
            if s:
                stocks = s[:1]
                break
    if not stocks:
        return None, None
    from services import naver_stock
    _en = (lang or "").lower().startswith("en")
    if not _en and not _re.search(r"[가-힣]", transcript or "") \
            and _re.search(r"[a-zA-Z]", transcript or ""):
        _en = True
    cut_iso = cutoff.isoformat()
    sections = []
    _src_lbl = None
    _ds_trace = None
    for code, name in stocks[:3]:
        try:
            from services.price_history import rows_traced as _ph_rows
            rows, _src_lbl, _tr = _ph_rows(db, code, min(2900, months * 23 + 15))
            if _ds_trace is None:
                _ds_trace = _tr
                if _ds_trace is not None:
                    _ds_trace["en"] = _en
        except Exception as e:
            log.warning(f"period stats {code} failed: {str(e)[:120]}")
            rows = []
        sel = [r for r in rows if r.get("date") and r["date"] >= cut_iso and r.get("close")]
        if len(sel) < 2:
            continue
        chron = list(reversed(sel))                       # oldest → newest
        hi_row = max(sel, key=lambda r: r.get("high") or 0)
        lo_row = min(sel, key=lambda r: r.get("low") or 10 ** 12)
        hi, lo = hi_row.get("high"), lo_row.get("low")
        first, last = chron[0], chron[-1]
        chg = (last["close"] - first["close"]) / first["close"] * 100
        vols = [r.get("volume") or 0 for r in sel]
        avg_vol = sum(vols) / len(vols) if vols else 0
        mv_row = max(sel, key=lambda r: r.get("volume") or 0)
        pos = (last["close"] - lo) / (hi - lo) * 100 if hi and lo and hi > lo else 50
        width = (hi - lo) / lo * 100 if lo else 0
        recent20 = [r.get("volume") or 0 for r in sel[:20]]
        v_ratio = (sum(recent20) / len(recent20) / avg_vol) if (recent20 and avg_vol) else None
        nm = (name or code).upper()
        if _en:
            try:
                from services.stock_resolver import display_name_en
                nm = display_name_en(code) or nm
            except Exception:
                pass
        # ---- headline summary table (assembled LAST — boss 2026-08-25: "if we ask past
        # data it should directly start answering from daily information, not summary —
        # summary should be in the end")
        if _en:
            sum_tbl = [
                 "| Item | Value | Date |", "|---|---|---|",
                 f"| Period high | {_won_str(hi)} | {hi_row.get('date')} |",
                 f"| Period low | {_won_str(lo)} | {lo_row.get('date')} |",
                 f"| Start close | {_won_str(first['close'])} | {first['date']} |",
                 f"| Latest close | {_won_str(last['close'])} | {last['date']} |",
                 f"| Period change | {chg:+.1f}% | |",
                 f"| Avg daily volume | {_vol_str(avg_vol, True)} | |",
                 f"| Biggest volume day | {_vol_str(mv_row.get('volume'), True)} | {mv_row.get('date')} |"]
            S = [f"**📊 {nm} ({code}) — {label_en} daily data ({first['date']} ~ {last['date']} · {len(sel)} trading days)**"]
        else:
            sum_tbl = [
                 "| 항목 | 값 | 날짜 |", "|---|---|---|",
                 f"| 기간 최고가 | {_won_str(hi)} | {hi_row.get('date')} |",
                 f"| 기간 최저가 | {_won_str(lo)} | {lo_row.get('date')} |",
                 f"| 기간 시작 종가 | {_won_str(first['close'])} | {first['date']} |",
                 f"| 최근 종가 | {_won_str(last['close'])} | {last['date']} |",
                 f"| 기간 등락률 | {chg:+.1f}% | |",
                 f"| 하루 평균 거래량 | {_vol_str(avg_vol, False)} | |",
                 f"| 최대 거래량일 | {_vol_str(mv_row.get('volume'), False)} | {mv_row.get('date')} |"]
            S = [f"**📊 {nm} ({code}) — {label_ko} 일별 데이터 ({first['date']} ~ {last['date']} · {len(sel)} 거래일)**"]
        # ---- monthly breakdown (assembled AFTER the daily rows below)
        by_m: dict[str, list] = {}
        for r in chron:
            by_m.setdefault(r["date"][:7], []).append(r)
        mon_tbl = [("| Month | Low | High | Avg volume | Month-end close |" if _en
                    else "| 월 | 최저가 | 최고가 | 평균 거래량 | 월말 종가 |"), "|---|---|---|---|---|"]
        for mk in sorted(by_m):
            mr = by_m[mk]
            m_lo = min(r.get("low") or 10 ** 12 for r in mr)
            m_hi = max(r.get("high") or 0 for r in mr)
            m_av = sum(r.get("volume") or 0 for r in mr) / len(mr)
            mon_tbl.append(f"| {mk} | {_won_str(m_lo)} | {_won_str(m_hi)} | {_vol_str(m_av, _en)} | {_won_str(mr[-1]['close'])} |")
        # ---- EVERY DAY's data FIRST (boss 2026-08-25: "it should directly start
        # answering from daily information, not summary — summary in the end")
        S += ["", ("| Date | Open | High | Low | Close | Volume |" if _en
                   else "| 날짜 | 시가 | 고가 | 저가 | 종가 | 거래량 |"), "|---|---|---|---|---|---|"]
        for r in chron:
            S.append(f"| {r.get('date')} | {_won_str(r.get('open')) if r.get('open') else '-'} "
                     f"| {_won_str(r.get('high'))} | {_won_str(r.get('low'))} "
                     f"| {_won_str(r.get('close'))} | {int(r.get('volume') or 0):,} |")
        # ---- written explanation (deterministic, from the numbers above)
        zone_ko = "저점권" if pos <= 30 else "고점권" if pos >= 70 else "중간 구간"
        zone_en = "near the period low" if pos <= 30 else "near the period high" if pos >= 70 else "mid-range"
        if _en:
            expl = [f"**Explanation:** over the {label_en}, {nm} moved between {_won_str(lo)} "
                    f"({lo_row.get('date')}) and {_won_str(hi)} ({hi_row.get('date')}) — a {width:.1f}% band. "
                    f"The latest close {_won_str(last['close'])} sits at the {pos:.0f}% point of that band ({zone_en}), "
                    f"and the period return is {chg:+.1f}%."]
            if v_ratio is not None:
                expl.append(f"Recent trading is {'heavier' if v_ratio >= 1.15 else 'lighter' if v_ratio <= 0.85 else 'about the same as'} "
                            f"the period norm — the last ~20 sessions averaged {v_ratio:.1f}× the period's daily volume.")
        else:
            expl = [f"**설명:** {label_ko} 동안 {nm}는 최저 {_won_str(lo)}({lo_row.get('date')}) ~ "
                    f"최고 {_won_str(hi)}({hi_row.get('date')}) 사이, 폭 {width:.1f}%에서 움직였습니다. "
                    f"최근 종가 {_won_str(last['close'])}는 이 범위의 {pos:.0f}% 지점({zone_ko})이며, "
                    f"기간 수익률은 {chg:+.1f}%입니다."]
            if v_ratio is not None:
                expl.append(f"최근 20거래일 평균 거래량은 기간 평균의 {v_ratio:.1f}배로, "
                            f"{'거래가 활발해진' if v_ratio >= 1.15 else '거래가 한산해진' if v_ratio <= 0.85 else '평소 수준의'} 흐름입니다.")
        # order: daily rows (already in S) → monthly → period summary → explanation
        S += ["", ("**📅 Monthly breakdown**" if _en else "**📅 월별 요약**"), ""] + mon_tbl
        S += ["", ("**📌 Period summary**" if _en else "**📌 기간 요약**"), ""] + sum_tbl
        S += ["", " ".join(expl)]
        sections.append("\n".join(S))
    if not sections:
        return None, None
    out = "\n\n---\n\n".join(sections)
    _own_src = bool(_src_lbl and ("자체" in _src_lbl or "데이터 PC" in _src_lbl))
    out += (f"\n\n📦 {'Source' if _en else '데이터 출처'}: "
            + (_src_lbl or ("네이버" if not _en else "Naver"))
            + ((" — our own collected data" if _en else " — 우리 서버 수집 데이터")
               if _own_src else ""))
    return out, _ds_trace


# ===== 📚 FUNDAMENTALS / CONSENSUS (deep audit 2026-08-25): PER·배당·시가총액·52주·
# 외국인비율·목표가 questions either got an LLM apology ("doesn't provide its PER"),
# the CURRENT PRICE (배당금+얼마 stolen by the price lane), or an unsourced consensus
# number that looked invented. Answered deterministically from live disclosure data. =====
_FUND_RE = _re.compile(r"\bper\b|\bpbr\b|\beps\b|\bbps\b|\broe\b", _re.I)
_FUND_KW = ("배당", "dividend", "시가총액", "시총", "market cap", "marketcap", "market value",
            "market capitalization", "외국인 비율", "외국인비율", "외국인 지분",
            "foreign ownership", "foreign rate", "52주", "52 week", "52-week",
            "목표가", "목표 주가", "target price", "컨센서스", "consensus", "투자의견",
            "애널리스트", "analyst rating", "analyst target", "fundamental", "펀더멘털",
            "밸류에이션", "valuation",
            # "is it expensive?" is a VALUATION ask (deep audit 2026-08-25: it got a
            # current-price table) — answer with PER/PBR + the 52-week band
            "expensive", "overvalued", "undervalued", "너무 비싸", "고평가", "저평가")


def _is_fundamentals_q(transcript: Optional[str]) -> bool:
    t = (transcript or "").lower()
    if not t:
        return False
    if not (_FUND_RE.search(t) or any(k in t for k in _FUND_KW)):
        return False
    return bool(_all_stocks_in_query(transcript))


def _fundamentals_reply(transcript: Optional[str], lang: str, db=None) -> Optional[str]:
    """Focused, sourced answer for fundamentals/consensus asks — only the groups the
    user asked about (boss: no unnecessary words), full table when the ask is broad."""
    stocks = _all_stocks_in_query(transcript)
    if not stocks:
        return None
    from services import naver_stock as _ns
    _en = (lang or "").lower().startswith("en")
    if not _en and not _re.search(r"[가-힣]", transcript or "") \
            and _re.search(r"[a-zA-Z]", transcript or ""):
        _en = True
    t = (transcript or "").lower()
    w_target = any(k in t for k in ("목표가", "목표 주가", "target price", "컨센서스",
                                    "consensus", "투자의견", "analyst"))
    w_div = "배당" in t or "dividend" in t
    w_mcap = any(k in t for k in ("시가총액", "시총", "market cap", "marketcap",
                                  "market value", "market capitalization"))
    w_frn = "외국인" in t or "foreign" in t
    w_pricey = any(k in t for k in ("expensive", "overvalued", "undervalued", "너무 비싸",
                                    "고평가", "저평가"))
    w_52 = "52" in t or w_pricey
    w_val = bool(_FUND_RE.search(t)) or "밸류에이션" in t or "valuation" in t or "펀더멘털" in t \
        or "fundamental" in t or w_pricey
    broad = not any((w_target, w_div, w_mcap, w_frn, w_52, w_val))
    secs = []
    for code, name in stocks[:3]:
        f = _ns.fundamentals(code)
        if not f:
            continue
        info = f.get("info") or {}

        def g(k):
            return info.get(k) or "-"
        nm = (name or code).upper()
        if _en:
            try:
                from services.stock_resolver import display_name_en as _dne
                nm = _dne(code) or nm
            except Exception:
                pass
        rows = []
        if w_val or broad:
            rows += [("PER", g("per")), ("PBR", g("pbr")), ("EPS", g("eps")),
                     ("BPS", g("bps"))]
        if w_div or broad:
            rows += [("배당금(주당)" if not _en else "Dividend/share", g("dividend")),
                     ("배당수익률" if not _en else "Dividend yield", g("dividendYieldRatio"))]
        if w_mcap or broad:
            rows += [("시가총액" if not _en else "Market cap", g("marketValue"))]
        if w_frn or broad:
            rows += [("외국인 비율" if not _en else "Foreign ownership", g("foreignRate"))]
        if w_52 or broad:
            rows += [("52주 최고" if not _en else "52-week high", g("highPriceOf52Weeks")),
                     ("52주 최저" if not _en else "52-week low", g("lowPriceOf52Weeks"))]
        L = [f"**📚 {nm} ({code})**"]
        if rows:
            L += ["", ("| 항목 | 값 |" if not _en else "| Item | Value |"), "|---|---|"]
            L += [f"| {k} | {v} |" for k, v in rows]
        if w_target or broad:
            tm, rm = f.get("target_mean"), f.get("recomm_mean")
            if tm:
                up = ""
                try:
                    from services.paper_desk import _live_price
                    px, _n2 = _live_price(code)
                    if px:
                        _u = (float(str(tm).replace(",", "")) / float(px) - 1) * 100
                        up = (f" — 현재가 대비 {_u:+.1f}%" if not _en
                              else f" — {_u:+.1f}% vs the current price")
                except Exception:
                    pass
                L += ["", (f"🎯 증권사 컨센서스 목표가: **{tm}원** (평균 투자의견 {rm}/5 · "
                           f"{f.get('consensus_date')} 기준){up}" if not _en else
                           f"🎯 Analyst consensus target: **₩{tm}** (mean rating {rm}/5 · "
                           f"as of {f.get('consensus_date')}){up}")]
                for r in (f.get("researches") or [])[:2]:
                    _ru9 = str(r.get("url") or r.get("link") or "").strip()
                    _rt9 = (f"[{r.get('title')}]({_ru9})" if _ru9.startswith("http")
                            else r.get("title"))
                    L.append(f"   · {r.get('broker')}: {_rt9} ({r.get('date')})")
                L.append("_컨센서스는 참고용입니다 — 저희 매매 판단은 100체크리스트+알고리즘 기준입니다._"
                         if not _en else
                         "_Consensus is reference only — our trading decisions follow the "
                         "100-item checklist + our algorithms._")
        if len(L) > 1:
            secs.append("\n".join(L))
    if not secs:
        return None
    out = "\n\n---\n\n".join(secs)
    out += ("\n\n📦 출처: 시장 공시·증권사 컨센서스 데이터 (실시간 조회)" if not _en
            else "\n\n📦 Source: market disclosure & analyst consensus data (live)")
    return out


def _is_adviceish(transcript: Optional[str]) -> bool:
    """'그럼 지금 사도 돼?' is a buy/sell advice ask even with no stock named — it must
    reach the checklist advice (with the context stock), never the confirm-chat LLM,
    which hallucinated '투자 조언은 제공해드릴 수 없습니다' (2026-08-26 audit)."""
    try:
        from services.checklist_advice import kind
        return kind(transcript) is not None
    except Exception:
        return False


def _is_cancelish(transcript: Optional[str]) -> bool:
    """A cancel-order intent ('cancle naver which I bought') must reach the chat
    order desk, not the portfolio lane (2026-08-26)."""
    try:
        from services.chat_trade import has_cancel_word
        return has_cancel_word((transcript or "").lower())
    except Exception:
        return False


def _is_my_chat_orders_q(transcript: Optional[str]) -> bool:
    t = (transcript or "").lower()
    if not t:
        return False
    who = "챗봇" in t or "chatbot" in t or "채팅으로" in t or "by chat" in t or "using chat" in t
    act = any(k in t for k in ("샀", "산 ", "bought", "buy", "buys", "팔", "sold", "매수",
                               "매도", "주문", "order", "trade"))
    ask_ = "?" in t or any(k in t for k in ("뭐", "what", "which", "보여", "show", "list", "얼마"))
    return who and act and ask_


def _my_chat_orders_reply(db, transcript: Optional[str], lang: str) -> Optional[str]:
    """Today's 💬 chatbot orders, straight from the record — fills, waiting, cancelled."""
    en = not _re.search(r"[가-힣]", transcript or "") and bool(_re.search(r"[a-zA-Z]", transcript or ""))
    from datetime import timedelta as _td9, timezone as _tz9
    KST = _tz9(_td9(hours=9))
    from sqlalchemy import text as _sqt
    rows = db.execute(_sqt(
        "SELECT name, side, qty, status, fill_price, limit_price, realized_pnl, created_at "
        "FROM paper_desk_orders WHERE COALESCE(source,'') IN ('chat','chatbot') "
        "ORDER BY id DESC LIMIT 40")).fetchall()
    today8 = _dt_now_kst().strftime("%Y%m%d")
    L = []
    for r in rows:
        try:
            if r[7] is None or r[7].astimezone(KST).strftime("%Y%m%d") != today8:
                continue
            tm = r[7].astimezone(KST).strftime("%H:%M")
        except Exception:
            continue
        side_ko = "매수" if r[1] == "BUY" else "매도"
        px = r[4] or r[5]
        st = {"FILLED": ("체결됨", "filled"), "OPEN": ("🕐 대기 중(미체결)", "🕐 waiting (not filled)"),
              "CANCELLED": ("취소됨", "cancelled"), "REJECTED": ("거부됨", "rejected")}.get(
                  str(r[3]), (str(r[3]), str(r[3])))
        pnl = f" · 손익 ₩{float(r[6]):,.0f}" if (not en and r[6] is not None) else \
              f" · P&L ₩{float(r[6]):,.0f}" if r[6] is not None else ""
        L.append((f"· {tm} {side_ko} **{r[0]}** {int(r[2] or 0):,}주"
                  + (f" @ ₩{float(px):,.0f}" if px else "") + f" — {st[0]}{pnl}") if not en else
                 (f"· {tm} {r[1]} **{r[0]}** {int(r[2] or 0):,} sh"
                  + (f" @ ₩{float(px):,.0f}" if px else "") + f" — {st[1]}{pnl}"))
    if not L:
        return ("오늘 챗봇으로 주문한 내역이 없습니다." if not en
                else "No chatbot orders today.")
    head = ("**💬 오늘 챗봇으로 하신 주문** (최신순)" if not en
            else "**💬 Your chatbot orders today** (newest first)")
    tail = ("\n\n대기 주문 취소는 \"종목명 주문 취소\"라고 말씀하세요." if not en
            else "\n\nSay \"cancel <stock> order\" to cancel a waiting one.")
    return head + "\n" + "\n".join(L) + tail


_DESK_PNL_KW = ("수익", "손익", "이익", "벌었", "얼마 벌", "profit", "p&l", "pnl", "make today",
                "made today", "money did", "earn")


def _is_desk_pnl_q(transcript: Optional[str]) -> bool:
    t = (transcript or "").lower()
    if not t or not any(k in t for k in _DESK_PNL_KW):
        return False
    return any(k in t for k in ("오늘", "today", "데스크", "desk", "지금까지", "so far"))


def _desk_pnl_reply(db, transcript: Optional[str], lang: str) -> Optional[str]:
    """Today's desk money, from the order record: realized per source + open count."""
    en = not _re.search(r"[가-힣]", transcript or "") and bool(_re.search(r"[a-zA-Z]", transcript or ""))
    from datetime import timedelta as _td9, timezone as _tz9
    KST = _tz9(_td9(hours=9))
    from sqlalchemy import text as _sqt
    rows = db.execute(_sqt(
        "SELECT COALESCE(source,'manual'), realized_pnl, created_at FROM paper_desk_orders "
        "WHERE status='FILLED' AND realized_pnl IS NOT NULL "
        "ORDER BY id DESC LIMIT 4000")).fetchall()
    today8 = _dt_now_kst().strftime("%Y%m%d")
    by_src: dict = {}
    for s, p, c in rows:
        try:
            if c is None or c.astimezone(KST).strftime("%Y%m%d") != today8:
                continue
        except Exception:
            continue
        k = "chatbot" if str(s) in ("chat", "chatbot") else str(s)
        d = by_src.setdefault(k, {"n": 0, "pnl": 0.0})
        d["n"] += 1
        d["pnl"] += float(p or 0)
    total = sum(d["pnl"] for d in by_src.values())
    n_open = db.execute(_sqt("SELECT count(*) FROM paper_desk_positions WHERE qty > 0")).scalar() or 0
    _tot_c = "#b02a2a" if total >= 0 else "#1565c0"  # noqa: F841 (color for future use)
    L = [("**💰 오늘 데스크 실현 손익 (매도 완료 기준)**" if not en
          else "**💰 Today's desk realized P&L (closed sells only)**"), ""]
    if by_src:
        for k in sorted(by_src, key=lambda x: -by_src[x]["pnl"]):
            d = by_src[k]
            lab = "💬 chatbot" if k == "chatbot" else k
            L.append(f"· {lab}: ₩{d['pnl']:+,.0f} ({d['n']}{'건' if not en else ' sells'})")
        L += ["", (f"**합계: ₩{total:+,.0f}**" if not en else f"**Total: ₩{total:+,.0f}**")]
    else:
        L.append("오늘 실현된(매도 완료) 손익이 아직 없습니다." if not en
                 else "Nothing realized (no closed sells) yet today.")
    L.append((f"현재 보유 {int(n_open)}종목 — 미실현 평가손익은 데스크 화면에서 실시간으로 보입니다."
              if not en else
              f"{int(n_open)} positions open now — unrealized P&L updates live on the desk pages."))
    return "\n".join(L)


def _looks_refusal(text: str) -> bool:
    """An apology / 'no information' non-answer — the thing the boss never wants."""
    tl = (text or "").lower()
    return any(p in tl for p in ("i'm sorry", "i am sorry", "cannot provide", "can't provide",
                                 "unable to provide", "do not have access", "don't have access",
                                 "no information available", "not able to answer",
                                 "죄송하지만", "죄송합니다만", "제공할 수 없", "정보가 없습니다",
                                 "알 수 없습니다", "도와드릴 수 없"))


def _ground_pack(db, stocks) -> str:
    """Compact REAL-data block for up to 2 named stocks — live price, last 5 daily rows
    (our DB), fundamentals, consensus. Injected into the fallback LLM's system prompt so
    ANY question shape is grounded (boss 2026-08-25: 'not only for specific questions —
    it must answer in all cases')."""
    parts = []
    for code, name in stocks[:2]:
        L = [f"### {(name or code).upper()} ({code})"]
        try:
            from services.paper_desk import _chg_cache, _live_price
            px, _n = _live_price(code)
            if px:
                _c = _chg_cache.get(code)
                L.append(f"- live price ₩{px:,.0f}" + (f" ({_c:+.2f}% today)" if _c is not None else ""))
        except Exception:
            pass
        try:
            from services.price_history import rows as _ph5
            rws, _s5 = _ph5(db, code, 5)
            for r in rws[:5]:
                L.append(f"- {r.get('date')}: close {r.get('close')} "
                         f"({r.get('change_pct') if r.get('change_pct') is not None else '?'}%) "
                         f"open {r.get('open')} high {r.get('high')} low {r.get('low')} "
                         f"vol {r.get('volume')}")
        except Exception:
            pass
        try:
            from services.naver_stock import fundamentals as _fund
            f = _fund(code) or {}
            info = f.get("info") or {}
            if info:
                L.append(f"- PER {info.get('per')} · PBR {info.get('pbr')} · mktcap {info.get('marketValue')} "
                         f"· div yield {info.get('dividendYieldRatio')} · 52w {info.get('lowPriceOf52Weeks')}"
                         f"~{info.get('highPriceOf52Weeks')} · foreign {info.get('foreignRate')}")
            if f.get("target_mean"):
                L.append(f"- analyst consensus target {f['target_mean']}원 "
                         f"(mean rating {f.get('recomm_mean')}/5, {f.get('consensus_date')})")
        except Exception:
            pass
        if len(L) > 1:
            parts.append("\n".join(L))
    return "\n\n".join(parts)


# ===== ❓ WHY DID IT MOVE (deep audit 2026-08-25): "why did SK hynix drop yesterday?"
# got an INVENTED "2024-08-24 foreign sell-off" story from the analyst LLM. Answer from
# what we actually KNOW — the real daily row, our news intern's stamps for that day,
# the market's own move — and say honestly when no specific news was collected. =====

def _is_why_move_q(transcript: Optional[str]) -> bool:
    t = (transcript or "").lower()
    if not t:
        return False
    why = "why" in t or "왜" in t or "이유" in t
    move = any(k in t for k in ("drop", "fell", "fall", "down", "떨어", "하락", "내렸",
                                "내려", "빠졌", "급락", "rose", "rise", " up", "올랐",
                                "올라", "상승", "급등", "jump", "surge", "plunge"))
    return why and move and bool(_all_stocks_in_query(transcript))


def _why_move_reply(transcript: Optional[str], lang: str, db=None):
    stocks = _all_stocks_in_query(transcript)
    if not stocks:
        return None, None
    code, name = stocks[0]
    _en = (lang or "").lower().startswith("en")
    if not _en and not _re.search(r"[가-힣]", transcript or "") \
            and _re.search(r"[a-zA-Z]", transcript or ""):
        _en = True
    from services.price_history import rows_traced as _phr
    rows, _src, _tr = _phr(db, code, 8)
    if not rows:
        return None, None
    if _tr is not None:
        _tr["en"] = _en
    t = (transcript or "").lower()
    today_iso = _dt_now_kst().date().isoformat()
    if "어제" in t or "yesterday" in t:
        row = next((r for r in rows if r.get("date") and r["date"] < today_iso), rows[0])
    else:
        row = rows[0]
    chg = row.get("change_pct")
    nm = (name or code).upper()
    if _en:
        try:
            from services.stock_resolver import display_name_en as _dne
            nm = _dne(code) or nm
        except Exception:
            pass
    # the market's own move that day (KODEX 200 as the index proxy)
    mkt = None
    try:
        mrows, _m2, _m3 = _phr(db, "069500", 10)
        _mrow = next((r for r in mrows if r.get("date") == row.get("date")), None)
        mkt = _mrow.get("change_pct") if _mrow else None
    except Exception:
        pass
    # our news intern's stamps for that day
    stamps = []
    try:
        import json as _json
        from pathlib import Path as _P
        nd = _P(__file__).resolve().parent.parent / "data" / "news_intern"
        d8 = str(row.get("date") or "").replace("-", "")
        for p in sorted(nd.glob("2*.jsonl")):
            if d8 and d8 in p.name:
                for ln in p.read_text(encoding="utf-8").splitlines():
                    try:
                        rec = _json.loads(ln)
                    except Exception:
                        continue
                    if rec.get("code") == code:
                        stamps.append(rec)
    except Exception:
        pass
    stamps = stamps[-4:]
    up = (chg or 0) > 0
    dirn_ko = "상승" if up else "하락"
    dirn_en = "rise" if up else "drop"
    L = [f"❓ **{nm} — {row.get('date')} {dirn_ko} 이유**" if not _en else
         f"❓ **{nm} — why the {dirn_en} on {row.get('date')}**", ""]
    # if the user assumed the wrong direction, correct it honestly first
    said_drop = any(k in t for k in ("drop", "fell", "fall", "떨어", "하락", "내렸", "빠졌", "급락"))
    if said_drop and up:
        L.append("실제로는 이날 **올랐습니다** — 아래가 실제 수치입니다." if not _en
                 else "It actually **rose** that day — here are the real figures.")
    _chg_s = f"{chg:+.2f}%" if chg is not None else "-"
    L.append((f"{'📉' if not up else '📈'} 실제 움직임: 종가 {_won_str(row.get('close'))} ({_chg_s}) · "
              f"거래량 {int(row.get('volume') or 0):,}주") if not _en else
             (f"{'📉' if not up else '📈'} The real move: close {_won_str(row.get('close'))} ({_chg_s}) · "
              f"volume {int(row.get('volume') or 0):,}"))
    if mkt is not None and chg is not None:
        same = (mkt > 0) == (chg > 0)
        if not same:
            rel_ko, rel_en = "시장과 반대로 움직였습니다", "it moved AGAINST the market"
        elif abs(chg) > abs(mkt) + 1:
            rel_ko, rel_en = "시장보다 훨씬 크게 움직였습니다 — 종목 자체 요인 가능성", \
                "a much bigger move than the market — likely stock-specific"
        else:
            rel_ko, rel_en = "시장 전체와 같은 방향·비슷한 폭입니다 — 시장 흐름 영향이 커 보입니다", \
                "same direction and similar size as the market — mostly a market-wide move"
        L.append(f"📊 같은 날 시장(KODEX200) {mkt:+.2f}% → {rel_ko}" if not _en
                 else f"📊 The market (KODEX200) that day: {mkt:+.2f}% → {rel_en}")
    if stamps:
        L.append("📰 그날 우리 뉴스 판정:" if not _en else "📰 Our news rulings that day:")
        for s in stamps:
            _ln = f"   · [{s.get('stamp','')}] {s.get('title','')}"
            if s.get("why"):
                _ln += f" — {s['why']}"
            if s.get("link"):
                _ln += f" ([기사]({s['link']}))" if not _en else f" ([article]({s['link']}))"
            L.append(_ln)
    else:
        L.append("📰 그날 우리 뉴스 수집에는 이 종목의 특별한 재료가 없었습니다." if not _en
                 else "📰 Our news collection has no stock-specific catalyst for that day.")
    L += ["", ("_위 수치와 뉴스가 저희가 실제로 아는 전부입니다 — 확인되지 않은 원인은 지어내지 않습니다._"
               if not _en else
               "_The figures and rulings above are everything we actually know — we don't "
               "invent unverified causes._")]
    return "\n".join(L), _tr


# ===== MARKET DIRECTION — checklist #11 asked as a question ("오늘 코스피/코스닥
# 방향은?", "What is today's KOSPI/KOSDAQ direction?") plus VIX/나스닥/유가/환율 asks.
# Deterministic, from the same live indicators decide() uses — the LLM used to pick a
# Yahoo summary tool with no KR indices and answer "no data" (boss 2026-08-24). =====

_MKT_DIR_KW = ("코스피", "코스닥", "kospi", "kosdaq", "시장 방향", "시장방향", "오늘 시장",
               "오늘 증시", "오늘 장", "market direction", "index direction",
               "how is the market", "market today", "지수 방향", "오늘 지수",
               "vix", "공포지수", "나스닥", "nasdaq", "유가", "환율", "원달러", "원/달러",
               "exchange rate", "usd/krw", "usdkrw", "wti", "oil price")


_MV_CACHE: dict = {}


def _is_movers_q(transcript: Optional[str]) -> bool:
    """'which stock increased/decreased most (yesterday/today)?' — a DATA question that
    the which-stock recommendation keywords kept hijacking (boss 2026-08-25)."""
    tl = (transcript or "").lower()
    if not tl:
        return False
    _dir = any(w in tl for w in ("increas", "decreas", "gainer", "loser", "오른", "내린",
                                 "상승", "하락", "급등", "급락",
                                 # plain EN move verbs ("what stocks ROSE the most today?"
                                 # got recommendations instead — deep audit 2026-08-25)
                                 "rose", "fell", "jumped", "dropped", "gained", "climbed",
                                 "surged", "plunged", "went up", "went down"))
    _pick = any(w in tl for w in ("which", "what stock", "어떤", "무슨", "가장", "제일",
                                  "most", "top", "상위"))
    _stok = "종목" in tl or bool(_re.search(r"\bstocks?\b|\bstokcs?\b", tl)) \
        or "급등" in tl or "급락" in tl
    return _dir and _pick and _stok


def _movers_reply(db, transcript: Optional[str], lang: str) -> Optional[str]:
    """Top gainers/losers among the 40 candidates for TODAY or YESTERDAY, from real
    Naver daily rows (cached 10 min per stock)."""
    import time as _t
    tl = (transcript or "").lower()
    en = str(lang or "").lower().startswith("en") or (
        not _re.search(r"[가-힣]", transcript or "") and _re.search(r"[a-zA-Z]", transcript or ""))
    want_today = any(w in tl for w in ("오늘", "today", "지금", "now")) \
        and not any(w in tl for w in ("어제", "yesterday"))
    from services import naver_stock as ns
    from services.checklist_reco import _ranking
    rows = (_ranking() or {}).get("rows", [])[:40]
    if not rows:
        return None
    from datetime import datetime as _dtm
    _today_iso = _dt_now_kst().date().isoformat()
    moves = []
    ref_date = None
    for r in rows:
        code = r["code"]
        hit = _MV_CACHE.get(code)
        if hit and _t.time() - hit[0] < 600:
            h = hit[1]
        else:
            try:
                h = ns.daily_history(code, days=4)
            except Exception:
                h = []
            _MV_CACHE[code] = (_t.time(), h)
        if len(h) < 3:
            continue
        # h[0] = the latest row (today's ongoing session when trading)
        if want_today:
            cur, prev = h[0], h[1]
        else:  # yesterday = the last COMPLETED session before today
            if h[0].get("date") == _today_iso:
                cur, prev = h[1], h[2]
            else:
                cur, prev = h[0], h[1]
        if not (cur.get("close") and prev.get("close")):
            continue
        chg = (cur["close"] / prev["close"] - 1) * 100
        ref_date = ref_date or cur.get("date")
        moves.append((r.get("name") or code, code, chg, cur.get("close")))
    if not moves:
        return None
    moves.sort(key=lambda x: -x[2])
    ups = [m for m in moves if m[2] > 0][:4]
    downs = sorted([m for m in moves if m[2] < 0], key=lambda x: x[2])[:4]
    day_lab = (("today" if want_today else "yesterday") + f" ({ref_date})") if en else \
              (("오늘" if want_today else "어제") + f" ({ref_date})")
    L = [f"**{'📊 Top movers' if en else '📊 등락 상위'} — {day_lab} · "
         + (f"{len(moves)} tracked candidates**" if en else f"추적 {len(moves)}종목 기준**"), ""]
    L.append("**📈 " + ("Biggest gainers" if en else "상승 상위") + "**")
    L += [f"{i}. [{n}](chart:{c}) — **{g:+.1f}%** ({_won_str(px)})"
          for i, (n, c, g, px) in enumerate(ups, 1)] or ["-"]
    L += ["", "**📉 " + ("Biggest losers" if en else "하락 상위") + "**"]
    L += [f"{i}. [{n}](chart:{c}) — **{g:+.1f}%** ({_won_str(px)})"
          for i, (n, c, g, px) in enumerate(downs, 1)] or ["-"]
    L += ["", ("Universe = our 40 scored candidates, not the whole market. Click a name for its chart."
               if en else "범위는 전체 시장이 아니라 우리가 채점하는 40종목입니다. 이름 클릭 = 차트.")]
    ah = _afterhours_note(en)
    if ah:
        L.insert(1, ah)
    return "\n".join(L)


def _wants_candidates(transcript: Optional[str]) -> bool:
    """A 'give me N stock candidates for trading' ask, TYPO-TOLERANT (boss 2026-08-24:
    'give em 3 sotck condidate for trading' slipped past every deterministic route and
    the LLM answered with the ML predictions tool — recommendations must ALWAYS come
    from the checklist engine)."""
    tl = (transcript or "").lower()
    if not tl:
        return False
    import difflib as _dl
    toks = [x for x in _re.findall(r"[a-z]+", tl) if len(x) >= 3]

    def has(word: str, cut: float = 0.8) -> bool:
        return any(_dl.get_close_matches(x, (word,), n=1, cutoff=cut) for x in toks)

    stockish = "종목" in tl or "후보" in tl or has("stock") or has("stocks") \
        or has("candidate", 0.75) or has("picks")
    tradeish = "매매" in tl or "단타" in tl or "살" in tl or "추천" in tl \
        or has("trading") or has("trade") or has("buy") or has("invest") \
        or has("recommend", 0.75)
    n = bool(_re.search(r"(?<!\d)(\d{1,2})(?!\d)", tl))
    return stockish and tradeish and (n or has("candidate", 0.75) or "추천" in tl or "후보" in tl)


def _afterhours_note(en: bool) -> Optional[str]:
    """After-hours banner (boss 2026-08-24): any price/market/recommendation answer
    outside KRX 09:00–15:30 must say so."""
    try:
        from services.kiwoom_tape import market_open
        if market_open():
            return None
    except Exception:
        return None
    return ("🌙 The market is CLOSED right now (KRX 09:00–15:30 KST) — figures reflect the last session."
            if en else
            "🌙 지금은 장외 시간입니다 (KRX 정규장 09:00~15:30) — 아래 수치는 마지막 거래 기준입니다.")


def _market_direction_reply(db, transcript: Optional[str], lang: str) -> Optional[str]:
    try:
        from services.decision_agent import _market_indicators
        mi = _market_indicators() or {}
    except Exception:
        mi = {}
    if not mi.get("kospi") and not mi.get("kosdaq"):
        return None
    en = str(lang or "").lower().startswith("en")
    if not en and not _re.search(r"[가-힣]", transcript or "") \
            and _re.search(r"[a-zA-Z]", transcript or ""):
        en = True

    def _word(p):
        if p is None:
            return "-"
        if p <= -2.5:
            return "plunge 📉" if en else "급락 📉"
        if p <= -0.3:
            return "down" if en else "하락"
        if p >= 0.3:
            return "up 📈" if en else "상승 📈"
        return "flat" if en else "보합"

    now = _dt_now_kst().strftime("%Y-%m-%d %H:%M")
    _ah = _afterhours_note(en)
    L = [f"**📊 {'Today' + chr(39) + 's market' if en else '오늘의 시장'} — {now} KST**"]
    if _ah:
        L.append(_ah)
    L += ["",
         ("| Indicator | Level | Change | Read |" if en else "| 지표 | 값 | 등락 | 판단 |"),
         "|---|---|---|---|"]
    rows = (("kospi", "KOSPI" if en else "코스피"),
            ("kosdaq", "KOSDAQ" if en else "코스닥"),
            ("usdkrw", "USD/KRW" if en else "원/달러 환율"),
            ("nasdaq", "NASDAQ (prev close)" if en else "나스닥(전일)"),
            ("vix", "VIX (fear index)" if en else "VIX 공포지수"),
            ("wti", "WTI oil" if en else "WTI 유가"))
    for key, label in rows:
        v = mi.get(key)
        if not v:
            continue
        pct = v.get("pct")
        read = _word(pct) if key in ("kospi", "kosdaq", "nasdaq") else ""
        if key == "vix" and v.get("price") is not None:
            try:
                _vx = float(str(v["price"]).replace(",", ""))
                read = ("calm" if _vx < 20 else "wary" if _vx < 28 else "fear") if en else \
                       ("안정권" if _vx < 20 else "경계권" if _vx < 28 else "공포권")
            except Exception:
                pass
        L.append(f"| {label} | {v.get('price', '-')} | {pct:+.2f}% | {read} |"
                 if pct is not None else f"| {label} | {v.get('price', '-')} | - | {read} |")
    # one-line verdict from the checklist's own market pre-flight
    verdict = ""
    try:
        from services.checklist_engine import market_preflight
        m = market_preflight(db)
        if m.get("deal_breakers"):
            _det = "; ".join(f"#{b['no']} {b['detail']}" for b in m["deal_breakers"][:2])
            verdict = (f"**One line:** market check {m['score']}/{m['max']} · 🚫 {_det} — "
                       f"better to skip NEW buying today." if en else
                       f"**한 줄 판단:** 시장 체크 {m['score']}/{m['max']}점 · 🚫 {_det} — "
                       f"오늘 신규 매수는 쉬는 게 좋습니다.")
        else:
            verdict = (f"**One line:** market check {m['score']}/{m['max']} · no deal-breakers — "
                       f"conditions are OK for trading." if en else
                       f"**한 줄 판단:** 시장 체크 {m['score']}/{m['max']}점 · 결격 없음 — "
                       f"매매 가능한 환경입니다.")
    except Exception:
        pass
    if verdict:
        L += ["", verdict]
    L += ["", ("Details: ask \"checklist\" for the full market check, \"recommend N stocks\" for today's picks."
               if en else
               "자세히: \"체크리스트\"로 시장 점검 전체, \"종목 N개 추천\"으로 오늘의 추천을 볼 수 있습니다.")]
    return "\n".join(L)


def _vip_stock_data_reply(transcript: Optional[str], lang: str, db=None) -> Optional[str]:
    """Unified VIP stock-data answer (the single source the AI Advisor relays):
    공매도 (Kiwoom ka10014) → history table (past/range) → live current price (with
    volume / requested fields). None when no stock/data resolves. `db` lets the live-price
    path read the PC collector's Kiwoom snapshot → '키움 실시간' source during market (else
    the AI Advisor relay shows Naver)."""
    if _is_short_selling_q(transcript):
        ss = _vip_short_selling_reply(transcript, lang)
        if ss and ss.get("reply"):
            return ss["reply"]
    ps, _ps_tr = _period_stats_reply(transcript, lang, db=db)
    if ps:
        return ps
    h = _requested_history_dates(transcript)
    if h:
        r, _tr_ig = _vip_history_reply(transcript, lang, h, db=db)
        if r:
            return r
    cur = _vip_live_price_reply(transcript, lang, db)
    if cur and cur.get("reply"):
        return cur["reply"]
    return None


# ===== 공매도 (short-selling) — VIP holds the Kiwoom key, so it answers locally
# (Kiwoom ka10014) AND exposes /chat/shortselling/live for the Stock app to relay,
# mirroring the price architecture (one key, identical answers). =====
_SHORT_KW = ("공매도", "short selling", "short-selling", "short sale", "공매도량", "공매도비중")


def _is_short_selling_q(transcript: Optional[str]) -> bool:
    return bool(transcript) and any(k in transcript.lower() for k in _SHORT_KW)


def _fmt_short_date(d) -> str:
    """Normalize a Kiwoom date ('20260617' or '2026-06-17') to 'YYYY-MM-DD'."""
    s = str(d or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def _short_selling_for_code(code: str, name: Optional[str]) -> Optional[dict]:
    """One stock's latest short-selling figures via Kiwoom ka10014. None on fail."""
    try:
        from services import kiwoom_rest
        k, s = kiwoom_rest._creds()
        if not (k and s):
            return None
        d = kiwoom_rest.short_selling(code)
        if d and d.get("short_volume") is not None:
            disp = _re.sub(r"[A-Za-z]+", lambda m: m.group(0).upper(), name or code)
            return {"code": code, "name": disp, "short_volume": d.get("short_volume"),
                    "short_ratio": d.get("short_ratio"), "short_value": d.get("short_value"),
                    "date": d.get("date")}
    except Exception as e:
        log.warning(f"vip short_selling {code} failed: {str(e)[:120]}")
    return None


def _vip_short_selling_reply(transcript: Optional[str], lang: str) -> Optional[dict]:
    """공매도 answered locally from Kiwoom, via the shared formatter (identical on
    both surfaces). None → caller falls back to delegation."""
    stocks = _all_stocks_in_query(transcript)
    if not stocks:
        return None
    items = [it for it in (_short_selling_for_code(c, n) for c, n in stocks) if it]
    if not items:
        return None
    from services import price_format
    _en = (lang or "").lower().startswith("en")
    if not _en and not _re.search(r"[가-힣]", transcript or "") \
            and _re.search(r"[a-zA-Z]", transcript or ""):
        _en = True
    date = _fmt_short_date(next((it.get("date") for it in items if it.get("date")), ""))
    reply = price_format.format_short_selling(items, date=date, lang=("en" if _en else "ko"))
    # boss 2026-07-16: one bare line was too short — add normal-length context
    # (deterministic, so VIP and the AI Advisor stay identical): what the ratio
    # means, today's price backdrop, and the data caveat. Target 4-6 lines.
    try:
        it0 = items[0]
        ratio = float(it0.get("short_ratio") or 0)
        px = chg = None
        try:
            from services.paper_desk import _chg_cache, _live_price
            px, _nm = _live_price(it0["code"])
            chg = _chg_cache.get(it0["code"])
        except Exception:
            pass
        if _en:
            lvl = ("on the high side" if ratio >= 3 else
                   "a normal level" if ratio >= 1 else "on the low side")
            extra = [f"For context, {ratio:.2f}% of the day's volume being short sales is {lvl} "
                     f"for a large cap — roughly 1~2% is typical.",
                     (f"The stock currently trades at ₩{px:,.0f}"
                      + (f" ({chg:+.2f}% today)" if chg is not None else "")
                      + ", so short activity should be read against that move." if px else ""),
                     "The figure is Kiwoom's official daily tally — it updates once per "
                     "session, so today's trading isn't included yet."]
        else:
            lvl = ("높은 편" if ratio >= 3 else "보통 수준" if ratio >= 1 else "낮은 편")
            extra = [f"참고로 거래량 대비 {ratio:.2f}%의 공매도 비중은 대형주 기준 {lvl}입니다 "
                     f"(통상 1~2% 수준).",
                     (f"현재 주가는 ₩{px:,.0f}"
                      + (f" ({chg:+.2f}%)" if chg is not None else "")
                      + "로, 공매도 수치는 이 흐름과 함께 읽는 것이 정확합니다." if px else ""),
                     "이 수치는 키움 공식 일별 집계라 하루 한 번 갱신됩니다 — 오늘 장중 물량은 아직 반영 전입니다."]
        reply = reply + "\n\n" + "\n".join(x for x in extra if x)
    except Exception:
        pass
    return {"intent": "short_selling", "language": lang, "reply": reply,
            "action": None, "speak": True, "transcript": transcript,
            "tool_used": "short_selling", "tool_result": {"items": items}}


# Clear stock-domain keywords (besides a specific stock name).
_STOCK_Q_KW = (
    "주가", "종가", "현재가", "시세", "코스피", "코스닥", "kospi", "kosdaq", "증시",
    "종목", "주식", "stock", "shares", "수급", "순매수", "공매도", "배당", "dividend",
    "etf", "목표주가", "상한가", "하한가", "나스닥", "nasdaq", "s&p", "실적", "per ", "pbr",
    "선물", "futures", "옵션", "파생",
)


# ===== NAVER search — every agent can check Naver (web + 네이버 부동산 매물). Answered
# DETERMINISTICALLY so the LLM can't mis-delegate it to another agent. =====
_NAVER_KW = ("네이버", "naver")
_NAVER_INTENT_KW = ("검색", "찾아", "올라", "매물", "부동산", "광고", "advertis", "listed",
                    "real estate", "search", "조회", "등록")
_NAVER_RE_KW = ("부동산", "매물", "땅", "집", "건물", "아파트", "상가", "토지", "오피스텔",
                "property", "real estate", "land", "house", "building", "apartment")


def _is_naver_search_q(transcript: Optional[str]) -> bool:
    t = (transcript or "").lower()
    if not any(k in t for k in _NAVER_KW):
        return False
    # A search/listing intent OR a property word (property + naver = a Naver listing check).
    if not (any(k in t for k in _NAVER_INTENT_KW) or any(k in t for k in _NAVER_RE_KW)):
        return False
    # Don't hijack 'NAVER 주가/현재가' (the STOCK) — unless real-estate words are present.
    price_only = (any(w in t for w in ("주가", "현재가", "시세", "stock price", "주식"))
                  and not any(w in t for w in _NAVER_RE_KW))
    return not price_only


def _naver_subject(transcript: Optional[str]) -> str:
    """Reduce the question to just the property/address subject (e.g. '낙하리') so the
    Naver search isn't polluted by filler like '부동산 매물이 올라와 있는지 확인해줘'."""
    t = transcript or ""
    # Strip search/listing filler + generic real-estate words (부동산/매물 — the search
    # itself scopes to Naver 부동산, so keep only the property NAME/ADDRESS + type).
    t = _re.sub(r"(네이버에서|네이버|naver|부동산에서|부동산에|부동산|매물이|매물로|매물|광고|"
                r"검색해줘|검색해|검색|조회해줘|조회|확인해줘|확인|알려줘|알려|해줘|해주세요|"
                r"시세는|시세|매매가|가격은|가격|얼마예요|얼마야|얼마|현재가|어때\??|"
                r"정보를|정보|관련된|관련|어떤지|어디야|어디|뭐야|보여줘|보여|좀|"
                r"더\s*보여줘|더\s*보여|더\s*보기|더\s*줘|더\s*알려|다른\s*매물|더|"
                r"올라와\s*있는지|올라와|올라온|등록\s*되어|등록|있는지요|있는지|있나요|있나|있어요|있어|"
                r"좀|찾아봐줘|찾아봐|찾아|please|search|for|on|in|is|are|our|whether|listed|price|"
                r"advertis\w*|우리|저희|the|check|real\s*estate)", " ", t, flags=_re.I)
    t = _re.sub(r"(?<=\s)(에|에서|이|가|을|를|은|는|도|의|로)(?=\s|$)", " ", t)
    t = _re.sub(r"[?!.,]+", " ", t)
    t = _re.sub(r"\s+", " ", t).strip()
    # Dedupe repeated tokens (a 'more' follow-up reuses the prior query → '낙하리 낙하리').
    _seen: set = set()
    return " ".join(w for w in t.split() if not (w in _seen or _seen.add(w)))


def _is_deep_naver_url(u: str) -> bool:
    """A "deep" Naver 부동산 link (specific article/complex/search) is real proof a
    property is advertised. A bare land.naver.com homepage is NOT — Serper just
    matched the site."""
    u = (u or "").lower()
    if "land.naver.com" not in u:
        return False
    tail = u.split("land.naver.com", 1)[1].lstrip("/")
    return len(tail) > 1 and any(k in u for k in (
        "articleno", "outlinkbridge", "/complexes", "/offices", "/article", "/search"))


# Generic property-TYPE words — stripped when picking the address key to look up in
# our asset file, so '낙하리 땅' resolves on the area token '낙하리'.
_RE_TYPE_WORDS = {
    "땅", "집", "건물", "매물", "부동산", "아파트", "상가", "토지", "오피스텔", "빌라",
    "주택", "임야", "도로", "공장", "창고", "property", "land", "house", "building",
    "apartment", "officetel",
}


def _our_property_addresses(db, subject: str) -> list[str]:
    """Resolve `subject` against OUR uploaded asset file and return the distinct real
    addresses / property names (e.g. '낙하리 301-7', '의정부역 한양수자인파크뷰 B1호').
    Searches BOTH asset_units (address/property) and asset_portfolio (description, where
    portfolio rows like 의정부 live). Empty list if we own nothing matching — then the
    caller treats it as a generic Naver search."""
    if db is None or not (subject or "").strip():
        return []
    from sqlalchemy import text as _text
    toks = [w for w in _re.split(r"\s+", subject.strip())
            if w and w.lower() not in _RE_TYPE_WORDS]
    if not toks:
        toks = [w for w in _re.split(r"\s+", subject.strip()) if w]
    if not toks:
        return []
    params = {f"t{i}": f"%{t}%" for i, t in enumerate(toks)}
    out: list[str] = []
    try:
        u_blob = "(coalesce(address,'')||' '||coalesce(property,''))"
        u_where = " AND ".join(f"{u_blob} ILIKE :t{i}" for i in range(len(toks)))
        rows = db.execute(_text(
            f"SELECT DISTINCT coalesce(nullif(trim(address),''), property) AS a "
            f"FROM asset_units WHERE {u_where} "
            f"AND coalesce(nullif(trim(address),''), property) IS NOT NULL LIMIT 8"), params)
        for r in rows:
            a = (r._mapping.get("a") or "").strip()
            if a and a not in out:
                out.append(a)
        # Portfolio rows (의정부 등): the property name lives in `description`.
        p_where = " AND ".join(f"coalesce(description,'') ILIKE :t{i}" for i in range(len(toks)))
        prows = db.execute(_text(
            f"SELECT DISTINCT trim(description) AS a FROM asset_portfolio "
            f"WHERE {p_where} AND coalesce(trim(description),'') <> '' LIMIT 8"), params)
        for r in prows:
            a = _clean_portfolio_name(r._mapping.get("a") or "")
            if a and a not in out:
                out.append(a)
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return []
    # Drop a bare area name when a MORE specific entry extends it (e.g. drop '낙하리'
    # when '낙하리 301-7' exists) — but keep named complexes like '의정부역 한양수자인
    # 파크뷰' that have no lot number. A Naver check on just '낙하리' is too broad.
    pruned = [a for a in out if not any(b != a and b.startswith(a) for b in out)]
    return pruned or out


def _clean_portfolio_name(desc: str) -> str:
    """Reduce a portfolio description to a Naver-searchable property name:
    '의정부역 한양수자인파크뷰 5개 호실' → '의정부역 한양수자인파크뷰'. Strips trailing
    count/unit phrases and parenthetical area notes."""
    d = (desc or "").strip()
    d = _re.sub(r"\(.*?\)", " ", d)                       # drop '(45.37평)'
    d = _re.sub(r"\s*\d+\s*개\s*호실.*$", "", d)           # '5개 호실'
    d = _re.sub(r"\s+(B?\d+호|[가-힣]?\d+호)\s*$", "", d)  # trailing 'B1호'
    return _re.sub(r"\s+", " ", d).strip()


def _addr_on_naver(addr: str, result: dict) -> bool:
    """True only if a Naver result genuinely corresponds to `addr` — its title /
    snippet / decoded URL must mention the property's area token (e.g. '상신리',
    '낙하리') OR its lot number. Prevents false 'listed' verdicts from generic
    land.naver.com/article links Serper matched (e.g. 반포센트럴자이 for 상신리)."""
    import urllib.parse as _up
    parts = (addr or "").split()
    area = parts[0] if parts else (addr or "")
    nums = _re.findall(r"\d+(?:-\d+)?", addr or "")
    hay = " ".join([
        (result.get("title") or ""), (result.get("snippet") or ""),
        _up.unquote(result.get("url") or ""),
    ])
    if area and area in hay:
        return True
    # A distinctive complex name token (≥4 chars, e.g. '한양수자인파크뷰') is strong proof.
    if any(len(t) >= 4 and t in hay for t in parts):
        return True
    # A lot number WITH a dash ('301-7') is distinctive; bare short numbers aren't.
    return any("-" in n and n in hay for n in nums)


def _naver_provider_authoritative(provider: Optional[str]) -> bool:
    """True only for a provider that can actually confirm/deny a 부동산 LISTING —
    i.e. Serper scoped to land.naver.com. The official Naver Open API
    (provider 'naver_api:*') searches web/news/blog, NOT 부동산 listings, and the
    Gemini-grounded fallback ('naver(web:...)') returns opaque redirects — so a
    "not listed" claim from either would be a lie. Only serper:naver lets us say
    a property is genuinely not on Naver 부동산."""
    return (provider or "").startswith("serper:naver")


# Words that signal a result really is a property listing/ad (not just a mention).
_LISTING_KW = ("매물", "임대", "매매", "전세", "월세", "보증금", "분양", "평", "㎡",
               "공장", "창고", "사옥", "상가", "오피스텔", "아파트", "토지", "부동산", "중개")
# Strong listing signals (an actual ad has a price / size) — used to rank results.
_STRONG_LISTING_KW = ("보증금", "월세", "전세", "매매가", "분양가", "평", "㎡", "억", "만원", "임대")
# Our own surfaces — never cite these back as a "Naver listing".
_OWN_NAVER_EXCLUDE = ("assetagent.vercel.app", "oasisvip", "vip-orchestrator", "onrender.com")
# Non-listing sources the user doesn't want (video / social) — skip entirely.
_SKIP_DOMAINS = ("youtube.com", "youtu.be", "instagram.com", "facebook.com",
                 "tiktok.com", "twitter.com", "x.com", "pinterest.")
# News/article domains — about a property, not a listing of it. Filtered out.
_NEWS_DOMAINS = ("hankyung.com", "joongang.co.kr", "chosun.com", "mk.co.kr", "donga.com",
                 "hani.co.kr", "khan.co.kr", "mt.co.kr", "edaily.co.kr", "sedaily.com",
                 "newsis.com", "yna.co.kr", "ytn.co.kr", "kbs.co.kr", "sbs.co.kr",
                 "news.", "/article/", "mbn.co.kr", "asiae.co.kr", "fnnews.com")


def _looks_like_listing(r: dict) -> bool:
    hay = ((r.get("title") or "") + " " + (r.get("snippet") or "")).lower()
    return any(k in hay for k in _LISTING_KW)


def _listing_score(r: dict) -> int:
    """Higher = more likely a real ad (has price/size signals)."""
    hay = ((r.get("title") or "") + " " + (r.get("snippet") or "")).lower()
    return sum(1 for k in _STRONG_LISTING_KW if k in hay)


def _own_naver_domain(url: str) -> bool:
    u = (url or "").lower()
    return any(d in u for d in _OWN_NAVER_EXCLUDE)


def _is_news_url(url: str) -> bool:
    u = (url or "").lower()
    return any(d in u for d in _NEWS_DOMAINS)


def _is_skip_url(url: str) -> bool:
    u = (url or "").lower()
    return any(d in u for d in _SKIP_DOMAINS)


def _is_naver_domain(url: str) -> bool:
    return "naver.com" in (url or "").lower()


# "Show more" follow-up phrases after a Naver listing answer.
_NAVER_MORE_KW = ("더 보", "더보", "더 줘", "더줘", "더 알려", "다른 매물", "다른거", "다른 거",
                  "전부", "모두", "목록", "여러", "리스트", "more", "show more", "list them", "show all")


def _naver_more_followup(transcript: Optional[str], history: Optional[list[dict]]) -> Optional[str]:
    """If the user says 'more' right after a Naver listing answer, return the PREVIOUS
    Naver/property query so we can re-run the search (with more results). None otherwise.
    Lets '우리 낙하리 매물 더 보여줘' (no '네이버') still reach the Naver path."""
    t = (transcript or "").lower().strip()
    if not any(k in t for k in _NAVER_MORE_KW):
        return None
    for h in reversed(history or []):
        if (h.get("role") or h.get("who") or "") == "user":
            prev = (h.get("text") or h.get("content") or "")
            pl = prev.lower()
            if prev and (_is_naver_search_q(prev)
                         or any(k in pl for k in _NAVER_RE_KW)
                         or any(k in pl for k in ("네이버", "naver", "올라", "매물"))):
                return prev
            break  # only the immediately-preceding user turn counts
    return None


def _vip_naver_search_reply(transcript: Optional[str], lang: str, db=None) -> Optional[dict]:
    """Answer 'is <property> on Naver?' / 'search Naver for <X>'.

    Key principle (anti-hallucination): "네이버에 올라와 있어?" means FINDABLE VIA NAVER
    SEARCH — web + 부동산 — not only land.naver.com. Many of our properties are
    advertised on brokerage sites (e.g. 창고연구소.com) that surface in Naver search.
    So we search broadly, surface any real listing we find (with its link), and NEVER
    claim a property "is not listed" (search can't prove absence) — instead we always
    hand back a clickable Naver search link the user can verify in one tap.
    """
    import urllib.parse as _up
    from services.naver_search import naver_search
    tl = (transcript or "").lower()
    re_estate = any(k in tl for k in _NAVER_RE_KW)
    subject = _naver_subject(transcript) or (transcript or "")
    _en = (lang or "").lower().startswith("en")

    def _fmt(rs):
        out = []
        for r in rs:
            title = (r.get("title") or "매물").strip()
            if len(title) > 48:
                title = title[:48].rstrip() + "…"
            url = (r.get("url") or "").strip()
            snip = (r.get("snippet") or "").strip()[:110]
            # Clickable markdown link — NOT wrapped in ** (bold swallows the link).
            head = f"• [{title}]({url})" if url else f"• {title}"
            out.append(head + (f"\n  {snip}" if snip else ""))
        return out

    # Resolve against OUR uploaded properties. This also decides intent: '우리 낙하리
    # 네이버에 올라와 있어?' has NO real-estate keyword, but if 낙하리 is one of our
    # assets it's clearly a property-listing question — route it there, not to a
    # generic web search that dumps 맛집/위키 results.
    our_addrs = _our_property_addresses(db, subject)
    is_property = re_estate or bool(our_addrs)

    # ===== General (non-property) Naver search → just show results =====
    if not is_property:
        res = naver_search(subject, realestate=False, num_results=6)
        results = [r for r in (res.get("results") or [])
                   if (res.get("provider") or "").startswith(("naver_api", "serper"))
                   and (r.get("title") or r.get("url"))]
        if results:
            head = (f"네이버 검색 결과 — '{subject}':" if not _en else f"NAVER results for '{subject}':")
            reply = head + "\n\n" + "\n".join(_fmt(results[:6]))
        else:
            web = "https://search.naver.com/search.naver?query=" + _up.quote(subject)
            reply = (f"네이버에서 '{subject}' 검색 결과를 직접 확인해 보세요:\n\n🔎 {web}" if not _en
                     else f"Check NAVER search for '{subject}':\n\n🔎 {web}")
        return {"intent": "naver_search", "language": lang, "reply": reply[:1900],
                "action": None, "speak": True, "transcript": transcript,
                "tool_used": "naver_search", "tool_result": res}

    # ===== Real-estate: find listings across Naver (web + 부동산), never claim absence =====
    q = subject if "매물" in subject else f"{subject} 매물"
    # General web search (Naver Open API is free) finds brokerage-site listings that
    # land.naver.com-only scoping would miss.
    res = naver_search(q, realestate=False, num_results=8)
    prov = (res.get("provider") or "")
    web_url = "https://search.naver.com/search.naver?query=" + _up.quote(q)

    listings, seen = [], set()
    for r in (res.get("results") or []):
        url = (r.get("url") or "").strip()
        if not (r.get("title") or url):
            continue
        if not prov.startswith(("naver_api", "serper")):
            continue
        # Drop our own surfaces, news articles, and video/social (YouTube etc.).
        if _own_naver_domain(url) or _is_news_url(url) or _is_skip_url(url) or url in seen:
            continue
        if _is_deep_naver_url(url) or _looks_like_listing(r):
            seen.add(url)
            listings.append(r)
    # Pick the single BEST listing. RELEVANCE first: the result must actually be about
    # this property — area mentions in the TITLE weigh most (a '갈현리' post that just
    # keyword-stuffs '낙하리' in its body must NOT win over a real '낙하리' listing).
    # Then deep land.naver.com listing, then ad-strength.
    area = (subject.split() or [subject])[0]

    def _relevance(r: dict) -> int:
        title = r.get("title") or ""
        snippet = r.get("snippet") or ""
        return title.count(area) * 3 + min(snippet.count(area), 2)

    listings.sort(key=lambda r: (_relevance(r),
                                 _is_deep_naver_url(r.get("url") or ""),
                                 _listing_score(r)), reverse=True)

    # Default: ONE best ad link. If the user asks for more ('더 보여줘'), show several.
    want_more = any(w in tl for w in (
        "더 보", "더보", "더 알려", "더 줘", "더줘", "다른 매물", "다른거", "다른 거",
        "전부", "모두", "목록", "여러", "리스트", "more", "other listing", "list them", "show all"))
    n_show = 5 if want_more else 1
    owns_note = ("\n\n(보유 자산: " + ", ".join(our_addrs[:5]) + ")") if our_addrs else ""
    if listings:
        head = (f"네이버에서 '{subject}' 매물입니다:" if not _en else
                f"'{subject}' listing on NAVER:")
        body = "\n".join(_fmt(listings[:n_show]))
        if want_more:
            extra = (f"\n\n🔎 네이버에서 더 보기: {web_url}" if not _en
                     else f"\n\n🔎 More on NAVER: {web_url}")
        elif len(listings) > 1:
            extra = ("\n\n더 보시려면 \"더 보여줘\"라고 말씀해 주세요." if not _en
                     else "\n\nSay \"show more\" to see more listings.")
        else:
            extra = ""
        reply = head + "\n\n" + body + extra + owns_note
    else:
        # Never claim "not listed" — hand back the single clickable Naver search link.
        reply = ((f"'{subject}' 매물을 네이버에서 직접 확인해 보세요:\n\n🔎 {web_url}" + owns_note)
                 if not _en else
                 (f"Check NAVER search for '{subject}' listings:\n\n🔎 {web_url}" + owns_note))
    return {"intent": "naver_search", "language": lang, "reply": reply[:1900],
            "action": None, "speak": True, "transcript": transcript,
            "tool_used": "naver_search",
            "tool_result": {"ok": True, "our_assets": our_addrs, "provider": prov,
                            "found": len(listings), "query": q,
                            "results": (res.get("results") or [])[:8]}}


# US stocks VIP can't price locally (no Kiwoom/Naver KR data) — detect them so the
# question DELEGATES to the Stock backend (which handles US tickers) instead of
# falling into a KR-only tool that errors ("couldn't resolve 'Apple'").
_US_STOCK_NAMES = (
    "apple", "aapl", "tesla", "tsla", "nvidia", "nvda", "microsoft", "msft",
    "alphabet", "google", "googl", "amazon", "amzn", "meta", "facebook", "netflix",
    "nflx", "palantir", "pltr", "broadcom", "avgo", "amd", "intel", "intc",
    "coinbase", "coin", "costco", "cost", "walmart", "wmt", "disney", "nike", "nke",
    "boeing", "starbucks", "sbux", "qualcomm", "qcom", "micron", "mu", "berkshire",
    "s&p 500", "sp500", "nasdaq", "dow jones",
)


def _is_us_stock_query(transcript: Optional[str]) -> bool:
    t = (transcript or "").lower()
    return any(_re.search(rf"(?<![a-z]){_re.escape(n)}(?![a-z])", t) for n in _US_STOCK_NAMES)


def _is_stock_question(transcript: Optional[str]) -> bool:
    """True when the message is clearly about stocks — a resolvable stock name/code,
    a US ticker/name, OR a stock-domain keyword. Used to make VIP delegate EVERY stock
    question to the Stock agent so the two agents always give the same answer."""
    t = (transcript or "").strip().lower()
    if not t:
        return False
    if _stock_in_query(transcript) is not None or _is_us_stock_query(transcript):
        return True
    # Real-estate guard: a clearly-property question ('향남 아파트 시세', '제주 토지
    # 매물') shares price words like 시세/가격/얼마 with stocks. When it carries a
    # real-estate keyword but resolves to NO specific stock, it is NOT a stock
    # question — don't let stock delegation hijack it from naver_search/onbid_search.
    if any(k in t for k in _REALESTATE_Q_KW):
        return False
    return any(k in t for k in _STOCK_Q_KW)


# Real-estate domain keywords that disqualify a "시세/가격/얼마" query from being
# treated as a stock question (when no specific stock name is present).
_REALESTATE_Q_KW = (
    "부동산", "매물", "아파트", "토지", "땅", "상가", "오피스텔", "빌라", "주택",
    "임야", "전세", "월세", "분양", "재건축", "재개발", "평당",
    "real estate", "property", "apartment", "officetel", "land plot",
)


# Follow-up phrasings that, after a stock turn, still concern that stock
# (no explicit name) — 'should I buy it', 'predict today', '얼마나 오를까'.
_STOCK_FOLLOWUP_KW = (
    "사야", "팔아야", "살까", "팔까", "사도", "매수", "매도", "보유", "사면", "팔면",
    "오를", "내릴", "오를까", "떨어질", "예측", "전망", "목표가", "얼마나",
    "buy", "sell", "hold", "predict", "forecast", "go up", "go down", "rise", "fall",
    "target", "upside", "downside", "should i", "how much will",
)


def _recent_stock_context(history: Optional[list[dict]]) -> bool:
    """True if the recent conversation was about a specific stock — so a bare
    follow-up ('should I buy?', 'predict today') belongs to the Stock agent."""
    if not history:
        return False
    for h in list(history)[-4:]:
        body = (h.get("content") or h.get("text") or "")
        if _stock_in_query(body) is not None or any(k in body.lower() for k in _STOCK_Q_KW):
            return True
    return False


_PRICE_ONLY_KW = ("현재가", "주가", "시세", "얼마", "가격", "price", "quote", "how much", "cost")


def _is_price_question(transcript: Optional[str]) -> bool:
    """True for a PURE current-price question on a specific stock — 'X 현재가/얼마/
    주가/시세'. Excludes advice and past-date questions so only the bare-price ask
    matches. Answered deterministically (no LLM) so it can never garble/leak."""
    t = (transcript or "").strip().lower()
    if not t or not any(k in t for k in _PRICE_ONLY_KW):
        return False
    if _is_past_price(transcript) or _is_stock_advice(transcript, None):
        return False
    return _stock_in_query(transcript) is not None


def _format_price_reply(res: dict, lang: str) -> Optional[str]:
    """Deterministically format a stock_quote result into a clean one-line reply
    (no LLM → identical on Stock and VIP, never garbled)."""
    if not isinstance(res, dict) or not res.get("ok"):
        return None
    name = res.get("name") or res.get("ticker") or ""
    # Display latin prefixes upper-cased (sk하이닉스 → SK하이닉스, naver → NAVER).
    name = _re.sub(r"[A-Za-z]+", lambda m: m.group(0).upper(), name)
    won = res.get("current_price_won")
    if not won:
        return None
    basis = res.get("basis") or ("현재가" if lang == "ko" else "price")
    extra = []
    if res.get("change"):
        extra.append((f"전일 대비 {res['change']}" if lang == "ko"
                      else f"{res['change']} vs prev close"))
    if res.get("as_of"):
        extra.append((f"기준 {res['as_of']} KST" if lang == "ko"
                      else f"as of {res['as_of']} KST"))
    tail = f" ({', '.join(extra)})" if extra else ""
    if lang == "ko":
        return f"{name} {basis}는 {won}입니다{tail}. (출처: NAVER/키움 실시간 시세)"
    return f"{name} {basis} is {won}{tail}. (source: NAVER/Kiwoom live quote)"


def _history_days_for(transcript: Optional[str]) -> int:
    """How many trading days of history to pull for a past-date question. A specific
    date drives the count (must be enough rows to REACH that date), not a fixed 30."""
    try:
        hist = _requested_history_dates(transcript)
        if hist and hist[0] == "dates" and hist[1]:
            span = (_dt_now_kst().date() - min(hist[1])).days
            return max(5, min(span + 7, 400))
        if hist and hist[0] == "range":
            return max(5, min(int(hist[1]) + 6, 400))
    except Exception:
        pass
    m = _re.search(r"(\d+)\s*일", transcript or "")
    if m:
        try:
            return max(5, min(int(m.group(1)) + 6, 400))
        except Exception:
            pass
    t = (transcript or "").lower()
    if any(k in t for k in ("지난달", "지난 달", "달 전", "개월", "month")):
        return 45
    return 30


def _build_system_prompt(
    current_path: Optional[str] = None,
    selected_id: Optional[str] = None,
    pending_attachments: Optional[list[dict]] = None,
    kb_context: Optional[list[dict]] = None,
    kb_files: Optional[list[dict]] = None,
    agent_id: Optional[str] = None,
    page_context: Optional[str] = None,
) -> str:
    """Compose the system prompt the LLM sees on every request.

    Includes:
      - Role
      - Tool catalog
      - Manifest summary (pages + external agents)
      - Output format
      - Strict rules to prevent intent hallucination
    """
    tool_lines: list[str] = []
    for s in list_tool_schemas(agent_id):
        param_names = list((s.get("parameters") or {}).get("properties", {}).keys())
        param_str = ", ".join(param_names) if param_names else "(no args)"
        tool_lines.append(
            f"- {s['name']}({param_str}) [{s['kind']}]: {s['description']}"
        )
    tools_block = "\n".join(tool_lines)

    context_lines = []
    if current_path:
        context_lines.append(f"[CURRENT PAGE] User is on: {current_path}")
    if selected_id:
        # Hint the LLM what 'this' refers to based on current page
        hint = ""
        if current_path and current_path.startswith("/chatbot"):
            hint = f' (treat as conversation_id when the user says "this conversation" / "this message")'
        elif current_path and current_path.startswith("/reports"):
            hint = f' (treat as report_id when the user says "this report")'
        elif current_path and current_path.startswith("/twins"):
            hint = f' (treat as twin_id when the user says "this twin")'
        elif current_path and current_path.startswith("/meetings"):
            hint = f' (treat as meeting_id when the user says "this meeting")'
        context_lines.append(f"[SELECTED ID] {selected_id}{hint}")
    context_block = "\n" + "\n".join(context_lines) + "\n" if context_lines else ""

    # Pending-attachments block — tells the LLM which attachment_ids it can
    # pass to send_dm/send_email/broadcast/etc. When this is non-empty the
    # user has dropped files into the chat AND used an action verb, so they
    # almost certainly want one of those write tools.
    attach_block = ""
    if pending_attachments:
        lines = ["[ATTACHED FILES] The user just attached these — pass the matching attachment_ids to send_dm / send_email / broadcast etc. if they ask to send/share/forward:"]
        for a in pending_attachments[:8]:
            lines.append(f"  - attachment_id={a.get('attachment_id')} filename={a.get('filename')} kind={a.get('kind')} mime={a.get('mime_type')}")
        attach_block = "\n" + "\n".join(lines) + "\n"

    # RAG-first retrieval block. When the user's question matches anything in
    # the agent's uploaded knowledge base (xlsx/pdf/docx/pptx the boss has
    # ingested), the top hits are injected here. The wording is forceful
    # ('ABSOLUTE PRIORITY', 'DO NOT call any tool') because earlier softer
    # wording let the LLM ignore the excerpts and call agent_status / mock-
    # data tools instead of quoting the actual file content.
    # === Page context block ===
    # What the user is literally seeing on screen right now. The frontend
    # AssistantCard captures innerText of the main page region (Claude
    # extension-style) so the LLM can answer questions like "how much
    # total asset?" / "whose contract expires tomorrow?" by reading the
    # rendered dashboard numbers directly, with NO tool call required.
    # This is the fast path for "ask about what's on this page" questions
    # before the full tool-calling DB bridge ships.
    page_block = ""
    if page_context and page_context.strip():
        # Cap the page snapshot. 14K chars bloated every prompt and slowed
        # responses; 5K still covers the visible numbers/labels the user asks
        # about while keeping the request fast.
        trimmed = page_context.strip()[:5000]
        page_block = (
            "\n■■■ WHAT THE USER IS SEEING ON SCREEN (live page DOM snapshot) ■■■\n"
            "The text below is exactly what's rendered on the user's current "
            "page right now. If their question can be answered from these "
            "numbers / lists / labels, ANSWER DIRECTLY using the answer shape "
            "({\"answer\": \"...\"}). Quote the exact numbers and names verbatim. "
            "Do NOT call search_knowledge_base or any tool when the answer is "
            "already visible here. Do NOT say 'I don't have access to your "
            "data' — you literally see it.\n"
            "─── current page content ───\n"
            f"{trimmed}\n"
            "─── end page content ───\n"
        )

    # === File index block ===
    # Always tell the LLM which files the boss has uploaded for this agent,
    # even when the current question didn't match any chunk. This lets the
    # assistant answer "what files do I have?" / "what do you know about
    # me?" / "내가 올린 파일 알려줘" from awareness alone, and lets it
    # recognize that a vague question is about file X without needing a
    # chunk-level keyword match.
    files_block = ""
    if kb_files:
        flines = [
            "■ UPLOADED KNOWLEDGE FILES (scoped to this agent — the boss can see these in the /chatbot → Add knowledge tab):",
        ]
        for f in kb_files[:30]:
            fn = f.get("filename") or "?"
            ch = f.get("chunk_count") or 0
            # Larger preview (up to 1200 chars) so identity-style files
            # ("about me", "프로필", "introduction") expose name + soccer
            # club + hometown etc. in the first-chunk preview, even when
            # the keyword search misses (e.g. 'what is my name?' won't
            # match the literal token 'name' in the file).
            preview = (f.get("preview") or "").strip().replace("\n", " ")[:1200]
            line = f"  - {fn} ({ch} chunks)"
            if preview:
                line += f"\n      preview: {preview}"
            flines.append(line)
        flines.append(
            "RULES for answering from these files:\n"
            "  • Treat every fact in the preview/excerpts as ESTABLISHED TRUTH about the boss — you already know it.\n"
            "  • Speak in 2nd person: 'You are X', 'Your favorite is Y', 'You live in Z'.\n"
            "  • NEVER use these forbidden phrases (the boss will see them and complain):\n"
            "      - 'I'm not sure of your name'\n"
            "      - 'I see you mentioned …'\n"
            "      - 'as mentioned in …'\n"
            "      - 'according to your file …'\n"
            "      - 'in the about me document'\n"
            "      - 'in your knowledge file'\n"
            "      - 'in your uploaded documents'\n"
            "      - any phrase that names a filename, sheet name, or document name\n"
            "  • If asked 'what is my name?' and a file preview contains a name like 'Davronbek', the ONLY acceptable reply is: 'You are Davronbek.' (optionally followed by a friendly sentence — but NEVER mention the file).\n"
            "  • If you genuinely cannot find the fact in any preview, call search_knowledge_base(query) BEFORE saying 'I don't know'."
        )
        files_block = "\n" + "\n".join(flines) + "\n"

    kb_block = ""
    if kb_context:
        kb_lines = [
            "═══════════════════════════════════════════════════════════════",
            "■■■ KNOWLEDGE BASE — USE AS PRIMARY SOURCE ■■■",
            "═══════════════════════════════════════════════════════════════",
            "The following are VERBATIM EXCERPTS from your knowledge base —",
            "uploaded documents AND market/stock reports. Treat them as",
            "established, CURRENT facts you already know.",
            "When the question can be answered from these excerpts, you MUST:",
            "  1. Answer DIRECTLY from them — do NOT fall back to vague,",
            "     generic knowledge when specifics are right here.",
            "  2. Prefer their SPECIFICS — company names, numbers, dates,",
            "     events, figures — over general statements. If they cover a",
            "     market/sector/stock question, synthesize and summarize those",
            "     specifics into the answer.",
            "  3. For PERSONAL facts about the boss, state them in 1st/2nd",
            "     person ('You are X', 'Your favorite is Y'). For market/report",
            "     content, summarize the concrete findings.",
            "  4. You MAY note the basis generically ('리포트 기준',",
            "     'per our report', '데이터 사전 기준') but NEVER expose internal",
            "     filenames or sheet names.",
            "  5. Quote specific numbers, names, and amounts verbatim.",
            "Only call a tool if the question needs CURRENT LIVE data (today's",
            "price, live status) that is NOT in the excerpts below.",
            "⚠ HARD EXCEPTION — COMPANY ASSETS (자산/부동산/포트폴리오): for ANY asset",
            "question involving a NUMBER, TOTAL, COUNT, 면적/size, 월세/rent, 가치/value,",
            "보증금, 공실/occupancy, or a COMPARISON (biggest/smallest/most/least/가장 큰/",
            "제일 비싼/월세 높은), you MUST call the asset_summary / asset_search /",
            "asset_top tools and answer ONLY from their exact results. The excerpts",
            "below are fuzzy text and are WRONG for asset totals/rankings — NEVER",
            "compute 'biggest / total / which / how many' for assets from the excerpts.",
            "─── excerpts (internal — do NOT mention filenames in your reply) ───",
        ]
        for i, c in enumerate(kb_context[:8], start=1):
            sim = c.get("similarity", 0.0)
            # Deliberately DO NOT include filename or sheet name in the
            # excerpt header — the LLM tended to echo them back into the
            # reply ("as mentioned in about me.docx"). The location alone
            # is enough internal context.
            loc = c.get("location") or f"excerpt {i}"
            kb_lines.append(
                f"[{i}] {loc}  (relevance {sim:.2f})\n"
                f"{c.get('content', '').strip()[:1800]}"
            )
        kb_lines.append("═══════════════════════════════════════════════════════════════")
        kb_block = "\n" + "\n".join(kb_lines) + "\n"

    # Per-agent identity — for Stock / Realty / Asset / AIGlass use THEIR own
    # name + tagline + role so the assistant never claims to be the VIP
    # platform. Falls back to the global VIP identity for vip / unknown agents.
    _prof = AGENT_PROFILES.get((agent_id or "").lower()) if agent_id else None
    if _prof and (agent_id or "").lower() != "vip":
        identity = {
            "name": f"{_prof['name']} assistant",
            "tagline": _prof["tagline"],
            "scope": (
                f"You serve {_prof['user_role']} You know every page and data domain of "
                f"THIS app (listed below), you remember the conversation, you can fetch live "
                f"data and analyze it, and you can do anything the user would do by hand here — "
                f"navigate/open pages, summarize the current screen, and run actions (with "
                f"confirmation). You are NOT the VIP platform; do not mention VIP pages."
            ),
        }
    else:
        identity = get_agent_identity()
    return (
        f"You are the {identity['name']} — {identity['tagline']}. "
        f"{identity['scope']}\n\n"
        "Reply in the SAME language the user wrote in (Korean ↔ English).\n"
        "When replying in Korean, write PURE Korean — Hangul only. Never emit Chinese "
        "characters (材料, 综合, 某 …); use the Korean word instead (재료, 종합, 어떤).\n"
        "Be warm and conversational, like a smart human consultant. Give as much "
        "detail as the question genuinely needs — a thorough, well-reasoned answer for "
        "substantive/advice questions (a short paragraph or two), and keep it brief only "
        "for simple lookups. Answer EVERY turn with the same depth (a follow-up is not "
        "less important than the first question).\n\n"
        "■ TOOL CATALOG (every capability you have):\n"
        f"{tools_block}\n\n"
        # Only include the global VIP-centric pages list when this is VIP
        # itself OR when no agent profile is configured. For non-VIP
        # agents (Stock / Realty / Asset / AIGlass), their own profile
        # below provides the authoritative pages list; mixing in VIP's
        # Dashboard / Control Room / Twins confuses the LLM into
        # suggesting non-existent pages.
        + (
            "■ INTERNAL PAGES (for navigate(path)):\n"
            f"{pages_summary_for_llm()}\n\n"
            "■ EXTERNAL AGENT APPS (for open_portal(agent)):\n"
            f"{agents_summary_for_llm()}\n"
            if (not agent_id or agent_id.lower() == "vip" or agent_id.lower() not in AGENT_PROFILES)
            else ""
        )
        + f"{_agent_profile_block(agent_id)}"
        + f"{context_block}{page_block}{attach_block}{files_block}{kb_block}\n"
        "■ HOW TO RESPOND\n"
        "Always respond with ONE of these JSON shapes — NOTHING ELSE:\n"
        '  A. Call ONE tool:    { "tool": "<name>", "args": { ... } }\n'
        '  B. Chain N tools:    { "steps": [ { "tool": "<name>", "args": {...} }, ... ] }\n'
        '                       The backend runs each step in order, feeds the\n'
        '                       result of step N into step N+1 (you can reference\n'
        '                       step results when the user asks compound questions).\n'
        '                       Use chains for "find X and then do Y" requests.\n'
        '  C. Answer directly:  { "answer": "<your reply>" }\n\n'
        "Rules:\n"
        "- IF the KNOWLEDGE BASE section above has the answer (any excerpt "
        "  contains the entity / number / topic the user asked about): use "
        "  the answer shape with verbatim numbers from the excerpt. DO NOT "
        "  call a tool. Speak confidently in 1st/2nd person ('You are X', "
        "  'Your favorite is Y'). NEVER mention the file name, sheet name, "
        "  or that you got the fact from an upload — just state it.\n"
        "- GENERAL / OFF-TOPIC QUESTIONS — if the user asks something that is NOT about "
        "  this app or its data (general knowledge, world facts, definitions, math, "
        "  coding help, translation, 'what is the capital of Uzbekistan', 'explain "
        "  inflation', casual chat, etc.): just ANSWER it directly and helpfully from "
        "  your own knowledge, like a normal capable AI assistant. Use the ANSWER shape. "
        "  NEVER refuse, NEVER say 'I can only help with stocks / this app / my domain', "
        "  and do NOT force a tool call. Be accurate, clear and genuinely useful — only "
        "  steer back to the app if the user actually seems to want app data.\n"
        "- INTENT — EXPLAIN vs OPEN vs OFFER (very important, like Claude in "
        "  Word/Excel/Chrome — answer first, only act when explicitly told):\n"
        "    • EXPLAIN (default for questions): 'what is in settings?', 'what "
        "      does the X page do?', 'tell me about Y', '무엇이 있어?', '설명해줘', "
        "      'what can I do here' → use the ANSWER shape. Describe it in words. "
        "      DO NOT navigate. End by offering: 'Want me to open it?'\n"
        "    • OPEN (explicit command only): 'open X', 'go to X', 'take me to X', "
        "      'navigate to X', '열어', '열어줘', '이동', '가줘' → call navigate(path) "
        "      (internal page) or open_portal(agent) (external app).\n"
        "    • OFFER (ambiguous 'see/show'): 'I wanna see settings', 'show me the "
        "      X page', '보고 싶어', '보여줄래?' → use the ANSWER shape: briefly say "
        "      what's there and ASK 'Shall I open it for you?' — do NOT navigate "
        "      until they confirm (yes / 응 / 열어).\n"
        "    • CONFIRM (yes/sure/응/네/그래/open it/do it) right after YOU offered "
        "      to open a page: navigate to the EXACT page YOU just offered in your "
        "      previous message — read the conversation history above to find it. "
        "      e.g. you said 'The Agents page lists… Shall I open it?' and the user "
        "      says 'yes, open it' → navigate('/agents'), NOT the current page. If "
        "      you genuinely can't tell what was offered, ASK 'Which page?' — never "
        "      guess the current page.\n"
        "    Rule of thumb: a QUESTION never auto-navigates; only an imperative "
        "    command (open/go/take me) OR a confirmation of your own offer "
        "    navigates. NEVER navigate to a path not in the pages list above, and "
        "    NEVER default to the page the user is already on.\n"
        "- For 'I wanna see Asset/Stock/Realty Agent' as an explicit open command, "
        "  pick open_portal — those are EXTERNAL apps.\n"
        "- CROSS-AGENT DATA questions — when the user asks about ANOTHER agent's "
        "  actual data/content while in VIP (e.g. 'what is my total asset value?', "
        "  'how is my stock portfolio doing?', 'what listings does Realty have?', "
        "  'ask the Asset agent about X', '자산 에이전트한테 물어봐', or 'give me a "
        "  report across all my agents about X'): call ask_agent(agent, question) — "
        "  agent='asset'|'stock'|'realty'|'aiglass' (or a domain word, or 'all') and "
        "  question=the user's question. It returns that agent's OWN answer; then "
        "  state it and cite which agent it came from. Do NOT just navigate or "
        "  guess from your own knowledge. (Use agent_status only for status/role, "
        "  not for the agent's data.)\n"
        "- For data questions ('how is X', 'what did Y do', 'find Z'): pick the "
        "matching read tool — search_twin, search_conversations, latest_report, "
        "agent_status, etc.\n"
        "- STOCK: ADVICE vs PRICE (critical — be a real advisor, not a price ticker):\n"
        "    • PURE PRICE ONLY — when the user asks ONLY for the price: 'X 현재가', "
        "'X 주가', 'X 얼마', 'X 시세', 'what is the price of X', 'how much is X (now)', "
        "'X price' → fetch and reply with JUST the current price + as-of time. Short.\n"
        "    • ADVICE / OPINION / ANALYSIS — 'should I buy/sell X', 'is X a good buy', "
        "'X 살까/팔까', 'X 어때', 'X 매수해도 돼?', 'X 전망/분석', 'worth buying?', "
        "'entry point for X?', 'X 지금 들어가도 될까' → DO NOT answer with the price "
        "alone. This is the DEFAULT for any non-price stock question.\n"
        "        – ALWAYS call two_method_view(ticker=X) FIRST (our own two decision "
        "methods + live price) for any advice/opinion/analysis on a registered KR stock. "
        "You MAY additionally chain read_chart(ticker=X) for the technical picture. Then "
        "present BOTH methods EXPLICITLY and SEPARATELY (never merge them): 'Method 1 — "
        "Machine-Learning Algorithms: <BUY/HOLD/SELL>, best algorithm <name>, expected "
        "5-day move <%>, backtest accuracy <%>' AND 'Method 2 — Analysis (수급/호가/박스권): "
        "<매수/관망/매도>, buy/sell levels <numbers>, reasons <수급·박스권>'. Then give YOUR "
        "combined verdict + the key risk, and note whether the two methods agree "
        "(consensus = higher conviction) or disagree (주의). Use the real numbers.\n"
        "        – If two_method_view has no data for that stock, fall back: gather "
        "evidence FIRST, then give a reasoned recommendation:\n"
        "        – If you have stock tools (Stock agent): CHAIN them — quote + "
        "stock_get_investor_flow + stock_get_intraday_signals + stock_get_recommendations "
        "+ stock_get_news for that name — then answer with: current price/level, 수급 "
        "(외국인/기관) direction, momentum/technical read, any live recommendation/news, "
        "and a clear BUY / HOLD / SELL stance + the key risk.\n"
        "        – If you do NOT have stock tools (e.g. VIP): call "
        "ask_agent('stock', <the user's FULL question verbatim>) so the Stock agent does "
        "the deep analysis, then relay its answer. NEVER reply with only the price.\n"
        "      Only strip down to a bare price when the user explicitly asked for the "
        "price and nothing else.\n"
        "    • CHART / TECHNICAL — 'X 차트 어때', 'read the chart/graph', '캔들/차트 분석', "
        "'support/resistance for X', 'trend of X', '추세/지지/저항', 'is X above the moving "
        "average' → call read_chart(ticker=X), then DESCRIBE what the chart shows: trend "
        "(상승/하락/횡보), MA5/20/60, support/resistance, distance from the recent high, the "
        "last few candles (양봉/음봉) and the volume read. This reads the SAME daily candles "
        "the AI Advisor's TradingView chart plots — answer as if reading that chart.\n"
        "- AGENT questions ('what is the Asset Agent?', 'what does the Stock "
        "agent do?', 'responsibility/role of the X agent', 'is X active?', "
        "'how many agents do I have?'): call agent_status(name='Asset Agent'|'Stock'"
        "|'Real Estate') for live status, or count(entity='agents') for how many, "
        "or the ANSWER shape to describe the role (asset→asset management, "
        "stock→stock analysis, realty→real estate). These are QUESTIONS — NEVER "
        "navigate to /agents for them. Only an explicit 'open the agents page' "
        "navigates.\n"
        "- For 'what can you do' / 'help': call what_can_you_do.\n"
        "- For greetings ('hi', '안녕', 'hello'): use the answer shape with a friendly hello.\n"
        "- If unsure which tool, use the answer shape with a clarifying question.\n"
        "- Never invent a tool name not in the catalog above.\n"
        "- Never invent a page path not in the pages list above.\n\n"
        "■ COMPANION MODE — when the user is just chatting (no task verb, "
        "no entity to fetch, sharing feelings, telling a story, saying "
        "they're tired, lonely, curious about your day, etc.):\n"
        "  • Skip every tool. Use the {\"answer\": \"...\"} shape only.\n"
        "  • Respond like a warm friend, not a customer-service bot. Show "
        "    that you actually heard what they said — reference a specific "
        "    word or feeling from their message.\n"
        "  • Ask ONE genuine open-ended follow-up question per turn so the "
        "    conversation keeps flowing. Vary it — about their day, what "
        "    they're thinking, how something turned out, what they enjoy.\n"
        "  • Volunteer your own observations sometimes — share a thought, "
        "    a curiosity, a gentle suggestion. Not just questions.\n"
        "  • Remember what they tell you (names, feelings, plans, family) "
        "    and bring it back naturally in later turns. The KNOWLEDGE "
        "    BASE excerpts above may also contain personal details the "
        "    boss has uploaded — quote them gently when relevant.\n"
        "  • Keep replies short for voice (1-2 sentences) — long monologues "
        "    feel robotic when spoken. Save longer answers for explicit "
        "    questions.\n"
        "  • If the user message begins with '[silence]' it's a system "
        "    nudge that the user has gone quiet — gently restart the "
        "    conversation with a fresh open-ended question, don't "
        "    acknowledge the bracket text.\n"
        "  • In Korean, match their register (반말 ↔ 존댓말) — listen to "
        "    their last sentence and mirror.\n"
    )


# ============================================================================
#  LLM call with JSON parsing
# ============================================================================

def _pick_model_for_query(user_msg: str, history: list[dict]) -> str:
    """Smart router — picks an LLM tier based on query complexity:

      • Easy + Normal → groq-llama-3.3-70b  (free, fast, no quota worry)
      • Hard          → claude-sonnet-4-6   (paid Anthropic; cascades to
                                             Groq automatically when the
                                             paid key has no credit)

    'Hard' is detected by the same signals as before (long prompts,
    compound requests, reasoning verbs, deep conversation history).

    Override via env var `ASSISTANT_FORCE_MODEL`.
    """
    forced_env = os.getenv("ASSISTANT_FORCE_MODEL", "").strip()
    if forced_env:
        return forced_env

    q = (user_msg or "").strip()
    qlc = q.lower()

    # Signal 1 — query length
    long_query = len(q) > 200

    # Signal 2 — compound / chained request
    compound_markers = (
        " and then ", " after that ", " also ", " then ", " plus ", "; ",
        " 그리고 ", " 그다음 ", " 그런 다음 ",
    )
    is_compound = any(m in qlc or m in q for m in compound_markers)

    # Signal 3 — reasoning / synthesis verbs (these benefit from Pro)
    reasoning_markers = (
        "summarize", "summary", "explain", "why", "compare", "analyze",
        "recommend", "suggest", "draft", "write a", "rewrite", "translate",
        "요약", "왜", "비교", "분석", "추천", "초안", "다시 써", "번역",
    )
    is_reasoning = any(m in qlc for m in reasoning_markers)

    # Signal 4 — long conversation history (context-heavy follow-up)
    deep_history = len(history or []) > 6

    # Signal 5 — short follow-up that DEPENDS on context (confirmation, "it",
    # "that one", "yes"). These are short so they'd otherwise stay on the fast
    # model, but resolving them correctly (which page did I offer? what does
    # 'it' refer to?) needs the stronger reasoner. Only when there's history.
    short_followup_markers = (
        "yes", "yeah", "yep", "sure", "ok", "okay", "do it", "open it",
        "that one", "go ahead", "please do", "응", "네", "그래", "열어", "해줘",
        "그거", "맞아",
    )
    is_context_followup = bool(history) and (
        len(q) <= 40 and any(m in qlc for m in short_followup_markers)
    )

    # Escalate to the smart (paid, slower) model ONLY for genuinely heavy
    # work, so the common case stays on fast Groq. A short "explain X" / "why"
    # no longer pays the Sonnet latency tax — Groq Llama 3.3 70B handles those
    # well and returns much faster.
    hard = (
        long_query
        or is_compound
        or (is_reasoning and (len(q) > 120 or deep_history))
        or is_context_followup
    )
    if hard:
        # llm_client cascade falls back to Groq when the Anthropic key has no
        # credit, so this never bricks.
        return "claude-sonnet-4-6"
    # Easy / Normal / short-explain → free Groq Llama 3.3 70B (fast, no quota
    # worry, excellent for tool-routing + RAG answers + short explanations).
    return "groq-llama-3.3-70b"


def _call_llm_for_decision(
    system: str,
    user_msg: str,
    history: list[dict],
    forced_model: Optional[str] = None,
) -> dict:
    """Ask the LLM to pick a tool or give a direct answer. Returns dict.

    Uses _pick_model_for_query to route between fast (Groq) and smart
    (Gemini Pro) tiers. If the chosen provider returns an error / the
    "[LLM unavailable]" sentinel, we re-try with the other tier so a
    single missing API key never bricks the assistant.

    `forced_model` (optional) bypasses the smart router — used by the
    in-overlay model picker dropdown to pin a specific LLM per request.
    """
    # History turns come from the frontends as {role, text, intent}. Accept
    # BOTH `text` (what every chatbot frontend actually sends) and `content`
    # (the OpenAI-style field) — previously we only read `content`, so the
    # whole conversation was silently dropped and the assistant had no memory
    # of what it just said. That broke follow-ups like "yes, open it" after an
    # offer (it forgot WHICH page it offered). Normalize the role too.
    messages = []
    for h in (history or []):
        body = (h.get("content") or h.get("text") or "").strip()
        if not body:
            continue
        raw_role = (h.get("role") or h.get("who") or "user").lower()
        role = "assistant" if raw_role in ("assistant", "ai", "bot") else "user"
        messages.append({"role": role, "content": body[:700]})
    messages.append({"role": "user", "content": user_msg})

    # Honor an explicit per-request model override (from the overlay's
    # model dropdown). Otherwise let the smart router pick.
    primary = ((forced_model or "").strip()
               or _pick_model_for_query(user_msg, history or []))
    # Cascade order: if Claude is rate-limited, try its other tier; if both
    # Claude tiers fail (outage, key issue), drop to Gemini Flash, then
    # OpenAI as the cross-provider safety net. Cheapest survivor wins.
    if primary == "claude-haiku-4-5":
        fallback = "claude-sonnet-4-6"
    elif primary == "claude-sonnet-4-6":
        fallback = "claude-haiku-4-5"
    elif primary.startswith("gemini"):
        fallback = "claude-haiku-4-5"
    else:
        fallback = "gpt-5.4-mini"

    def _try(model: str) -> tuple[str, Optional[str]]:
        """Returns (usable_text, error_reason). usable_text is empty when
        the LLM call failed; the error_reason contains either the exception
        message OR the LLM's own '[LLM unavailable] …' sentinel so the
        caller can surface the real problem (404 model id, quota, etc.).

        Retries TRANSIENT failures (timeout, rate-limit, 5xx, cold connection,
        empty) up to 3 attempts with backoff — these blips are the #1 cause of the
        intermittent 'I don't know' replies. Permanent errors (bad key, 404 model)
        fail fast so they surface immediately."""
        import time as _t
        transient = ("timeout", "timed out", "429", "rate limit", "ratelimit",
                     "503", "502", "500", "overloaded", "unavailable", "connection",
                     "connect", "reset", "temporarily", "empty response", "read timed")
        last_err: Optional[str] = None
        for attempt in range(3):
            try:
                out = chat_completion_sync(
                    system_prompt=system_prompt,
                    messages=messages,
                    # Big enough that a multi-step {"steps":[...]} decision is never
                    # truncated mid-JSON (truncation → unparseable → raw-JSON leak).
                    max_tokens=1100,
                    temperature=0.2,
                    model=model,
                )
                text = (out or "").strip()
                # llm_client returns "[LLM unavailable] <reason>" on provider
                # failure — propagate that reason instead of pretending success.
                if text and not text.startswith("[LLM unavailable") and not text.startswith("["):
                    return text, None
                last_err = (text or "empty response from provider")
            except Exception as e:
                last_err = str(e)
            if attempt < 2 and any(k in (last_err or "").lower() for k in transient):
                _t.sleep(0.7 * (attempt + 1))  # 0.7s, 1.4s backoff
                continue
            break
        return "", last_err

    # NB: _try used to reference `system` (out of scope here). Pin to
    # `system_prompt` since this nested helper closes over the caller's
    # `system` variable name. Both refer to the same string built above.
    system_prompt = system
    raw, err_primary = _try(primary)
    err_fallback = None
    if not raw:
        log.info(f"assistant_agent: primary {primary} failed ({err_primary}); cascading to {fallback}")
        raw, err_fallback = _try(fallback)

    if not raw or raw.startswith("[LLM unavailable"):
        # Surface BOTH errors so the boss can see what's actually broken.
        # The previous opaque "Sorry, unavailable" hid quota / key / model
        # issues for hours of head-scratching.
        log.warning(f"assistant_agent: both LLM tiers failed (after retries) — "
                    f"primary {primary}: {err_primary} | "
                    f"fallback {fallback}: {err_fallback}")
        # LAST RESORT = WEB SEARCH (boss 2026-08-24: "if LLM can not answer it can
        # websearch"): both tiers are down, so answer from live search results
        # deterministically (no LLM needed to format a source list) instead of a
        # bare "try again".
        try:
            _q = next((m.get("content") for m in reversed(messages or [])
                       if m.get("role") == "user" and m.get("content")), None)
            if _q:
                from services.web_search import search_web as _ws
                _res = _ws(str(_q)[:200], num_results=5)
                _hits = (_res or {}).get("results") or []
                if _hits:
                    _ko = bool(_re.search(r"[가-힣]", str(_q)))
                    _L = ["🌐 " + ("모델 연결이 잠시 불안정해 웹 검색 결과로 대신 답해드립니다:" if _ko
                                   else "The model is briefly unreachable, so here are live web-search results instead:"), ""]
                    for _h in _hits[:5]:
                        _t = (_h.get("title") or "").strip()
                        _s = (_h.get("snippet") or "").strip()
                        _u = (_h.get("url") or "").strip()
                        if _t:
                            _L.append(f"- **{_t}** — {_s}" + (f" ([{'링크' if _ko else 'link'}]({_u}))" if _u else ""))
                    return {"answer": "\n".join(_L)}
        except Exception as _wse:
            log.warning(f"web-search last resort failed: {str(_wse)[:120]}")
        # Graceful user-facing message (the technical reason is in the logs above).
        # Both tiers failed even after transient retries → a real outage, so we ask
        # the user to retry rather than dumping provider errors at them.
        return {
            "answer": (
                "일시적으로 응답을 생성하지 못했어요. 잠시 후 다시 한 번 시도해 주세요. "
                "(Sorry — I couldn't generate a response just now. Please try again in a moment.)"
            )
        }

    # Stash which model decided this turn so the response can surface it
    # (useful for telemetry — the overlay can show 'groq' / 'gemini' chip).
    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        # Some models (Llama via Groq) emit tool calls as '<|python_tag|>name(args)'
        # instead of JSON — parse that so the call isn't leaked as raw text.
        call = _parse_pythonic_call(raw)
        if call:
            call["_model"] = primary
            return call
        # The model tried to emit a {"tool":...}/{"steps":[...]} decision but it
        # didn't parse cleanly (usually truncated). NEVER leak raw JSON/braces as
        # the user-facing answer — flag it so the caller can recover gracefully.
        s = raw.lstrip()
        if s.startswith(("{", "[")) or '"steps"' in raw or '"tool"' in raw or '"args"' in raw:
            return {"_unparsed_decision": True, "_model": primary}
        # 2400, not 500: this is the PLAIN-PROSE branch (the model answered in words
        # instead of the {"answer": …} JSON) and it carries every general-knowledge reply.
        # A 500-char cut chopped them mid-word — "* 1/4 cup of chopped", "* 2큰술" — while
        # the JSON branch below happily returned 2400. Same budget for both now.
        return {"answer": raw[:2400], "_model": primary}
    parsed["_model"] = primary
    return parsed


def _parse_pythonic_call(text: str) -> Optional[dict]:
    """Parse a Llama-style tool call ('<|python_tag|>name(arg=val, …)' or a bare
    'name(kw=val, …)') into {'tool': name, 'args': {...}}. Returns None unless it
    cleanly resolves to a KNOWN tool — so prose is never misread as a call."""
    if not text:
        return None
    import ast
    import re as _re
    t = text.strip()
    for marker in ("<|python_tag|>", "<|python_end|>", "<|eom_id|>", "```python", "```"):
        t = t.replace(marker, "")
    t = t.strip()
    # Accept an optional surrounding list: [name(...)]
    if t.startswith("[") and t.endswith("]"):
        t = t[1:-1].strip()
    m = _re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*\((.*)\)\s*$", t, _re.S)
    if not m:
        return None
    name, argstr = m.group(1), m.group(2)
    if name not in TOOL_REGISTRY:
        return None
    args: dict[str, Any] = {}
    try:
        node = ast.parse(f"_f({argstr})", mode="eval").body
        for kw in node.keywords:  # type: ignore[attr-defined]
            try:
                args[kw.arg] = ast.literal_eval(kw.value)
            except Exception:
                args[kw.arg] = None
        if node.args:  # type: ignore[attr-defined]
            props = list((TOOL_REGISTRY[name].parameters.get("properties") or {}).keys())
            for i, a in enumerate(node.args):  # positional → param order
                if i < len(props):
                    try:
                        args[props[i]] = ast.literal_eval(a)
                    except Exception:
                        pass
    except Exception:
        return None
    return {"tool": name, "args": args}


def _extract_json(text: str) -> Any:
    """Pull the first balanced JSON object out of text, tolerant to surrounding prose."""
    try:
        return json.loads(text)
    except Exception:
        pass
    depth = 0
    start = -1
    for i, c in enumerate(text):
        if c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    return json.loads(text[start:i + 1])
                except Exception:
                    start = -1
    return None


def _insert_before_summary(reply: str, extra: str) -> str:
    """Insert `extra` BEFORE the '📌 요약/Summary' block so answers always END with the
    final answer (boss format). Falls back to appending when there's no summary marker."""
    for marker in ("\n**📌 요약", "\n**📌 Summary"):
        i = reply.rfind(marker)
        if i != -1:
            return reply[:i] + extra + reply[i:]
    return reply + extra


def _elaborate_answer(question: str, lang: str, step_results: list[dict]) -> Optional[str]:
    """LLM '심층 해설 / Deep dive' section appended AFTER a deterministic reco/forecast block.
    Answer = our methods' verified numbers (correctness) + LLM elaboration (detail/fluency),
    grounded STRICTLY on the tool JSON — the LLM may not introduce any number/fact not in it.
    Groq (fast) so relays don't time out; '' / failure → skip silently."""
    en = str(lang or "").lower().startswith("en")
    try:
        import json as _json
        data = _json.dumps([{"tool": s.get("tool"), "result": s.get("result")}
                            for s in step_results], ensure_ascii=False, default=str)[:6500]
        sys_p = (
            "You are a senior Korean-equities analyst writing the ELABORATION section that follows a "
            "structured verdict block the user already saw. STRICT GROUNDING: use ONLY numbers/facts "
            "present in the DATA JSON — never invent prices, percentages, or events. Write "
            + ("ENGLISH" if en else "KOREAN (한국어)") + " only.\n"
            "Write 2-3 substantial paragraphs (no headers, no bullet lists, no verdict repetition):\n"
            "1) What the numbers actually mean for this investor right now — connect the methods' "
            "signals into one coherent story (why they agree/disagree, which to trust more here).\n"
            "2) The concrete risk: what would invalidate this view, which level/number to watch.\n"
            "3) Practical next step tied to the levels in the data.\n"
            "Conversational, confident, no fluff, no disclaimers (one exists already)."
        )
        out = chat_completion_sync(
            system_prompt=sys_p,
            messages=[{"role": "user", "content": f"QUESTION: {question}\n\nDATA:\n{data}"}],
            max_tokens=700, temperature=0.4, model="groq-llama-3.3-70b",
        )
        out = (out or "").strip()
        if not out or out.startswith("[LLM"):
            return None
        if not en:
            # llama occasionally leaks Chinese words into Korean prose (综合적으로…)
            for _cn, _ko in (("综合", "종합"), ("分析", "분석"), ("市场", "시장"),
                             ("投资", "투자"), ("经济", "경제"), ("技术", "기술"),
                             ("战略", "전략"), ("确认", "확인")):
                out = out.replace(_cn, _ko)
        return ("**Detailed explanation**\n" if en else "**상세 설명**\n") + out
    except Exception:
        return None


def _format_algo_scoreboard(sb: dict, lang) -> str:
    """🏁 Render the multi-day, fee-honest algorithm verdict board in KO/EN.
    sb = output of routers.paper_desk._scoreboard(db, days)."""
    en = str(lang or "").lower().startswith("en")
    algos = sb.get("algos", {})
    gate = sb.get("gate", {})
    rec = sb.get("recommendation", {})
    win = int(gate.get("window_days", 15))
    L = []
    if en:
        L.append(f"🏁 **Which algorithm is best?** (last {win} days · fee-honest net ₩)")
        L.append("")
    else:
        L.append(f"🏁 **어떤 알고리즘이 제일 좋은가?** (최근 {win}일 · 수수료 반영 순손익 ₩)")
        L.append("")
    for src in ("algo1", "algo2", "algo3", "algo4"):
        a = algos.get(src)
        if not a:
            continue
        label = a.get("label", src)
        if a.get("verdict") == "NO DATA":
            L.append(f"**{label}** — {'no trades yet' if en else '아직 거래 없음'}")
            continue
        net = a.get("net_won", 0)
        trips = a.get("trips", 0)
        days_n = a.get("days", 0)
        wr = a.get("win_rate")
        pt = a.get("net_per_trade")
        pct = a.get("net_pct_per_trade")
        worst = a.get("worst_day")
        if en:
            L.append(f"**{label}** — {a.get('verdict')}")
            L.append(f"  · net **{net:+,}₩** over {trips} trades / {days_n} days"
                     + (f" · win {wr}%" if wr is not None else ""))
            L.append(f"  · per trade {pt:+,}₩ ({pct:+.3f}%)" if pt is not None else "")
            if worst:
                L.append(f"  · worst day {worst[1]:+,}₩ ({worst[0]})")
            L.append(f"  · {a.get('reason','')}")
        else:
            L.append(f"**{label}** — {a.get('verdict')}")
            L.append(f"  · 순손익 **{net:+,}₩** / {trips}거래 · {days_n}일"
                     + (f" · 승률 {wr}%" if wr is not None else ""))
            L.append(f"  · 거래당 {pt:+,}₩ ({pct:+.3f}%)" if pt is not None else "")
            if worst:
                L.append(f"  · 최악의 날 {worst[1]:+,}₩ ({worst[0]})")
            L.append(f"  · {a.get('reason','')}")
        L.append("")
    if rec.get("status") == "GO":
        L.append(("👉 **Recommendation: " if en else "👉 **추천: ") + rec.get("text", "") + "**")
    else:
        L.append(("👉 **" if en else "👉 **") + rec.get("text", "") + "**")
    if en:
        L.append(f"\n_Safety gate: an algorithm is only 'READY' for real money after "
                 f"net ₩ > 0 AND ≥ {gate.get('days',5)} trading days AND ≥ {gate.get('trips',30)} "
                 f"completed trades. Win % alone is not enough — a high win rate can still "
                 f"lose money after fees._")
    else:
        L.append(f"\n_안전 기준: 순손익 > 0 이면서 거래일 ≥ {gate.get('days',5)}일, "
                 f"완료 거래 ≥ {gate.get('trips',30)}건을 모두 만족해야만 실전(real money) "
                 f"'준비완료'로 봅니다. 승률만으로는 부족합니다 — 승률이 높아도 수수료 때문에 "
                 f"손해일 수 있습니다._")
    return "\n".join([x for x in L if x is not None])


# 🔮 FUTURE / NEXT-DAY PREDICTION cues (boss 2026-07-20): "predict tomorrow's price",
# "tomorrow morning which price will it open?" must run ALL 3 algorithms + an LLM final
# summary — not dump the current price. Requires a NEXT-DAY cue AND a predict verb so it
# never grabs plain current-price or today's-high questions.
_PRED_NEXTDAY = ("tomorrow", "next day", "next-day", "next open", "next trading day",
                 "opening price", "will open", "open at", "tomorrow morning", "next session",
                 "내일", "모레", "다음날", "다음 날", "담날", "시초가", "개장", "다음 거래일", "다음거래일")
_PRED_VERB = ("predict", "prediction", "forecast", "expect", "outlook", "estimate",
              "예측", "전망", "예상", "오를", "내릴", "될까", "열릴", "얼마", "시작",
              "오를까", "내릴까", "상승", "하락",
              # boss 2026-07-20: 'what will be price... how many percent increase or decrease?'
              # must also trigger the prediction interval (nd cue already gates to next-day).
              "increase", "decrease", "percent", "%", "how much", "how many", "what will",
              "what price", "what's the price", "rise", "fall", "go up", "go down", "price",
              "가격", "몇 프로", "몇 퍼센트", "몇 프로", "몇 %", "몇퍼", "얼마나", "오르", "내려")


def _is_future_prediction(text: str) -> bool:
    t = text or ""
    tl = t.lower()
    # A question about the PAST can accidentally contain both cue lists — "last 6 days
    # ... opening prices table" hit 'opening price' + 'price' and got a 3-algorithm
    # FORECAST instead of the history table (boss 2026-08-25). An explicit past window
    # with no future word is never a prediction ask.
    if not _is_future_outlook(t) and (_requested_history_dates(t) or _is_period_stats_q(t)):
        return False
    nd = any(k in t or k in tl for k in _PRED_NEXTDAY)
    vb = any(k in t or k in tl for k in _PRED_VERB)
    return bool(nd and vb)


def _llm_prediction_summary(d: dict, block: str, name: str, lang, question: str) -> str:
    """LLM final summary over the 3 algorithms' PREDICTIONS. Pure forecast — the boss
    (2026-07-20): a prediction question must NOT contain any buy/sell/hold advice."""
    from services.llm_client import chat_completion_sync
    en = str(lang or "").lower().startswith("en")
    if en:
        sys = (
            "You are a Korean-stock trading assistant. The user asked a PURE PREDICTION "
            "question (which direction / what price) — NOT for advice. Below are the "
            "directional PREDICTIONS from three algorithms (Algorithm 1 = combined brain + "
            "1-hour up-probability; Algorithm 2 = Ripple, minutes; Algorithm 3 = Candle, "
            "1-min momentum), PLUS a 4th input — the Cross-Check line, which is simply HOW MANY "
            "of the three point the same way (3 of 3 = strong agreement; 2 = partial; split = "
            "disagreement). REFLECT that agreement in the confidence you state: all three "
            "agreeing → higher confidence; only one → low confidence; split → uncertain/sideways. "
            "Write a SHORT final summary (3-5 sentences) that (1) states the "
            "COMBINED predicted DIRECTION — up / down / flat — with rough magnitude/confidence, "
            "(2) states the PREDICTED PRICE RANGE — both the ₩ interval (from ₩X to ₩Y) AND "
            "its percent change (e.g. −4.2% ~ +1.4%) — exactly as shown in the analysis's "
            "'Expected price range' line, (3) briefly explains WHY (which algorithms agree or "
            "disagree), and (4) is honest that these are short-horizon signals, not a "
            "guaranteed price. CRITICAL: do NOT give ANY buy/sell/hold advice or recommendation "
            "— this is a forecast ONLY. Never write the words buy, sell, or hold. Use ONLY "
            "numbers that appear in the analysis — never invent prices. Answer ONLY in English. "
            "End with a one-line forecast that includes BOTH the ₩ range and the % change, prefixed '👉'."
        )
    else:
        sys = (
            "당신은 한국 주식 트레이딩 어시스턴트입니다. 사용자가 '순수 예측' 질문(어느 방향/얼마)"
            "을 했습니다 — 매매 조언 요청이 아닙니다. 아래는 3개 알고리즘의 방향 예측입니다 "
            "(알고리즘1 = 종합 브레인 + 1시간 상승확률, 알고리즘2 = 잔물결·분 단위, 알고리즘3 = "
            "캔들·1분봉 모멘텀). 여기에 4번째 입력 '교차검증' 줄이 있습니다 — 세 알고리즘 중 몇 개가 "
            "같은 방향을 보는지입니다(3/3 = 강한 일치, 2 = 부분 일치, 엇갈림 = 불일치). 이 일치도를 "
            "확신도에 반영하세요: 셋 다 일치 → 높은 확신, 하나만 → 낮은 확신, 엇갈림 → 불확실·보합. "
            "다음을 담은 짧은 요약(3~5문장)을 쓰세요: (1) 종합 예측 방향 — "
            "상승/하락/보합 — 과 대략적 강도·확신도, (2) 분석의 '예상 가격 범위' 줄에 나온 예측 "
            "가격 구간을 ₩ 금액(₩X ~ ₩Y)과 % 변동(예: −4.2% ~ +1.4%) 둘 다 제시, (3) 이유 "
            "간단히(어느 알고리즘이 일치/불일치), (4) 단기 신호라 가격을 보장하지 않는다는 점. "
            "매우 중요: 매수/매도/보유 등 어떤 매매 조언·추천도 하지 마세요 — 예측만. '매수'·'매도'"
            "·'보유'라는 단어를 쓰지 마세요. 분석에 나온 숫자만 쓰고 가격을 지어내지 마세요. 반드시 "
            "한국어로만. 마지막 줄은 ₩ 범위와 % 변동을 모두 포함해 '👉'로 시작하는 한 줄 예측."
        )
    user = (f"User question: {question}\n\nStock: {name}\n"
            f"price {d.get('price')}\n\n=== 3 ALGORITHMS' PREDICTIONS ===\n{block}")
    try:
        out = chat_completion_sync(sys, [{"role": "user", "content": user}],
                                   max_tokens=500, temperature=0.3)
        if not (out or "").strip():
            return ""
        # a REFUSAL printed under "AI Final Summary" ("I'm sorry, but I can't provide
        # that information" — boss's 2026-08-25 screenshot) is worse than no summary:
        # drop it and let the three algorithms' numbers stand on their own.
        _low_o = out.lower()
        if any(p in _low_o for p in ("i'm sorry", "i am sorry", "cannot provide",
                                     "can't provide", "unable to provide", "죄송",
                                     "제공할 수 없", "도와드릴 수 없")):
            return ""
        return ("🧠 **AI Final Summary**\n\n" if en else "🧠 **AI 종합 예측 요약**\n\n") + out.strip()
    except Exception as e:
        log.warning(f"prediction summary LLM failed: {str(e)[:120]}")
        return ""


def _run_chain(
    db: Session,
    transcript: str,
    lang: str,
    steps: list[dict],
    current_path: Optional[str],
    selected_id: Optional[str],
    system_prompt: str,
    history: list[dict],
    agent_id: str = "vip",
    user_id: Optional[str] = None,
    fresh_market_evidence: bool = False,
) -> dict[str, Any]:
    """Execute a multi-step chain. If any step is a WRITE tool, halt and
    return a proposed_chain so the widget can ask for confirmation up front
    (single confirm covers the whole chain)."""
    # Validate every step's tool exists; if any write tool appears, request confirm
    validated_steps = []
    any_write = False
    for s in steps[:6]:  # cap chain length
        tname = (s.get("tool") or "").strip()
        if tname not in TOOL_REGISTRY:
            log.warning(f"chain: skip unknown tool '{tname}'")
            continue
        targs = s.get("args") or {}
        if selected_id:
            for k in ("conversation_id", "report_id", "twin_id", "meeting_id",
                      "handoff_id", "task_id", "knowledge_id"):
                if k in (TOOL_REGISTRY[tname].parameters.get("properties") or {}) and not targs.get(k):
                    targs[k] = selected_id
                    break
        validated_steps.append({"tool": tname, "args": targs})
        if TOOL_REGISTRY[tname].requires_confirmation:
            any_write = True

    if not validated_steps:
        return {
            "intent": "chain_empty", "language": lang, "reply": "I'm not sure how to do that.",
            "action": None, "speak": True, "transcript": transcript,
        }

    if any_write:
        # Compose a multi-line preview
        preview_lines = []
        for i, s in enumerate(validated_steps, 1):
            p = _compose_write_preview(s["tool"], s["args"])
            preview_lines.append(f"{i}. {p['message']}")
        return {
            "intent": "chain_proposed", "language": lang,
            "reply": "I'd like to run these steps — confirm?\n" + "\n".join(preview_lines),
            "action": None, "speak": True, "transcript": transcript,
            "tool_used": None,
            "proposed_chain": validated_steps,
        }

    # Read-only chain — execute all and compose a final answer
    step_results = []
    for s in validated_steps:
        res = execute_tool(s["tool"], s["args"], db=db, agent_id=agent_id, transcript=transcript)
        step_results.append({"tool": s["tool"], "result": res})

    # Compose final answer from all step results — honour table/list requests.
    _fmt_line, _max_tok, _cap = _output_format_directive(transcript)
    follow_system = (
        "You just ran the following tools sequentially (same language as the "
        "user's question).\n" + _fmt_line +
        "Use specific names and numbers from the results verbatim. Do NOT wrap a "
        "table in code fences or add a 'summary for the boss' preamble.\n"
        "If the user asked for ADVICE or an OPINION on a stock (should I buy/sell, "
        "어때, 전망, is it a good buy) AND the results contain 'method1_ml'/'method2_analysis' "
        "(two_method_view): a deterministic '✅ 직접 답변' line (TOP), a '방법 1/2/3' summary "
        "block, and a '🎯 최종 추천' line (BOTTOM) are added AROUND your answer AUTOMATICALLY. "
        "So do NOT write your own verdict sentence and do NOT write a final recommendation — "
        "those are handled for you. YOUR job is ONLY the MIDDLE part: a line '근거:' "
        "(English: 'Why each method says this:') followed by a NUMBERED list explaining, "
        "FOR EACH of the 3 methods, WHY it gave its call, using the real numbers from the "
        "results — one line each:\n"
        "  1. 방법 1 (머신러닝): <call + 왜: 예상 변동/정확도/시장대비 강·약세>\n"
        "  2. 방법 2 (분석·수급/호가/박스권): <call + 왜: 호가 압력, 외국인/기관 수급, 지지/저항>\n"
        "  3. 방법 3 (파동·엘리엇/피보나치): <call + 왜: 파동 강도, 되돌림 %, 피보나치 진입대>\n"
        "No preamble, no price-only answer, no repeating the verdict/recommendation. "
        "If a stock question is NOT advice (or has no two-method data), instead lead with a "
        "ONE-sentence verdict (매수/보유/매도 + biggest risk) then a '근거:' numbered list."
    )
    if fresh_market_evidence:
        follow_system = (
            "You just fetched live market evidence for the user's question. Answer in a "
            "natural shape and length that directly fits the question; do not force a fixed "
            "briefing template, headings, table, verdict, or recommendation. Use only facts "
            "present in the tool results. Clearly distinguish verified facts from your brief "
            "interpretation, and include the available source/provider and fetched_at time. "
            "If a source failed or returned no data, say that plainly; never fill the gap with "
            "generic market commentary or facts from memory."
        )
    import json as _json
    summary_input = _json.dumps(step_results, ensure_ascii=False, default=str)[:max(_cap, 3000)]
    try:
        reply = chat_completion_sync(
            system_prompt=follow_system,
            messages=[
                {"role": "user", "content": f"Question: {transcript}"},
                {"role": "user", "content": f"Tool chain results:\n{summary_input}"},
            ],
            max_tokens=max(_max_tok, 650), temperature=0.3,
            model="groq-llama-3.3-70b",
        )
    except Exception:
        reply = "Done — checked the data."

    # Deterministic two-method header — guarantee BOTH methods show (방법1/방법2),
    # regardless of whether the LLM formatted them.
    _tm = next((s.get("result") for s in step_results
                if s.get("tool") == "two_method_view"
                and isinstance(s.get("result"), dict) and s["result"].get("ok")), None)
    if _tm:
        # OUTLOOK = a pure FORECAST. two_method_view is the outlook tool (buy/sell ADVICE now
        # goes to 'decide'), so it must NOT carry a buy/sell verdict or a '최종 추천' — that
        # would make the outlook read like a recommendation. Show only the deterministic
        # forecast block (direction + range + per-method forecast + scenarios).
        reply = _two_method_header(_tm, lang)

    # decide tool: use its OWN language-correct reasoning verbatim (the LLM otherwise
    # mixes EN/KO). MULTI-STOCK: '삼성전자랑 SK하이닉스 살까?' runs decide per stock — join
    # ALL results (name-headed) so every asked stock gets its own verdict.
    _decs = [s.get("result") for s in step_results
             if s.get("tool") == "decide"
             and isinstance(s.get("result"), dict) and s["result"].get("ok")]
    if _decs:
        _en = str(lang or "").lower().startswith("en")
        _parts = []
        for _dec in _decs:
            # 🎯 CLEAN RECOMMENDATION LAYOUT (boss 2026-07-20): one-line final decision →
            # Algorithm 1 (decision + 1h prediction + ML/news/YT/chart/Kiwoom/orderbook/
            # wave detail) → Algo 2 Ripple → Algo 2 Candle → final answer from the 3.
            # Replaces the old confusing dump entirely ("that's all, nothing more").
            try:
                from services.decision_brain import clean_recommendation as _brain_clean
                _p = _brain_clean(db, _dec, lang, transcript)
            except Exception:
                _p = _dec.get("reasoning_en" if _en else "reasoning_ko") or ""
            if _p and len(_decs) > 1:
                _p = f"# 📌 {_dec.get('name') or _dec.get('ticker')}\n\n{_p}"
            if _p:
                _parts.append(_p)
            # M1.2 — measure it: log EACH stock's advice for grading after its horizon.
            # When the ⚡ live 1-hour setup drove the answer ("BUY — 1-hour trade"),
            # grade THAT call (BUY, setup target/stop) — the record must score what
            # the user was actually told, not the background investment verdict.
            try:
                from services.call_grader import log_call
                _wv = _dec.get("method3_wave") or {}
                _isu = _dec.get("intraday_setup") or {}
                if _isu.get("answered_buy_1h"):
                    _tb = _isu.get("target_band") or [None, None]
                    log_call(db, ticker=_dec.get("ticker"), action="BUY",
                             intent="decision_1h", ref_price=_dec.get("price"),
                             target=_tb[0], stop=_isu.get("stop"),
                             horizon_min=int(_isu.get("time_min") or 60),
                             name=_dec.get("name"), agent_id=agent_id, lang=lang)
                else:
                    log_call(db, ticker=_dec.get("ticker"), action=_dec.get("decision"),
                             intent="decision", ref_price=_dec.get("price"),
                             target=_wv.get("target"), stop=_wv.get("stop"), horizon_min=60,
                             name=_dec.get("name"), agent_id=agent_id, lang=lang)
            except Exception:
                pass
        if _parts:
            reply = "\n\n---\n\n".join(_parts)
        # boss 2026-07-20: the clean 3-case recommendation is self-contained —
        # "that's all, nothing more". The old track-record footer, LLM deep-dive,
        # and appended chart block are suppressed for decide answers below.

    # DETAIL LAYER (user ask: answers were too short): an LLM-written '심층 해설 /
    # Deep dive' elaborating STRICTLY on the deterministic method data. Placed INSIDE
    # the body — before the 📌 summary — so the answer ends with the final answer
    # (boss: 'Deep Dive after the final answer is wrong').
    if _tm and not _decs:            # outlook only — recommendations use the clean 3-case layout
        _extra = _elaborate_answer(transcript, lang, step_results)
        if _extra:
            reply = _insert_before_summary(reply or "", "\n\n" + _extra)

    # 📈 GRAPH OPINION on every recommendation (boss 2026-07-16: "in case of the
    # recommendation also it should give graph analysis opinion") — the deep
    # multi-timeframe chart read (1-min · 5-min · daily · analog pattern)
    # appended to decide / two-method answers for the first ticker.
    if _tm and not _decs:            # recommendation already carries chart in Algo-1 detail
        try:
            _ct = None
            for _s in (steps or []):
                if _s.get("tool") in ("decide", "two_method_view"):
                    _ct = (_s.get("args") or {}).get("ticker")
                    break
            if _ct:
                from services.chart_analysis import chart_read as _cr
                from services.prediction_service import NAMES as _cnames
                _crd = _cr(db, str(_ct), _cnames.get(str(_ct).zfill(6)))
                if _crd.get("ok"):
                    reply = (reply or "") + "\n\n" + (
                        _crd["block_ko"] if lang == "ko" else _crd["block_en"])
        except Exception as e:
            log.warning(f"chart opinion append failed: {str(e)[:100]}")

    # If any step returned an action (navigate / open_portal), surface the LAST one
    action = None
    for s in reversed(step_results):
        a = (s.get("result") or {}).get("action")
        if a:
            action = a
            break

    # For chains, derive suggestions from the LAST tool that ran (most
    # recent intent is the one the user is likely to follow up on)
    last_tool = step_results[-1]["tool"] if step_results else None
    last_result = step_results[-1].get("result") if step_results else None
    return {
        "intent": "chain_completed",
        "language": lang,
        # 6000 (was 1500→4000): the 3-method recommendation + deep-dive is intentionally
        # detailed; EN answers hit exactly 4000 = truncated mid-sentence.
        "reply": (reply or "Done.")[:9000],
        "action": action,
        "speak": True,
        "transcript": transcript,
        "tool_used": "[chain]",
        "tool_result": {"steps": step_results, "step_count": len(step_results)},
        "suggestions": _suggest_followups(last_tool, last_result, lang),
    }


def _build_card(tool_name: str, result: dict) -> Optional[dict]:
    """Convert a read-tool's result into a structured display card the
    widget can render (Notion-AI style). Returns None when the tool
    result isn't card-worthy (just text / action)."""
    if not isinstance(result, dict) or not result.get("ok"):
        return None
    if tool_name == "search_twin" and result.get("matches"):
        return {
            "type": "twin_list",
            "title": f"Found {len(result['matches'])} twin(s)",
            "items": result["matches"],
        }
    if tool_name == "twin_activity" and result.get("activities"):
        return {
            "type": "activity_list",
            "title": f"{result.get('twin_name', '?')} — last {result.get('hours_window', '?')}h activity",
            "items": result["activities"],
        }
    if tool_name == "twin_tasks" and result.get("tasks"):
        return {
            "type": "task_list",
            "title": f"{result.get('twin_name', '?')} — tasks",
            "items": result["tasks"],
        }
    if tool_name == "search_conversations" and result.get("matches"):
        return {
            "type": "conversation_list",
            "title": f"Found {result['count']} conversation(s)",
            "items": result["matches"],
        }
    if tool_name == "conversation_history" and result.get("messages"):
        return {
            "type": "message_thread",
            "title": f"Conversation {result.get('conversation_id', '')[:8]}",
            "items": result["messages"],
        }
    if tool_name == "latest_report":
        return {
            "type": "report_excerpt",
            "title": result.get("title") or f"{result.get('type', '?').title()} Report",
            "summary": result.get("summary"),
            "report_id": result.get("report_id"),
        }
    if tool_name == "search_reports" and result.get("matches"):
        return {
            "type": "report_list",
            "title": f"Found {result['count']} report(s)",
            "items": result["matches"],
        }
    if tool_name == "agent_status":
        return {
            "type": "agent_status_card",
            "title": f"{result.get('agent', '?')} ({result.get('type', '?')})",
            "summary": result.get("summary"),
            "data": result.get("data"),
        }
    if tool_name == "list_pending_approvals" and result.get("cases"):
        return {
            "type": "approval_list",
            "title": f"{result['count']} pending approval(s)",
            "items": result["cases"],
        }
    if tool_name == "search_knowledge" and result.get("matches"):
        return {
            "type": "knowledge_list",
            "title": f"Found {result['count']} knowledge entrie(s)",
            "items": result["matches"],
        }
    if tool_name == "count":
        return {
            "type": "stat_card",
            "label": result.get("entity"),
            "value": result.get("count"),
        }
    if tool_name == "latest_meeting_notes" and result.get("notes"):
        return {
            "type": "meeting_notes_list",
            "title": f"{result['count']} latest meeting note(s)",
            "items": result["notes"],
        }
    if tool_name == "list_pages" and result.get("pages"):
        return {
            "type": "page_list",
            "title": f"{result['count']} pages available",
            "items": result["pages"],
        }
    if tool_name == "semantic_search" and result.get("matches"):
        return {
            "type": "cross_search",
            "title": f"Found {result['count']} matches for '{result.get('query', '')}'",
            "by_source": result.get("by_source"),
            "items": result["matches"],
        }
    return None


def _compose_write_preview(tool_name: str, args: dict) -> dict[str, Any]:
    """Human-readable preview of a write action before user confirms.
    Returns {"message": str, "details": dict (optional)}."""
    if tool_name == "send_dm":
        return {
            "message": f"📩 Send DM to {args.get('twin_name', '?')}: \"{(args.get('body') or '')[:120]}\"",
            "details": {"target": args.get("twin_name"), "body": args.get("body")},
        }
    if tool_name == "send_email":
        return {
            "message": f"✉️ Send email to {args.get('to', '?')}: \"{(args.get('subject') or '')[:60]}\"",
            "details": {"to": args.get("to"), "subject": args.get("subject"),
                        "body": (args.get("body") or "")[:300]},
        }
    if tool_name == "broadcast":
        return {
            "message": f"📢 Broadcast to ALL workers: \"{(args.get('body') or '')[:120]}\"",
            "details": {"body": args.get("body")},
        }
    if tool_name == "kakao_reply":
        return {
            "message": f"💬 Reply on Kakao conversation {args.get('conversation_id', '?')[:8]}: \"{(args.get('text') or '')[:120]}\"",
            "details": {"conversation_id": args.get("conversation_id"), "text": args.get("text")},
        }
    if tool_name == "trigger_daily_report":
        return {"message": "📊 Generate today's daily report now?"}
    if tool_name == "trigger_weekly_report":
        return {"message": "📈 Generate this week's report now?"}
    if tool_name == "approve_handoff":
        return {"message": f"✅ Approve handoff {args.get('handoff_id', '?')[:12]}?"}
    if tool_name == "approve_all_pending":
        return {"message": "✅ Approve ALL pending overnight handoffs?"}
    if tool_name == "reject_handoff":
        return {"message": f"❌ Reject handoff {args.get('handoff_id', '?')[:12]}? Reason: {args.get('reason', '(none)')}"}
    if tool_name == "resolve_conversation":
        return {"message": f"✓ Mark Kakao conversation {args.get('conversation_id', '?')[:8]} as resolved?"}
    if tool_name == "take_over_conversation":
        return {"message": f"👤 Take over Kakao conversation {args.get('conversation_id', '?')[:8]} (you will reply manually)?"}
    if tool_name == "escalate_conversation":
        return {"message": f"⚠️ Escalate Kakao conversation {args.get('conversation_id', '?')[:8]} as urgent?"}
    if tool_name == "create_task":
        return {"message": f"➕ Create task '{args.get('title', '')[:60]}' assigned to {args.get('twin_name', '?')}?"}
    if tool_name == "cancel_task":
        return {"message": f"❌ Cancel task {args.get('task_id', '?')[:12]}?"}
    if tool_name == "schedule_meeting":
        return {"message": f"📅 Schedule meeting with {args.get('participants', '?')} at {args.get('when', '?')}: {args.get('agenda', '')[:60]}"}
    if tool_name == "cancel_meeting":
        return {"message": f"❌ Cancel meeting {args.get('meeting_id', '?')[:12]}?"}
    if tool_name == "add_knowledge":
        return {"message": f"📝 Add knowledge to {args.get('twin_name', '?')}: '{args.get('title', '')[:60]}'?"}
    if tool_name == "delete_knowledge":
        return {"message": f"🗑️ Delete knowledge entry {args.get('knowledge_id', '?')[:12]}?"}
    if tool_name == "set_boss_mode":
        return {"message": f"🔧 Set Boss mode to '{args.get('mode')}' for {args.get('hours', 24)} hours?"}
    if tool_name == "set_twin_mode":
        return {"message": f"🔧 Set {args.get('twin_name', '?')}'s mode to '{args.get('mode')}'?"}
    # ── New tools from the 56-tool expansion ──
    if tool_name == "create_twin":
        return {"message": f"➕ Create new twin '{args.get('name', '?')}' owned by {args.get('owner_email', '(default)')}?"}
    if tool_name == "delete_twin":
        return {"message": f"🗑️ DELETE twin '{args.get('twin_name', '?')}' and ALL its data? This cannot be undone."}
    if tool_name == "update_twin_owner":
        return {"message": f"✏️ Change {args.get('twin_name', '?')}'s owner to {args.get('owner_email', '?')}?"}
    if tool_name == "update_task_status":
        return {"message": f"✓ Move task {args.get('task_id', '?')[:12]} → status '{args.get('status', '?')}'?"}
    if tool_name == "update_task_priority":
        return {"message": f"⚑ Set task {args.get('task_id', '?')[:12]} priority → '{args.get('priority', '?')}'?"}
    if tool_name == "reassign_task":
        return {"message": f"↪️ Reassign task {args.get('task_id', '?')[:12]} → {args.get('twin_name', '?')}?"}
    if tool_name == "update_knowledge":
        return {"message": f"✏️ Edit knowledge entry {args.get('knowledge_id', '?')[:12]}?"}
    if tool_name == "trigger_cross_agent_report":
        return {"message": "📊 Generate a cross-agent summary (Asset + Stock) report now?"}
    if tool_name == "delete_report":
        return {"message": f"🗑️ DELETE report {args.get('report_id', '?')[:12]}? This cannot be undone."}
    if tool_name == "trigger_workflow":
        return {"message": f"▶️ Manually run workflow {args.get('workflow_id', '?')[:12]} now?"}
    if tool_name == "set_workflow_enabled":
        verb = "enable" if args.get("enabled") else "disable"
        return {"message": f"⚙️ {verb.capitalize()} workflow {args.get('workflow_id', '?')[:12]}?"}
    if tool_name == "unsend_dm":
        return {"message": f"↩️ Unsend DM {args.get('message_id', '?')[:12]}? This deletes the message from your records."}
    if tool_name == "unsend_last_dm":
        return {"message": f"↩️ Unsend your last DM to {args.get('twin_name', '?')}?"}
    # Generic fallback
    return {"message": f"Run {tool_name}({args})?"}


def _output_format_directive(user_msg: str) -> tuple[str, int, int]:
    """Decide the reply FORMAT from what the user asked for. Returns
    (instruction_line, max_tokens, data_char_cap). Honours table/list requests
    (KO+EN) — model-agnostic, so 'make a table' yields a real Markdown table."""
    u = (user_msg or "").lower()
    wants_table = ("표" in (user_msg or "")) or any(
        k in u for k in ("table", "tabular", "spreadsheet", "grid", "테이블",
                         "표로", "표 만", "칸으로", "행과 열", "도표"))
    wants_list = any(k in u for k in ("list", "bullet", "목록", "리스트",
                                      "나열", "항목별", "불릿"))
    if wants_table:
        return (
            "Format the answer as a clean GitHub-flavored MARKDOWN TABLE.\n"
            "- Choose sensible column headers from the data.\n"
            "- One row per item; include EVERY row present in the result "
            "(don't truncate); order newest/most-relevant first.\n"
            "- Use the real numbers/dates from the result. At most ONE short "
            "line of text before the table.\n",
            1300, 6000,
        )
    if wants_list:
        return (
            "Format the answer as a concise MARKDOWN bullet list — one bullet per "
            "item with its key fields and real values. No long prose.\n",
            900, 5000,
        )
    return (
        "Answer like a knowledgeable consultant: give the full reasoning and the "
        "specifics the question needs — usually one to three short paragraphs (more if "
        "the question is complex), using the real numbers/names from the data. Be "
        "thorough, clear and consistent every turn (a follow-up deserves the same depth "
        "as the first answer); don't cut it short, but don't pad with filler.\n",
        750, 2600,
    )


def _fmt_wave_line(wv: dict, en: bool) -> Optional[str]:
    """One-line Method 3 (Wave) summary for the header, or None if no wave data."""
    if not wv or wv.get("verdict") in (None, "N/A"):
        return None
    v = wv.get("verdict")
    vlabel = ({"BUY": "BUY", "WATCH": "WATCH", "AVOID": "AVOID"} if en
              else {"BUY": "매수", "WATCH": "관망", "AVOID": "회피"}).get(v, v)
    sc = wv.get("wave_score")
    bits = [(f"{vlabel} (wave score {sc})" if en else f"{vlabel} (파동점수 {sc})")]
    if v == "BUY" and wv.get("entry"):
        bits.append((f"entry ~{wv['entry']:,} / stop {wv['stop']:,} / target {wv['target']:,} (R:R {wv.get('rr')})"
                     if en else
                     f"진입 ~{wv['entry']:,} / 손절 {wv['stop']:,} / 목표 {wv['target']:,} (R:R {wv.get('rr')})"))
    elif wv.get("entry"):
        bits.append((f"buy zone {wv['entry']:,} (Fib 0.618)" if en else f"매수구간 {wv['entry']:,} (피보 0.618)"))
    return " · ".join(bits)


def _wave_line_for(tm: dict, en: bool) -> Optional[str]:
    """Fetch the Wave (Method 3) verdict for tm's ticker via a short-lived session."""
    try:
        from db.base import SessionLocal
        from services.wave_method import wave_for
        tkr = tm.get("ticker")
        if not tkr:
            return None
        _db = SessionLocal()
        try:
            return _fmt_wave_line(wave_for(_db, str(tkr).zfill(6)), en)
        finally:
            _db.close()
    except Exception:
        return None


def _wave_dict_for(tm: dict) -> dict:
    """Raw Method-3 (Wave) verdict dict for tm's ticker (for the 3-method vote)."""
    try:
        from db.base import SessionLocal
        from services.wave_method import wave_for
        tkr = tm.get("ticker")
        if not tkr:
            return {}
        _db = SessionLocal()
        try:
            return wave_for(_db, str(tkr).zfill(6)) or {}
        finally:
            _db.close()
    except Exception:
        return {}


def _direct_and_final(tm: dict, lang: str = "ko") -> tuple[str, str]:
    """Deterministic '✅ 직접 답변' (top) + '🎯 최종 추천' (bottom) for an advice answer,
    synthesised by VOTING the 3 methods (ML advice + Analysis signal + Wave verdict).
    Bilingual; advisory only. Returns (direct_line, final_line)."""
    en = str(lang or "").lower().startswith("en")
    m1 = (tm.get("method1_ml") or {}).get("advice") or ""
    m2 = (tm.get("method2_analysis") or {}).get("signal") or ""
    m3 = (_wave_dict_for(tm).get("verdict") or "")
    calls = [c.upper() for c in (m1, m2, m3) if c and c.upper() != "N/A"]

    def _d(x: str) -> int:
        x = x.upper()
        return 1 if x == "BUY" else (-1 if x in ("SELL", "AVOID") else 0)
    buys = sum(1 for c in calls if _d(c) > 0)
    sells = sum(1 for c in calls if _d(c) < 0)
    n = len(calls)

    if buys >= 2 and sells == 0:
        stance = "buy"
    elif sells >= 2 and buys == 0:
        stance = "sell"
    elif buys > 0 and sells > 0:
        stance = "mixed"
    elif buys == 1 and sells == 0:
        stance = "weak_buy"
    elif sells == 1 and buys == 0:
        stance = "weak_sell"
    else:
        stance = "neutral"

    if en:
        direct = {
            "buy": "✅ **Direct answer:** Leans **BUY** — the majority of the 3 methods are positive (consider a careful, scaled-in entry).",
            "sell": "✅ **Direct answer:** Leans **SELL / AVOID** — the majority are negative (hold off on new buying).",
            "mixed": "✅ **Direct answer:** **Signals conflict** — WATCH for now; wait for confirmation before entering.",
            "weak_buy": "✅ **Direct answer:** **Weak BUY** — only a mild positive signal; watch, or enter small/scaled.",
            "weak_sell": "✅ **Direct answer:** **Weak SELL** — trim if holding, hold off on new buys.",
            "neutral": "✅ **Direct answer:** **No clear signal** — WATCH is the sensible stance right now.",
        }[stance]
        final = {
            "buy": f"🎯 **Final recommendation:** {buys}/{n} methods say BUY, {sells} negative → **buy bias**. Scale in and set a stop to manage risk.",
            "sell": f"🎯 **Final recommendation:** {sells}/{n} methods are negative, {buys} positive → **avoid / reduce**. Wait for a better setup.",
            "mixed": f"🎯 **Final recommendation:** methods disagree ({buys} buy / {sells} negative) → **WATCH**. Act only if they align.",
            "weak_buy": f"🎯 **Final recommendation:** only a weak edge ({buys}/{n} buy) → **watch or small position**, with a tight stop.",
            "weak_sell": f"🎯 **Final recommendation:** a weak negative ({sells}/{n}) → **hold off on new buys**, trim if already in.",
            "neutral": f"🎯 **Final recommendation:** no method has a clear edge → **WATCH** and wait.",
        }[stance]
        final += " ※ Reference only — not investment advice; verify before trading."
    else:
        direct = {
            "buy": "✅ **직접 답변:** **매수**에 무게 — 3가지 방법 중 다수가 긍정적입니다 (신중히 분할 매수 고려).",
            "sell": "✅ **직접 답변:** **매도/회피**에 무게 — 다수가 부정적입니다 (신규 매수 자제).",
            "mixed": "✅ **직접 답변:** **신호가 엇갈립니다** — 지금은 관망, 정렬될 때까지 진입 보류.",
            "weak_buy": "✅ **직접 답변:** **약한 매수 신호** — 관망하거나 소량·분할로 접근.",
            "weak_sell": "✅ **직접 답변:** **약한 매도 신호** — 보유 시 비중 축소, 신규 매수 보류.",
            "neutral": "✅ **직접 답변:** **뚜렷한 신호 없음** — 관망이 합리적입니다.",
        }[stance]
        final = {
            "buy": f"🎯 **최종 추천:** 3가지 방법 중 {buys}개 매수·{sells}개 부정 → **매수 우세**. 분할 매수 + 손절 설정으로 리스크를 관리하세요.",
            "sell": f"🎯 **최종 추천:** {sells}개 부정·{buys}개 긍정 → **회피/비중 축소**. 더 좋은 자리를 기다리세요.",
            "mixed": f"🎯 **최종 추천:** 방법 간 엇갈림({buys} 매수 / {sells} 부정) → **관망**. 신호가 정렬될 때만 진입.",
            "weak_buy": f"🎯 **최종 추천:** 엣지 약함({buys}/{n} 매수) → **관망 또는 소량**, 타이트한 손절.",
            "weak_sell": f"🎯 **최종 추천:** 약한 부정({sells}/{n}) → **신규 매수 보류**, 보유 시 비중 축소.",
            "neutral": f"🎯 **최종 추천:** 뚜렷한 우위 없음 → **관망** 후 대기.",
        }[stance]
        final += " ※ 참고용이며 투자 권유가 아닙니다. 매매 전 반드시 직접 확인하세요."
    return direct, final


def _cycle_dict_for(tm: dict) -> dict:
    """Method-4 (±1% cycle) live signal for tm's ticker (short-lived session)."""
    try:
        from db.base import SessionLocal
        from services.cycle_scalp import signal
        tkr = tm.get("ticker")
        if not tkr:
            return {}
        _db = SessionLocal()
        try:
            return signal(_db, str(tkr).zfill(6)) or {}
        finally:
            _db.close()
    except Exception:
        return {}


def _two_method_header(tm: dict, lang: str = "ko") -> str:
    """Build the deterministic '방법 1 / 방법 2' (Method 1 / Method 2) block from a
    two_method_view result — bilingual, so EN and KO get the SAME structured block in
    the user's language (the LLM is unreliable at always showing both methods)."""
    # A detailed FORECAST (distinct from the buy/sell recommendation): where the stock is
    # likely headed over ~5 days, each method's prediction, wave targets, and up/down
    # scenarios with key levels. Bilingual + deterministic (EN==KO, VIP==AI Advisor).
    en = str(lang or "").lower().startswith("en")
    m1 = tm.get("method1_ml") or {}
    m2 = tm.get("method2_analysis") or {}
    lv = m2.get("levels") or {}
    name = tm.get("name") or tm.get("ticker") or ""
    wave = _wave_dict_for(tm)
    pfx = "₩" if en else ""
    unit = "" if en else "원"

    def _f(x):
        try:
            return f"{int(round(float(x))):,}"
        except Exception:
            return None

    def _w(x):
        f = _f(x)
        return f"{pfx}{f}{unit}" if f else None

    _EN_NAMES = {"SK하이닉스": "SK Hynix", "삼성전자": "Samsung Electronics",
                 "삼성전기": "Samsung Electro-Mechanics", "SK스퀘어": "SK Square",
                 "한미반도체": "Hanmi Semiconductor", "NAVER": "NAVER", "카카오": "Kakao"}
    disp = _EN_NAMES.get(name, name) if en else name
    src = tm.get("live_source") or ""
    src_tag = (("Kiwoom (live)" if ("실전" in src or "키움" in src)
                else "Naver" if ("naver" in src.lower() or "네이버" in src) else src) if en else src)
    price_num = tm.get("live_price")
    price = _w(price_num)

    adv = (m1.get("advice") or "").upper()
    em = m1.get("expected_move_pct")
    lo_pct, hi_pct = m1.get("expected_low_pct"), m1.get("expected_high_pct")
    acc, algo = m1.get("backtest_accuracy_pct"), m1.get("best_algorithm")
    rng = None
    if price_num and lo_pct is not None and hi_pct is not None:
        try:
            rng = f"{_w(float(price_num) * (1 + float(lo_pct) / 100))} ~ {_w(float(price_num) * (1 + float(hi_pct) / 100))}"
        except Exception:
            rng = None
    sig = (m2.get("signal") or "").upper()
    reasons = ", ".join(str(x) for x in ((m2.get("reasons_en") if en else m2.get("reasons")) or [])[:3])
    buy_lo, sell_hi = lv.get("buy_lo"), lv.get("sell_hi")
    wv, wsc = (wave.get("verdict") or "").upper(), wave.get("wave_score")

    dir_ko = {"BUY": "상승 우세 — 모델이 시장 대비 아웃퍼폼 예측",
              "SELL": "하락 우세 — 시장 대비 언더퍼폼 예측",
              "HOLD": "뚜렷한 방향성 약함 — 중립 신호"}.get(adv, "중립")
    dir_en = {"BUY": "leaning UP — model sees market-outperformance",
              "SELL": "leaning DOWN — underperformance",
              "HOLD": "no strong direction — weak/neutral signal"}.get(adv, "neutral")
    sig_ko = {"BUY": "매수 우위", "SELL": "매도 우위", "WATCH": "관망", "HOLD": "관망"}.get(sig, "관망")
    sig_en = {"BUY": "buy-side", "SELL": "sell-side", "WATCH": "neutral", "HOLD": "neutral"}.get(sig, "neutral")

    # Method 4 — live cycle-timing signal (friendly forecast voice, all FOUR methods).
    m4 = _cycle_dict_for(tm)
    m4v = m4.get("verdict") if m4.get("ok") else None
    _agree_up = sum(1 for x in (adv == "BUY", sig == "BUY", wv == "BUY") if x)
    _agree_dn = sum(1 for x in (adv == "SELL", sig == "SELL", wv == "AVOID") if x)
    mood_ko = ("전반적으로 위쪽 힘이 더 세 보여요" if _agree_up > _agree_dn
               else "전반적으로 아래쪽 압력이 좀 더 커 보여요" if _agree_dn > _agree_up
               else "지금은 위·아래 힘이 팽팽해서, 방향이 정해지길 기다리는 구간이에요")
    mood_en = ("overall the upside pressure looks stronger" if _agree_up > _agree_dn
               else "overall the downside pressure looks a bit stronger" if _agree_dn > _agree_up
               else "buyers and sellers look evenly matched right now — a wait-for-direction zone")

    if en:
        L = [f"**📈 {disp} — Outlook (next ~5 days · 4 methods)**"
             + (f"  ·  now {price}" + (f" ({src_tag})" if src_tag else "") if price else "")]
        L += ["", "**Where it's likely headed**",
              f"- In one line: {mood_en}." + (f" Expected swing ±{abs(em)}%." if em is not None else "")]
        if rng:
            L.append(f"- Likely 5-day range: {rng} — plan entries/exits inside this band.")
        L += ["", f"**Method 1 — Machine Learning" + (f" ({algo})" if algo else "") + "**",
              f"- Forecast: {dir_en}. The model read ~19 features (price/volume/investor flows) over 20 years of "
              f"Korean-market data, and this is the side its probability tilted to"
              + (f" — backtest accuracy {acc}%, so treat it as one vote, not gospel." if acc is not None else "."),
              ((f"- Its numbers: expected 5-day swing ±{abs(em)}%" if em is not None else "- Expected move: n/a")
               + (f", which maps to roughly {rng} from here." if rng else ".")
               + " If price starts breaking out of that band, news/flows are overtaking the model's read.")]
        L += ["", "**Method 2 — Analysis (orderbook · flows · box)**",
              f"- Signal: {sig_en}" + (f" — {reasons}." if reasons else ".")
              + " This is the 'what real money is doing right now' view — order-book depth, foreign/institutional "
              "net flows and box position scored together; it reacts fastest when the mood flips."]
        if buy_lo and sell_hi:
            L.append(f"- Its trade map: support ~{_w(buy_lo)} / resistance ~{_w(sell_hi)} — the range that's been "
                     f"holding. It favours buying near {_w(buy_lo)} and taking profit near {_w(sell_hi)}, not chasing the middle.")
        if wv in ("BUY", "WATCH", "AVOID"):
            _ret = wave.get("retrace")
            L += ["", "**Method 3 — Wave (Elliott · Fibonacci)**",
                  f"- Verdict: {wv}" + (f" (wave score {wsc}" + (f", pullback {round(_ret*100)}%" if _ret is not None else "") + ")" if wsc is not None else "")
                  + {"BUY": " — a strong rally pulled back deep enough (61.8–78.6% Fibonacci zone) to be a classic dip-buy spot.",
                     "WATCH": " — the rally is real (score ≥0.65 counts as strong) but the dip hasn't reached the 61.8–78.6% buy zone yet; it watches rather than chases.",
                     "AVOID": " — the up-wave itself is weak or broken, so this method steps aside until a fresh strong wave forms."}.get(wv, "")]
            if wave.get("target"):
                L.append(f"- Its map: upside target ₩{_f(wave['target'])}"
                         + (f" · deep-pullback buy near ₩{_f(wave.get('entry'))}" if wave.get("entry") else "")
                         + (f" · stop ₩{_f(wave.get('stop'))}" if wave.get("stop") else "")
                         + " — it only trades when price comes to ITS levels, which is why its backtest edge held up.")
        if m4v:
            L += ["", "**Method 4 — Cycle Scalp (real-time timing · ±1%)**",
                  f"- Right now: {m4v.replace('_',' ')}"
                  + (f" — 5-min RSI {m4.get('rsi_prev')}→{m4.get('rsi')}" if m4.get("rsi") is not None else "")
                  + {"BUY_NOW": ". The short-term timing just turned up out of the low zone — this method would take a +1% cycle here.",
                     "WAIT": ". It's near the low zone but the upturn hasn't fired yet — it waits for the RSI to actually turn rather than guessing the bottom.",
                     "NO_SETUP": ". No pullback to work with right now — it sits out until a dip forms, because chasing strength is exactly what this method avoids."}.get(m4v, ".")]
            if m4.get("target"):
                L.append(f"- If it fires: target +1% (₩{_f(m4['target'])}) / stop −1% (₩{_f(m4['stop'])}) / "
                         f"{m4.get('time_stop_min', 60)}-min time-stop, then re-enter on the next leg — small losses, repeated wins.")
            if m4.get("veto_en"):
                L.append(f"- {m4['veto_en']}")
        L.append("")
        L.append("**Scenarios — the two levels that matter**")
        if sell_hi:
            L.append(f"- Break above ~{_w(sell_hi)} with volume → the upside scenario opens; shorts get squeezed.")
        if buy_lo:
            L.append(f"- Lose ~{_w(buy_lo)} → expect a deeper pullback; the wave method's deep-buy zone comes into play.")
        # FINAL REMINDER (user ask): after all the explanation, repeat the conclusion.
        L += ["", f"**Final take — one line again:** {mood_en}."
              + (f" Likely range {rng};" if rng else "")
              + (f" watch resistance ~{_w(sell_hi)}" if sell_hi else "")
              + (f" and support ~{_w(buy_lo)}." if buy_lo else ".")]
        L += ["", "_Forecast only — not a buy/sell call. Ask \"should I buy?\" for a recommendation._"]
        return "\n".join(L)

    L = [f"**📈 {disp} — 향후 전망 (향후 ~5일 · 4가지 방법)**"
         + (f"  ·  현재가 {price}" + (f" ({src})" if src else "") if price else "")]
    L += ["", "**어디로 향할까**",
          f"· 한 줄 요약: {mood_ko}." + (f" 예상 변동폭은 ±{abs(em)}%예요." if em is not None else "")]
    if rng:
        L.append(f"· 예상 5일 범위: {rng} — 매매 계획은 이 밴드 안에서 잡는 게 좋아요.")
    L += ["", f"**방법 1 — 머신러닝 알고리즘" + (f" ({algo})" if algo else "") + "**",
          f"· 예측: {dir_ko}. 20년치 국내 시장 데이터로 학습한 모델이 가격·거래량·수급 등 19개 지표를 읽고 "
          f"확률이 기운 쪽이에요" + (f" — 백테스트 정확도 {acc}%라 '한 표'로 참고하세요." if acc is not None else "."),
          ((f"· 수치로는: 예상 5일 변동 ±{abs(em)}%" if em is not None else "· 예상 변동: 추정 불가")
           + (f", 가격으로 환산하면 대략 {rng} 범위예요." if rng else ".")
           + " 실제 가격이 이 밴드를 벗어나기 시작하면 뉴스·수급이 모델 예측을 앞지르고 있다는 신호예요.")]
    L += ["", "**방법 2 — 분석 (호가·수급·박스권)**",
          f"· 신호: {sig_ko}" + (f" — {reasons}." if reasons else ".")
          + " 호가창 잔량·외국인/기관 실시간 순매수·박스권 위치를 함께 점수로 합친, "
          "'지금 실제 돈이 어디로 움직이는지' 보는 방법이라 분위기가 바뀌면 가장 먼저 반응해요."]
    if buy_lo and sell_hi:
        L.append(f"· 이 방법의 매매 지도: 지지 ~{_w(buy_lo)} / 저항 ~{_w(sell_hi)} — 최근 지켜지는 범위예요. "
                 f"박스 안에서는 {_w(buy_lo)} 부근 매수, {_w(sell_hi)} 부근 익절이 기본이고 중간에서 쫓아가는 건 피해요.")
    if wv in ("BUY", "WATCH", "AVOID"):
        _ret = wave.get("retrace")
        L += ["", "**방법 3 — 파동 (엘리엇 · 피보나치)**",
              f"· 판단: {({'BUY':'매수','WATCH':'관망','AVOID':'회피'}).get(wv, wv)}"
              + (f" (파동점수 {wsc}" + (f" · 되돌림 {round(_ret*100)}%" if _ret is not None else "") + ")" if wsc is not None else "")
              + {"BUY": " — 강하게 오른 뒤 61.8~78.6% 피보나치 구간까지 깊게 눌린, 교과서적인 눌림목 자리예요.",
                 "WATCH": " — 상승 파동은 진짜인데(점수 0.65 이상이면 강한 파동) 아직 61.8~78.6% 매수 구간까지 눌리지 않았어요. 쫓아가지 않고 기다리는 중이에요.",
                 "AVOID": " — 상승 파동 자체가 약하거나 무너져서, 새 파동이 나올 때까지 한발 물러나 있어요."}.get(wv, "")]
        if wave.get("target"):
            L.append(f"· 이 방법의 지도: 상단 목표 {_f(wave['target'])}원"
                     + (f" · 깊은 눌림목 매수 {_f(wave.get('entry'))}원 부근" if wave.get("entry") else "")
                     + (f" · 손절 {_f(wave.get('stop'))}원" if wave.get("stop") else "")
                     + " — 가격이 '자기 자리'에 올 때만 매매하는 게 이 방법의 강점이에요.")
    if m4v:
        L += ["", "**방법 4 — 단타 사이클 (실시간 타이밍 · ±1%)**",
              f"· 지금: {({'BUY_NOW':'진입 타이밍','WAIT':'타이밍 대기','NO_SETUP':'셋업 없음'}).get(m4v, m4v)}"
              + (f" — 5분봉 RSI {m4.get('rsi_prev')}→{m4.get('rsi')}" if m4.get("rsi") is not None else "")
              + {"BUY_NOW": ". 단기 타이밍이 방금 저점에서 위로 돌아서서, 이 방법이라면 여기서 +1% 사이클을 노려요.",
                 "WAIT": ". 저점권 근처지만 RSI가 실제로 돌아서는 걸 확인해야 해요 — 바닥을 찍기보다 신호를 기다리는 방법이에요.",
                 "NO_SETUP": ". 눌림 자체가 없어서 쉬는 중이에요 — 강한 가격을 쫓아가는 건 이 방법이 가장 피하는 행동이거든요."}.get(m4v, ".")]
        if m4.get("target"):
            L.append(f"· 신호가 켜지면: 목표 +1% ({_f(m4['target'])}원) / 손절 −1% ({_f(m4['stop'])}원) / "
                     f"{m4.get('time_stop_min', 60)}분 안에 안 가면 소폭 정리 후 다음 파동 재진입 — 손실은 작게, 이익은 반복해서.")
        if m4.get("veto_ko"):
            L.append(f"· {m4['veto_ko']}")
    L.append("")
    L.append("**시나리오 — 중요한 두 가격**")
    if sell_hi:
        L.append(f"· 저항 ~{_w(sell_hi)}을 거래량 실리며 돌파하면 → 위쪽 시나리오가 열려요.")
    if buy_lo:
        L.append(f"· 지지 ~{_w(buy_lo)}이 깨지면 → 조정이 깊어질 수 있고, 파동 방법의 깊은 매수 구간이 살아나요.")
    # 최종 리마인드 (user ask): 설명이 끝난 뒤 결론을 한 번 더.
    L += ["", f"**최종 정리 — 다시 한 줄로:** {mood_ko}."
          + (f" 예상 범위는 {rng}," if rng else "")
          + (f" 위로는 저항 ~{_w(sell_hi)}" if sell_hi else "")
          + (f" · 아래로는 지지 ~{_w(buy_lo)}이 기준선이에요." if buy_lo else ".")]
    L += ["", "_전망(예측)일 뿐 매수/매도 권유가 아닙니다. 매매 판단은 \"살까?\"로 물어보세요._"]
    return "\n".join(L)


def _compose_final_answer(
    system: str,
    user_msg: str,
    tool_name: str,
    tool_result: dict,
    history: list[dict],
) -> str:
    """Second LLM turn: tool data → natural-language answer."""
    _u_has_ko = any(0xAC00 <= ord(c) <= 0xD7A3 for c in (user_msg or ""))
    _lang_line = (
        "반드시 한국어로만 답변하세요 (이전 대화 언어 무시).\n" if _u_has_ko
        else "Reply in English ONLY (ignore the language of earlier turns).\n"
    )
    # Honour the OUTPUT FORMAT the user asked for (table / list / prose).
    fmt_line, _max_tokens, _data_cap = _output_format_directive(user_msg)
    follow_system = (
        "You just called the tool '" + tool_name + "' and got back this result.\n"
        + _lang_line + fmt_line +
        "Use specific numbers/names from the tool result verbatim. "
        "If the result has ok=false or an error, apologize and explain briefly.\n"
        "Do NOT return JSON or code fences around a table — output the table/text "
        "directly. Do NOT add a 'summary for the boss' preamble."
    )
    # Carry the recent conversation so a follow-up keeps context (and the same depth)
    # instead of being answered in isolation.
    summary_messages = []
    for h in (history or [])[-4:]:
        role = "assistant" if (h.get("role") or h.get("sender")) in ("assistant", "ai", "bot") else "user"
        content = h.get("content") or h.get("text") or h.get("message") or ""
        if content:
            summary_messages.append({"role": role, "content": str(content)[:1200]})
    summary_messages += [
        {"role": "user", "content": f"My question: {user_msg}"},
        {"role": "user", "content": f"Tool '{tool_name}' returned:\n{json.dumps(tool_result, ensure_ascii=False, default=str)[:_data_cap]}"},
    ]
    try:
        reply = chat_completion_sync(
            system_prompt=follow_system,
            messages=summary_messages,
            max_tokens=max(_max_tokens, 650),   # same depth floor as the multi-tool path
            temperature=0.3,   # match the Stock backend (0.3) for consistent detail
            model="groq-llama-3.3-70b",
        )
        return (reply or "").strip() or "(no reply)"
    except Exception as e:
        log.warning(f"assistant_agent: compose failed: {e}")
        # Fall back to the message field if the tool had one
        return tool_result.get("message") or tool_result.get("summary") or "Done."


# ============================================================================
#  Public entry point
# ============================================================================

def _extract_attachment_text(filename: str, mime_type: str, blob: bytes) -> Optional[str]:
    """Extract readable text from an attached file. Returns None if the
    file type is binary-only (image / audio / unknown) — caller decides
    what to do (vision fallback, transcription, etc.).

    Supported:
      .xlsx / .xls         → openpyxl (via knowledge_ingest._parse_xlsx)
      .docx                → python-docx
      .pptx                → python-pptx
      .pdf                 → pypdf
      .csv                 → built-in csv module
      .txt / .md / .json   → utf-8 decode
      .hwp                 → olefile + PrvText stream (Korean Hangul docs)
    """
    name = filename.lower()
    try:
        if name.endswith((".xlsx", ".xls", ".xlsm")):
            from services.knowledge_ingest import _parse_xlsx
            chunks = _parse_xlsx(filename, blob)
            return "\n\n".join(c.get("content", "") for c in chunks)[:60000]
        if name.endswith(".docx"):
            from services.knowledge_ingest import _parse_docx
            chunks = _parse_docx(filename, blob)
            return "\n\n".join(c.get("content", "") for c in chunks)[:60000]
        if name.endswith(".pptx"):
            from services.knowledge_ingest import _parse_pptx
            chunks = _parse_pptx(filename, blob)
            return "\n\n".join(c.get("content", "") for c in chunks)[:60000]
        if name.endswith(".pdf"):
            from services.knowledge_ingest import _parse_pdf
            chunks = _parse_pdf(filename, blob)
            return "\n\n".join(c.get("content", "") for c in chunks)[:60000]
        if name.endswith(".csv"):
            from services.knowledge_ingest import _parse_csv
            chunks = _parse_csv(filename, blob)
            return "\n\n".join(c.get("content", "") for c in chunks)[:60000]
        if name.endswith((".txt", ".md", ".json", ".log")):
            return blob.decode("utf-8", errors="replace")[:60000]
        if name.endswith(".hwp"):
            # Hangul Word Processor (Korean). HWP is a compound document
            # format; the 'PrvText' stream is a UTF-16-LE preview that's
            # readable without licensed parsers.
            try:
                import olefile, io
                ole = olefile.OleFileIO(io.BytesIO(blob))
                if ole.exists("PrvText"):
                    raw = ole.openstream("PrvText").read()
                    return raw.decode("utf-16-le", errors="replace")[:60000]
                # Fallback: BodyText sections (less reliable but worth trying)
                if ole.exists("BodyText"):
                    raw = b""
                    for s in ole.listdir():
                        if s and s[0] == "BodyText":
                            raw += ole.openstream(s).read()
                    if raw:
                        return raw.decode("utf-16-le", errors="replace")[:60000]
            except ImportError:
                return "[HWP parser not installed — install 'olefile' on the orchestrator]"
            except Exception as e:
                return f"[Could not extract HWP text: {e}]"
        # Office legacy (.doc / .xls / .ppt) — would need antiword / xlrd /
        # python-pptx old format. Skip for now; report instead of crashing.
        if name.endswith((".doc", ".xls", ".ppt")):
            return f"[Legacy Office format {name.rsplit('.', 1)[-1]} — please re-save as the modern docx/xlsx/pptx format.]"
    except Exception as e:
        log.warning(f"_extract_attachment_text({filename}) failed: {e}")
        return f"[Could not extract text from {filename}: {e}]"
    return None  # Binary / unknown — caller handles


def _run_multimodal_path(
    transcript: str,
    lang: str,
    history: list[dict],
    attachment_ids: list[str],
) -> dict[str, Any]:
    """Handle Q&A about uploaded files of ANY supported type.

    Strategy:
      1. Load each attachment by id.
      2. For each:
         - text-extractable (xlsx/docx/pptx/pdf/csv/txt/md/json/hwp) →
           extract with _extract_attachment_text and inject as context.
         - image (image/*) or PDF → ALSO send raw bytes to vision so the
           LLM can see layout / charts / scanned content.
         - audio (audio/*) → transcribe via Whisper (Groq) first, then
           treat the transcript as text context.
      3. Compose final answer using the text-or-vision path with the
         best available provider (cascade).
    """
    from routers.chatbot import load_attachment
    from services.llm_client import gemini_multimodal_sync, chat_completion_sync
    import httpx as _httpx

    attachments: list[dict] = []
    for aid in attachment_ids:
        a = load_attachment(aid)
        if a:
            attachments.append(a)

    if not attachments:
        return {
            "intent": "multimodal_missing",
            "language": lang,
            "reply": ("첨부 파일을 찾을 수 없습니다 — 다시 업로드해 주세요."
                      if lang == "ko" else
                      "I couldn't find the attached file — please re-upload."),
            "action": None, "speak": True, "transcript": transcript,
            "tool_used": None,
        }

    # 1) Build a text-context block out of every attachment we can parse
    text_blocks: list[str] = []
    image_or_pdf: list[dict] = []
    for a in attachments:
        fn = a.get("filename") or ""
        mime = a.get("mime_type") or ""
        blob = a.get("bytes") or b""
        # Audio → transcribe and use the transcript as text
        if mime.startswith("audio/"):
            try:
                groq_key = os.getenv("GROQ_API_KEY", "")
                if groq_key:
                    resp = _httpx.post(
                        "https://api.groq.com/openai/v1/audio/transcriptions",
                        headers={"Authorization": f"Bearer {groq_key}"},
                        files={"file": (fn or "audio.webm", blob, mime)},
                        data={"model": "whisper-large-v3"},
                        timeout=90,
                    )
                    if resp.status_code == 200:
                        text = (resp.json().get("text") or "").strip()
                        if text:
                            text_blocks.append(f"[{fn} — transcribed audio]\n{text}")
                            continue
            except Exception as e:
                log.warning(f"audio transcribe ({fn}) failed: {e}")
                text_blocks.append(f"[{fn}: transcription failed]")
            continue
        # Image + PDF → keep for vision pass (in addition to text extract for PDF)
        if mime.startswith("image/"):
            image_or_pdf.append({"mime_type": mime, "bytes": blob, "filename": fn})
        # Try text extraction
        extracted = _extract_attachment_text(fn, mime, blob)
        if extracted and extracted.strip():
            text_blocks.append(f"[{fn} — extracted]\n{extracted}")
        elif mime == "application/pdf":
            # No text extracted from PDF (likely scanned) → vision fallback
            image_or_pdf.append({"mime_type": mime, "bytes": blob, "filename": fn})

    # 2) Compose the prompt
    sys = (
        "You are the VIP Assistant — the boss attached one or more files "
        "and is asking about them. Read the extracted text below carefully, "
        "quote specific numbers / names verbatim, and answer concretely "
        "(no 'I see a file…' filler). Reply in the SAME language the boss "
        "wrote in (Korean ↔ English). Keep it tight — 1-4 sentences unless "
        "they explicitly asked for detail."
    )
    user_text = transcript or (
        "이 파일에 대해 알려주세요." if lang == "ko" else "Tell me what's in this."
    )
    if history:
        recent = [h for h in history[-3:] if ((h.get("role") or h.get("who")) == "user")]
        if recent:
            prev = (recent[-1].get("content") or recent[-1].get("text") or "").strip()
            if prev:
                user_text = prev[:400] + "\n\n" + user_text

    context_block = ""
    if text_blocks:
        context_block = "\n\n===== ATTACHED FILE CONTENT =====\n" + "\n\n---\n".join(text_blocks) + "\n===== END =====\n"

    # 3) Choose path: vision (image + maybe text) OR pure text
    if image_or_pdf:
        # Vision path: pass image bytes alongside the extracted text. Gemini
        # is preferred but may be denied; fall back to OpenAI vision in the
        # multimodal helper itself when configured to.
        full_user = (context_block + "\n\n" if context_block else "") + user_text
        reply = gemini_multimodal_sync(
            system_prompt=sys,
            user_text=full_user,
            attachments=image_or_pdf,
            model="gemini-3.5-flash",   # cost guard: Flash reads images fine; Pro-preview is ~10x pricier
            max_tokens=800,
            temperature=0.4,
        )
        if reply.startswith("[LLM unavailable]") and context_block:
            # Vision dead — degrade gracefully to text-only on a working LLM
            log.warning("vision unreachable, falling back to text-only on attached extracts")
            reply = chat_completion_sync(
                system_prompt=sys + "\n\n(Note: vision is unavailable; answer from the extracted text only.)",
                messages=[{"role": "user", "content": full_user}],
                max_tokens=800,
                temperature=0.4,
            )
    else:
        # Pure text path — works on any LLM (Anthropic/OpenAI/Gemini/Groq/Ollama).
        # No vision needed, so we go through the standard cascade.
        full_user = (context_block + "\n\n" if context_block else "") + user_text
        reply = chat_completion_sync(
            system_prompt=sys,
            messages=[{"role": "user", "content": full_user}],
            max_tokens=800,
            temperature=0.4,
        )

    if isinstance(reply, str) and reply.startswith("[LLM unavailable]"):
        reason = reply.replace("[LLM unavailable]", "").strip(" :-")
        log.warning(f"assistant_agent: multimodal failed: {reply}")
        return {
            "intent": "multimodal_failed",
            "language": lang,
            "reply": (f"죄송합니다, 모델에 연결할 수 없습니다 — {reason}"
                      if lang == "ko" else
                      f"Sorry — model unreachable: {reason}"),
            "action": None, "speak": True, "transcript": transcript,
            "tool_used": None,
            "error_reason": reason,
        }

    return {
        "intent": "multimodal_answer",
        "language": lang,
        "reply": reply[:1500],
        "action": None, "speak": True, "transcript": transcript,
        "tool_used": "vision" if image_or_pdf else "file_text",
        "tool_result": {
            "attachment_count": len(attachments),
            "text_blocks": len(text_blocks),
            "images_or_pdf": len(image_or_pdf),
            "kinds": [a.get("kind") for a in attachments],
        },
    }


def _persist_assistant_turn(
    db: Session,
    user_id: str,
    user_text: str,
    assistant_reply: str,
    intent: Optional[str] = None,
    tool_used: Optional[str] = None,
) -> None:
    """Write the user-question + assistant-reply pair to the assistant's
    cross-session memory (chat_sessions + chat_messages with channel
    'assistant_overlay'). Used so the `recall_history` tool can answer
    'what did we discuss yesterday'-style questions.

    Best-effort — failures are swallowed; persistence is not on the
    critical path of returning a reply. Each user gets ONE rolling
    overlay session (channel='assistant_overlay'); messages append to it.
    """
    if not db or not user_id:
        return
    try:
        from db.models import ChatSession, ChatMessage
        session = (db.query(ChatSession)
                   .filter(ChatSession.user_id == user_id,
                           ChatSession.channel == "assistant_overlay")
                   .order_by(ChatSession.created_at.desc())
                   .first())
        if not session:
            session = ChatSession(
                user_id=user_id, channel="assistant_overlay",
                mode="llm", title="Assistant overlay history",
            )
            db.add(session)
            db.flush()  # need session.id for the messages below

        if user_text:
            db.add(ChatMessage(
                session_id=session.id, role="user", message_type="plain_text",
                content_json={"text": user_text[:2000]},
            ))
        if assistant_reply:
            db.add(ChatMessage(
                session_id=session.id, role="assistant", message_type="plain_text",
                content_json={
                    "text": assistant_reply[:2000],
                    "intent": intent,
                    "tool_used": tool_used,
                },
            ))
        db.commit()
    except Exception as e:
        log.info(f"assistant_agent: persist_turn skipped ({e})")


def _suggest_followups(
    tool_used: Optional[str],
    tool_result: Optional[dict],
    lang: str,
) -> list[str]:
    """Generate 2-3 short follow-up questions the user might want to ask
    next, based on the tool that just fired. Templated (deterministic, free).

    Returns up to 3 user-facing strings. The overlay renders these as
    clickable chips under the assistant bubble — clicking sends the chip
    text as the next query. Empty list → no chips shown.
    """
    if not tool_used:
        return []
    en = lang != "ko"
    # Pull useful entities out of the tool result for personalised chips
    name = None
    if isinstance(tool_result, dict):
        # `matches` is a list of dicts for twin tools but an int *count* for
        # others (e.g. asset_search → {"matches": 3, ...}). Only subscript it
        # when it's actually a non-empty list of dicts, else we'd crash with
        # "'int' object is not subscriptable".
        m = tool_result.get("matches")
        first_match_name = (
            m[0].get("name")
            if isinstance(m, list) and m and isinstance(m[0], dict)
            else None
        )
        name = (
            tool_result.get("twin_name")
            or tool_result.get("name")
            or first_match_name
        )

    def en_or_ko(en_text: str, ko_text: str) -> str:
        return en_text if en else ko_text

    # Tool-specific templates ---------------------------------------------
    if tool_used == "send_dm":
        return [
            en_or_ko(f"Show {name}'s recent activity", f"{name}의 최근 활동 보여줘") if name else en_or_ko("Show recent DMs", "최근 메시지 보여줘"),
            en_or_ko(f"What tasks does {name} have?", f"{name}의 작업은?") if name else en_or_ko("Unsend that message", "그 메시지 취소"),
            en_or_ko("Broadcast something to everyone", "전체에게 공지"),
        ]
    if tool_used == "search_twin" or tool_used == "list_twins":
        return [
            en_or_ko(f"Show {name}'s activity today" if name else "Show twin activity today",
                     f"오늘 {name} 활동" if name else "오늘 트윈 활동"),
            en_or_ko(f"List {name}'s tasks" if name else "List tasks",
                     f"{name} 작업 목록" if name else "작업 목록"),
            en_or_ko("Send a message to a twin", "트윈에게 메시지 보내"),
        ]
    if tool_used == "open_portal":
        portal = (tool_result or {}).get("agent") or "the agent"
        return [
            en_or_ko(f"What's the status of {portal}?", f"{portal} 상태는?"),
            en_or_ko("Show agent health", "에이전트 상태 보여줘"),
            en_or_ko("Open the agents list", "에이전트 목록 열어"),
        ]
    if tool_used == "navigate":
        return [
            en_or_ko("What can I do on this page?", "이 페이지에서 뭘 할 수 있어?"),
            en_or_ko("Go back", "뒤로"),
            en_or_ko("Show me the dashboard", "대시보드 보여줘"),
        ]
    if tool_used == "count":
        return [
            en_or_ko("List them with details", "자세한 목록"),
            en_or_ko("Which are active right now?", "지금 활성 상태?"),
            en_or_ko("Show today's activity", "오늘 활동 보여줘"),
        ]
    if tool_used in ("search_conversations", "conversation_history"):
        return [
            en_or_ko("Reply to this conversation", "이 대화에 답장"),
            en_or_ko("Mark as resolved", "해결됨으로 표시"),
            en_or_ko("Escalate it as urgent", "긴급으로 에스컬레이트"),
        ]
    if tool_used in ("latest_report", "search_reports", "trigger_daily_report"):
        return [
            en_or_ko("Compose a weekly report", "주간 리포트 생성"),
            en_or_ko("Email this to the team", "팀에게 이메일"),
            en_or_ko("Show the next report due", "다음 리포트 일정"),
        ]
    if tool_used == "agent_status":
        return [
            en_or_ko("Show all three agents' status", "세 에이전트 모두 상태"),
            en_or_ko("Ping every agent now", "모든 에이전트 핑"),
            en_or_ko("Open this agent's app", "이 에이전트 앱 열어"),
        ]
    if tool_used == "find_page":
        return [
            en_or_ko("Open it", "열어"),
            en_or_ko("What can I do there?", "거기서 뭐 할 수 있어?"),
            en_or_ko("Find something else", "다른 거 찾기"),
        ]
    if tool_used == "broadcast":
        return [
            en_or_ko("Show the broadcast history", "공지 기록 보기"),
            en_or_ko("Send a different message", "다른 메시지 보내"),
            en_or_ko("Schedule a daily summary", "일일 요약 예약"),
        ]
    if tool_used == "what_can_you_do":
        return [
            en_or_ko("Show today's situation", "오늘 상황 보여줘"),
            en_or_ko("How many twins do I have?", "트윈 몇 명?"),
            en_or_ko("Open the reports page", "리포트 페이지 열어"),
        ]
    # Generic fallback — works for any other read tool
    if tool_used != "[chain]":
        return [
            en_or_ko("Show me more detail", "자세히 보여줘"),
            en_or_ko("What else can you do?", "또 뭘 할 수 있어?"),
        ]
    return []


def _wanted_lang(language: Optional[str], transcript: Optional[str]) -> Optional[str]:
    """What language the ANSWER should be in: explicit param wins, else detect the Q."""
    l = (language or "auto").lower()
    if l.startswith("en"):
        return "en"
    if l.startswith("ko"):
        return "ko"
    h = sum(1 for c in (transcript or "") if 0xAC00 <= ord(c) <= 0xD7A3)
    a = sum(1 for c in (transcript or "") if "a" <= c.lower() <= "z")
    if h > 0:
        return "ko"
    return "en" if a >= 3 else None


def _enforce_reply_language(reply: str, language: Optional[str], transcript: Optional[str]) -> Optional[str]:
    """If the reply's language doesn't match what the user asked in, translate it.
    Only fires on a clear mismatch (so it costs nothing on normal turns)."""
    want = _wanted_lang(language, transcript)
    if not want or len(reply) < 15:
        return None
    h = sum(1 for c in reply if 0xAC00 <= ord(c) <= 0xD7A3)
    a = sum(1 for c in reply if "a" <= c.lower() <= "z")
    mismatch = (want == "en" and h > 12 and h > a) or (want == "ko" and h < 3 and a > 40)
    if not mismatch:
        return None
    tgt = "English" if want == "en" else "Korean"
    try:
        out = chat_completion_sync(
            system_prompt=(f"Translate the user's message into {tgt}. Keep ALL numbers, "
                           f"prices, tickers, %/원/₩, markdown (**, #, tables), emojis and line "
                           f"breaks EXACTLY. Output ONLY the translation, nothing else."),
            messages=[{"role": "user", "content": reply}],
            max_tokens=1200, temperature=0.0, model="groq-llama-3.3-70b",
        )
        return (out or "").strip() or None
    except Exception:
        return None


# --- Korean purity guard -----------------------------------------------------
# Groq's Llama 3.3 is the default model for short/normal turns and it intermittently
# emits Chinese words inside otherwise-fluent Korean ("某 주식의…", "材料:", "调味").
# _elaborate_answer already patched a handful of finance words locally; this is the
# shared version, applied to EVERY Korean reply. Cheap string swaps first; only if
# ideographs still remain do we pay for one rewrite call.
_CN_KO_MAP = (
    ("综合", "종합"), ("分析", "분석"), ("市场", "시장"), ("投资", "투자"),
    ("经济", "경제"), ("技术", "기술"), ("战略", "전략"), ("确认", "확인"),
    ("材料", "재료"), ("调味", "양념"), ("方法", "방법"), ("准备", "준비"),
    ("注意", "주의"), ("推荐", "추천"), ("参考", "참고"), ("价格", "가격"),
    ("股票", "주식"), ("收益", "수익"), ("风险", "리스크"), ("结论", "결론"),
    ("总结", "요약"), ("说明", "설명"), ("使用", "사용"), ("制作", "만들기"),
    ("时间", "시간"), ("温度", "온도"), ("需要", "필요"), ("选择", "선택"),
    ("适量", "적당량"), ("步骤", "순서"), ("提示", "팁"), ("某", "어떤"),
)
_HANJA_RE = _re.compile(r"[一-鿿]")


def _purify_korean(reply: str) -> Optional[str]:
    """Strip Chinese-character leakage out of a Korean reply. Returns the cleaned
    text, or None when nothing needed fixing."""
    if not reply or not _HANJA_RE.search(reply):
        return None
    out = reply
    for _cn, _ko in _CN_KO_MAP:
        out = out.replace(_cn, _ko)
    left = len(_HANJA_RE.findall(out))
    if left:
        # Unknown ideographs survived the map — one strict rewrite, numbers frozen.
        try:
            fixed = chat_completion_sync(
                system_prompt=("Rewrite the user's message in natural Korean using ONLY Hangul. "
                               "Replace every Chinese character with its Korean equivalent. Keep ALL "
                               "numbers, prices, tickers, %/원/₩, markdown (**, #, tables), emojis and "
                               "line breaks EXACTLY as they are. Output ONLY the rewritten text."),
                messages=[{"role": "user", "content": out}],
                max_tokens=1200, temperature=0.0, model="groq-llama-3.3-70b",
            )
            fixed = (fixed or "").strip()
            # Only accept the rewrite if it actually removed ideographs and didn't
            # collapse the answer (a truncated/garbage rewrite is worse than a leak).
            # And never when the provider handed back its '[LLM unavailable] …'
            # sentinel — that string is ideograph-free and long, so it sailed through
            # the length/hanja test and got shown to the user AS the answer.
            if (fixed and not fixed.startswith("[")
                    and len(_HANJA_RE.findall(fixed)) < left
                    and len(fixed) > len(out) * 0.6):
                out = fixed
        except Exception:
            pass
    return out if out != reply else None


_SUBQ_LEAD_RE = _re.compile(r"^\s*(그리고|그럼|또한|또|아울러|and also|and then|also|then|and)\b[\s,]*", _re.IGNORECASE)


def _split_subquestions(t: str) -> list[str]:
    """Split a compound question ('A? 그리고 B? 그리고 C?') into its sub-questions so EVERY
    part gets answered (the router otherwise picks one intent and drops the rest).
    Conservative: only splits on '?' boundaries; needs 2-4 substantive parts."""
    t = (t or "").strip()
    if t.count("?") < 2:
        return []
    out = []
    for p in t.split("?"):
        p = _SUBQ_LEAD_RE.sub("", p.strip()).strip(" ,.;·-")
        # hangul packs a question into fewer chars ('몇 주?') — 2 is already substantive
        if len(p) >= 4 or (len(p) >= 2 and any("가" <= ch <= "힣" for ch in p)):
            out.append(p + "?")
    return out if 2 <= len(out) <= 4 else []


_DETAIL_SECTION_RE = _re.compile(r"\n+\*\*(?:심층 해설|상세 설명|Deep dive|Detailed explanation)\*\*.*", _re.DOTALL)


# --- Conversational confirmation ('it means it decreased 5.59%?' / '그럼 떨어졌다는 거야?')
# A short follow-up that only wants the prior answer confirmed must be answered like a
# normal LLM chat — Yes/No + plain words from the conversation — not another data table
# (2026-07-08 boss feedback: 'sometimes the LLM answer is enough').
_CONFIRM_RE = _re.compile(
    r"^(so\b|it means|that means|you mean|does (that|it) mean|meaning\b)"
    r"|(am i right|is that (right|correct)|(,? ?right|,? ?correct|,? ?no)\s*\?+\s*$)"
    r"|^(그럼|그러면|그러니까|그 ?말은|즉)[ ,]"
    r"|(맞아|맞지|맞죠|맞나요|맞습니까|(라|다)는 (거야|거지|거네|건가요|뜻이야|뜻인가요|말이야)|란 뜻)\s*\??\s*$",
    _re.IGNORECASE)
# decisions/recommendations always go to the engine — never answered from memory
_CONFIRM_SKIP = ("살까", "사야", "팔까", "팔아야", "추천", "전망", "언제",
                 "should i", "recommend", "when will", "how many", "몇 주")

# My paper-desk portfolio — 'how many stock currently on the t(y)rade?' / '내가 산 종목
# 몇 개야?' must read the 모의투자 desk's REAL holdings, not fall into the picks scanner
# (2026-07-09 screenshot: 'how many stock' keyword hijacked an ownership question).
_PORTFOLIO_RE = _re.compile(
    r"(how many|which|what).{0,36}\b(i|we)('ve| have| am| are)?\s*(bought|hold|own|holding|has bought)"
    r"|\bmy (stocks?|positions?|portfolio|holdings?|trades?)\b|what do (i|we) (own|hold)"
    r"|did (i|we) buy|on the t[yh]?rade\b|paper (account|desk|portfolio)"
    r"|내(가)?.{0,8}(샀|산 |산\?|보유|가진|들고)|보유(한|중인|하고 있는)?\s*(종목|주식)|포트폴리오"
    r"|모의투자.{0,12}(보유|종목|몇|현황|얼마)|뭐\s*샀|몇\s*(종목|개).{0,12}(샀|보유|들고)"
    # 'Do I have a naver right now?' (boss 2026-08-26: the question 'do I OWN
    # this stock' was mis-routed into the full position-advice engine) — plain
    # ownership questions read the desk, they never ask the adviser
    r"|\bdo (i|we) (still )?(have|own|hold)\b|\bam i (still )?holding\b"
    r"|\bare we (still )?holding\b|\bhave (i|we) (got|bought)\b"
    r"|(가지고|갖고|들고)\s*있|보유\s*(중|하고\s*있|했)|(아직|지금).{0,10}(있어|있나|있니)",
    _re.IGNORECASE)


def _paper_portfolio_reply(db, lang: str, agent_id: str = "vip",
                           focus_text: str = "") -> Optional[str]:
    """The boss's 모의투자 desk holdings, live-priced — real numbers from paper_desk.state.
    If the question names a specific stock ('do I have a naver right now?'),
    the answer LEADS with yes/no for that stock (boss 2026-08-26)."""
    en = str(lang or "").lower().startswith("en")
    try:
        from services.paper_desk import state as _pd_state
        st = _pd_state(db)
    except Exception:
        return None
    poss = st.get("positions") or []
    rec = st.get("record") or {}
    _desk_url = "/testing" if str(agent_id) == "vip" else "https://oasisvip.vercel.app/testing"

    def _w(v):
        try:
            return f"{int(float(v)):,}"
        except Exception:
            return "-"
    L = []
    # named-stock lead: '예/아니요' first, list after
    if focus_text:
        try:
            from services.stock_resolver import resolve_one
            _fc9, _fn9 = resolve_one(focus_text)
        except Exception:
            _fc9, _fn9 = None, None
        if _fc9:
            _hit9 = next((p for p in poss
                          if str(p.get("ticker")) == str(_fc9)), None)
            if _hit9:
                if en:
                    L.append(f"**✅ Yes — you hold {_hit9.get('name')}: "
                             f"{_hit9.get('qty'):,} share(s) @ avg ₩{_w(_hit9.get('avg_price'))}"
                             + (f" · now ₩{_w(_hit9.get('live_price'))} · P&L {_hit9.get('unrealized_pnl_pct'):+.2f}%"
                                if _hit9.get("live_price") else "") + "**\n")
                else:
                    L.append(f"**✅ 네 — {_hit9.get('name')} 보유 중입니다: "
                             f"{_hit9.get('qty'):,}주 @ 평단 {_w(_hit9.get('avg_price'))}원"
                             + (f" · 현재가 {_w(_hit9.get('live_price'))}원 · 평가손익 {_hit9.get('unrealized_pnl_pct'):+.2f}%"
                                if _hit9.get("live_price") else "") + "**\n")
            else:
                L.append((f"**❌ No — you don't hold {_fn9 or _fc9} right now.**\n" if en
                          else f"**❌ 아니요 — {_fn9 or _fc9}은(는) 현재 보유하고 있지 않습니다.**\n"))
    if en:
        L.append(f"**🧾 Your paper-trading desk — {len(poss)} position(s) right now**")
        if not poss:
            L.append(f"\nNo stocks held at the moment — the account is 100% cash (₩{_w(st.get('cash'))}).")
        for p in poss:
            ln = f"- **{p.get('name')}** ({p.get('ticker')}): {p.get('qty'):,} shares @ avg ₩{_w(p.get('avg_price'))}"
            if p.get("live_price"):
                ln += (f" · now ₩{_w(p.get('live_price'))} · P&L {p.get('unrealized_pnl_pct'):+.2f}%"
                       f" (₩{_w(p.get('unrealized_pnl'))})")
            L.append(ln)
        oo = st.get("open_orders") or []
        if oo:
            L.append(f"- ⏳ Open limit orders waiting: {len(oo)}")
        L.append(f"\n**Account**: cash ₩{_w(st.get('cash'))} · positions ₩{_w(st.get('positions_value'))}"
                 f" · equity ₩{_w(st.get('equity'))} · total P&L {st.get('total_pnl_pct'):+.2f}%"
                 f" (₩{_w(st.get('total_pnl'))})")
        if rec.get("trades"):
            L.append(f"**Record**: {rec['trades']} closed trades · {rec.get('wins', 0)} wins"
                     + (f" · win rate {rec.get('win_rate')}%" if rec.get("win_rate") is not None else ""))
        L.append(f"\nFull desk (orders, history, charts): open [모의투자 테스트]({_desk_url}).")
    else:
        L.append(f"**🧾 모의투자 보유 현황 — 현재 {len(poss)}종목**")
        if not poss:
            L.append(f"\n현재 보유 종목이 없습니다 — 전액 현금({_w(st.get('cash'))}원) 상태입니다.")
        for p in poss:
            ln = f"- **{p.get('name')}** ({p.get('ticker')}): {p.get('qty'):,}주 @ 평단 {_w(p.get('avg_price'))}원"
            if p.get("live_price"):
                ln += (f" · 현재가 {_w(p.get('live_price'))}원 · 평가손익 {p.get('unrealized_pnl_pct'):+.2f}%"
                       f" ({_w(p.get('unrealized_pnl'))}원)")
            L.append(ln)
        oo = st.get("open_orders") or []
        if oo:
            L.append(f"- ⏳ 대기 중 지정가 주문: {len(oo)}건")
        L.append(f"\n**계좌**: 현금 {_w(st.get('cash'))}원 · 주식 평가 {_w(st.get('positions_value'))}원"
                 f" · 총자산 {_w(st.get('equity'))}원 · 누적 손익 {st.get('total_pnl_pct'):+.2f}%"
                 f" ({_w(st.get('total_pnl'))}원)")
        if rec.get("trades"):
            L.append(f"**전적**: 청산 {rec['trades']}건 · {rec.get('wins', 0)}승"
                     + (f" · 승률 {rec.get('win_rate')}%" if rec.get("win_rate") is not None else ""))
        L.append(f"\n주문·기록·차트 전체는 [모의투자 테스트]({_desk_url})에서 보실 수 있습니다.")
    return "\n".join(L)


# US-dollar prices always carry a live ₩ conversion (boss: 'answer in dollar and in the
# bracket show Korean won'). Uses the Naver USD/KRW rate cached in decision_agent.
def _append_krw_to_usd(text: str) -> str:
    if not text or "$" not in text:
        return text
    try:
        from services.decision_agent import _market_indicators
        fx = ((_market_indicators() or {}).get("usdkrw") or {}).get("price")
        rate = float(str(fx).replace(",", ""))
        if not (800 < rate < 3000):
            return text
    except Exception:
        return text
    out, last, n = [], 0, 0
    for m in _re.finditer(r"\$([\d,]+(?:\.\d+)?)", text):
        out.append(text[last:m.end()])
        last = m.end()
        tail = text[m.end():m.end() + 14]
        try:
            v = float(m.group(1).replace(",", ""))
        except Exception:
            v = None
        if v and v >= 0.5 and "₩" not in tail and "≈" not in tail and n < 6:
            out.append(f" (≈ ₩{int(round(v * rate)):,})")
            n += 1
    out.append(text[last:])
    return "".join(out)


# Korean companies with US-listed ADRs — 'SK Hynix ADR price?' must answer the US line,
# never the Korean listing dressed up as an ADR (2026-07-15 boss feedback).
# SK하이닉스 = SKHYV on NasdaqGS (verified via Yahoo symbol search; old OTC HXSCL is dead).
_ADR_US = {"000660": ("SKHYV", "NASDAQ"), "005930": ("SSNLF", "OTC"),
           "017670": ("SKM", "NYSE"), "030200": ("KT", "NYSE"),
           "105560": ("KB", "NYSE"), "055550": ("SHG", "NYSE"),
           "015760": ("KEP", "NYSE"), "005490": ("PKX", "NYSE"),
           "034220": ("LPL", "NYSE")}


def _us_quote(symbol: str) -> Optional[dict]:
    """Live US quote via Yahoo's chart API (no key). Returns price/prev/pct or None."""
    try:
        import httpx as _hx
        j = _hx.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
                    "?interval=1d&range=5d",
                    headers={"User-Agent": "Mozilla/5.0"}, timeout=10).json()
        m = j["chart"]["result"][0]["meta"]
        px = m.get("regularMarketPrice")
        prev = m.get("chartPreviousClose")
        if px is None:
            return None
        pct = ((float(px) / float(prev)) - 1) * 100 if prev else None
        return {"price": float(px), "prev": prev, "pct": pct,
                "exchange": m.get("fullExchangeName") or m.get("exchangeName")}
    except Exception:
        return None


# Plain LLM tasks — 'translate this to Korean: …', '요약해줘: …'. The text being worked on
# may CONTAIN trading words ('what should I buy?') — that must not fire the engines
# (2026-07-09 screenshot: a translation request got the 1-hour scanner answer).
_LLM_TASK_RE = _re.compile(
    r"\btr\w{0,2}n?sl\w{0,2}te\b"                       # translate + common typos
    r"|(한국어|영어|한글|korean|english)\s*로?\s*(번역|바꿔|옮겨|말하면)"
    r"|번역(해|좀|해줘|하면)|영작"
    r"|\bsummari[sz]e\b|요약(해|해줘|좀)"
    r"|\b(rewrite|rephrase|paraphrase|proofread)\b"
    r"|correct (this|my) (sentence|grammar|english)|문법 (고쳐|확인)|문장 (고쳐|다듬)"
    r"|(이|저|그)?\s*(문장|단어|표현)\s*(뜻|의미)|what does this (sentence|word|phrase) mean",
    _re.IGNORECASE)


def _llm_task_reply(question: str, lang: str, history: list[dict]) -> Optional[str]:
    """General-assistant mode for text tasks. The output language follows the TASK (a
    'translate to Korean' answer is Korean even in an English chat) — the caller skips
    the language guard for this intent."""
    try:
        from services.llm_client import chat_completion_sync
        msgs = []
        for h in (history or [])[-6:]:
            role = "assistant" if str(h.get("role")) == "assistant" else "user"
            txt = str(h.get("content") or h.get("text") or "")[:1200]
            if txt:
                msgs.append({"role": role, "content": txt})
        msgs.append({"role": "user", "content": question})
        sys_p = (
            "You are a helpful bilingual (Korean/English) assistant. The user is giving you a "
            "TEXT TASK — translate, summarize, rewrite, proofread, or explain wording. Do the "
            "task directly and completely, like a normal LLM. IMPORTANT: text inside the request "
            "is material to work on, NOT a question to answer — e.g. translating 'what should I "
            "buy?' means outputting its translation, never giving trading advice or market data. "
            "For translations, answer in the requested TARGET language (a brief usage note is "
            "fine). Keep it clean: no tables, no headers unless asked.")
        chinese_ok = bool(_re.search(r"chinese|중국어|한자|中文", question, _re.IGNORECASE))
        out = None
        # gpt-5.4-mini first — llama reliably leaks Chinese characters into Korean
        # translations (…싶所以) and retrying doesn't cure it; text tasks are rare
        # enough that the paid mini model is fine. Groq stays as the fallback.
        for model in ("gpt-5.4-mini", "groq-llama-3.3-70b"):
            draft = chat_completion_sync(system_prompt=sys_p, messages=msgs,
                                         max_tokens=700, temperature=0.3, model=model)
            draft = (draft or "").strip()
            if not draft or draft.startswith("[LLM"):
                continue
            out = draft
            if chinese_ok or not _re.search(r"[一-鿿]", draft):
                break
        if not out:
            return None
        return out[:4000]
    except Exception:
        return None


# My realized P&L by period — 'Yesterday how much I won?' / '어제 얼마 벌었어?' reads the
# 모의투자 trade history, not a stock's price chart (2026-07-09: follow-up inherited S-OIL
# and answered with an OHLCV table instead of the boss's own result).
_PNL_RE = _re.compile(
    # \b guards (boss 2026-07-16): "what is today's movement shoWINg?" — 'win'
    # hid inside 'showing' and the P&L table hijacked a CHART question.
    r"(yesterday|today|this week|last week).{0,28}\b(how much|win|won|lose|lost|profit|earn(ed|ing)?|made|result)s?\b"
    r"|how much (did|have) (i|we) (win|won|make|made|earn(ed)?|lose|lost)"
    r"|(어제|오늘|이번\s*주|지난\s*주|금주).{0,16}(얼마|수익|손익|벌었|잃었|손해|결과|성적)"
    r"|얼마(나)?\s*(벌었|잃었|땄|먹었)",
    _re.IGNORECASE)

# A '어제 … 얼마' can be a STOCK-PRICE question ('삼성전자 어제 종가는 얼마였어?'), NOT the
# desk's realized P&L. When a price-field word (종가/시가/현재가/close/price…) or a named
# stock is present WITHOUT any gain/loss verb, it's a market-data question and must yield
# to the price/history lane (boss 2026-07-29: the KO price question hit the 모의투자 P&L
# result while the identical EN question correctly returned the OHLCV table — KO/EN parity).
_PRICE_WORD_RE = _re.compile(
    r"종가|시가|고가|저가|현재가|주가|시세|얼마에"
    r"|\bclos(e|ed|ing)\b|\bopen(ing|ed)?\b|\bhigh\b|\blow\b|\bprice\b|\bquote\b",
    _re.IGNORECASE)
_PNL_VERB_RE = _re.compile(
    r"벌었|벌어|벌였|잃었|잃어|땄|먹었|손익|수익|손해|이익"
    r"|\b(profit|won|win|lost|lose|earn(ed|ing)?|made|make|gain(ed|s)?|result)s?\b",
    _re.IGNORECASE)


def _pnl_is_really_price(transcript: Optional[str]) -> bool:
    """True when a '어제/today … 얼마' actually asks a stock's PRICE (contains a price-field
    word or names a stock) with NO gain/loss verb — so the P&L lane yields to price/history."""
    t = transcript or ""
    if _PNL_VERB_RE.search(t):
        return False                      # explicit 벌었/잃었/profit → genuine P&L
    if _PRICE_WORD_RE.search(t):
        return True                       # 종가/시가/price/close … → a price question
    try:
        if _all_stocks_in_query(t):       # a named stock + bare 얼마 → that stock's price
            return True
    except Exception:
        pass
    return False


def _paper_pnl_reply(db, lang: str, transcript: str, agent_id: str = "vip") -> Optional[str]:
    """Realized P&L from the 모의투자 desk for the asked period (KST days). Direct answer
    first, then the closed trades; unrealized P&L noted separately — never mixed in."""
    en = str(lang or "").lower().startswith("en")
    try:
        from datetime import datetime, timedelta, date
        from zoneinfo import ZoneInfo
        from services.paper_desk import state as _pd_state
        kst = ZoneInfo("Asia/Seoul")
        today = datetime.now(kst).date()
        tl = (transcript or "").lower()
        if "yesterday" in tl or "어제" in tl:
            days, label_ko, label_en = {today - timedelta(days=1)}, "어제", "yesterday"
        elif "last week" in tl or "지난주" in tl or "지난 주" in tl:
            mon = today - timedelta(days=today.weekday() + 7)
            days = {mon + timedelta(days=i) for i in range(7)}
            label_ko, label_en = "지난주", "last week"
        elif "this week" in tl or "이번주" in tl or "이번 주" in tl or "금주" in tl:
            mon = today - timedelta(days=today.weekday())
            days = {mon + timedelta(days=i) for i in range((today - mon).days + 1)}
            label_ko, label_en = "이번 주", "this week"
        else:
            days, label_ko, label_en = {today}, "오늘", "today"

        st = _pd_state(db)

        def _kst_date(v) -> Optional[date]:
            try:
                if isinstance(v, datetime):
                    return (v.astimezone(kst) if v.tzinfo
                            else v.replace(tzinfo=ZoneInfo("UTC")).astimezone(kst)).date()
                s = str(v)[:19].replace("T", " ")
                return (datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("UTC"))
                        .astimezone(kst).date())
            except Exception:
                return None
        _desk_url = "/testing" if str(agent_id) == "vip" else "https://oasisvip.vercel.app/testing"
        sells, buys = [], 0
        for h in (st.get("history") or []):
            d = _kst_date(h.get("filled_at") or h.get("created_at"))
            if d not in days or h.get("status") != "FILLED":
                continue
            if h.get("side") == "SELL" and h.get("realized_pnl") is not None:
                sells.append(h)
            elif h.get("side") == "BUY":
                buys += 1
        total = round(sum(float(h["realized_pnl"]) for h in sells))
        upnl = sum(float(p.get("unrealized_pnl") or 0) for p in (st.get("positions") or []))

        def _w(v):
            return f"{int(float(v)):,}"
        L = []
        if en:
            if sells:
                verdict = ("you WON" if total > 0 else "you LOST" if total < 0 else "you broke even")
                L.append(f"**💰 {label_en.capitalize()}'s realized result: {'+' if total > 0 else ''}{_w(total)}원 "
                         f"({len(sells)} closed trade(s))** — {verdict} {label_en}.")
                for h in sells:
                    L.append(f"- {h.get('name')} · SELL {h.get('qty'):,} @ ₩{_w(h.get('fill_price'))} → "
                             f"{'+' if float(h['realized_pnl']) > 0 else ''}{_w(h['realized_pnl'])}원"
                             + (f" ({h.get('realized_pnl_pct'):+.2f}%)" if h.get("realized_pnl_pct") is not None else ""))
            else:
                L.append(f"**💰 {label_en.capitalize()}: no closed (sold) trades — realized P&L ₩0.**")
            if buys:
                L.append(f"- Buys {label_en}: {buys} order(s) filled (not counted until sold).")
            L.append(f"\nStill-open positions carry {'+' if upnl >= 0 else ''}{_w(upnl)}원 unrealized "
                     f"(separate from the number above). Cumulative desk P&L: {st.get('total_pnl_pct'):+.2f}% "
                     f"({_w(st.get('total_pnl'))}원). Details: [모의투자 테스트]({_desk_url}).")
        else:
            if sells:
                verdict = ("이겼습니다" if total > 0 else "잃었습니다" if total < 0 else "본전이었습니다")
                josa = "은" if label_ko == "오늘" else "는"
                L.append(f"**💰 {label_ko} 실현 손익: {'+' if total > 0 else ''}{_w(total)}원 "
                         f"(청산 {len(sells)}건)** — {label_ko}{josa} {verdict}.")
                for h in sells:
                    L.append(f"- {h.get('name')} · 매도 {h.get('qty'):,}주 @ {_w(h.get('fill_price'))}원 → "
                             f"{'+' if float(h['realized_pnl']) > 0 else ''}{_w(h['realized_pnl'])}원"
                             + (f" ({h.get('realized_pnl_pct'):+.2f}%)" if h.get("realized_pnl_pct") is not None else ""))
            else:
                L.append(f"**💰 {label_ko}는 청산(매도)한 거래가 없어 실현 손익이 0원입니다.**")
            if buys:
                L.append(f"- {label_ko} 매수 체결: {buys}건 (매도 전까지는 손익에 포함되지 않습니다).")
            L.append(f"\n보유 중 종목의 평가손익은 {'+' if upnl >= 0 else ''}{_w(upnl)}원으로 위 숫자와는 별도입니다. "
                     f"데스크 누적 손익: {st.get('total_pnl_pct'):+.2f}% ({_w(st.get('total_pnl'))}원). "
                     f"자세한 기록: [모의투자 테스트]({_desk_url}).")
        return "\n".join(L)
    except Exception:
        return None


# Context math — 'if I win 1% how much will I win?' after a calculation answer is plain
# arithmetic on the conversation's numbers, not a picks/recommendation ask (2026-07-09
# screenshot: the word 'win' dragged it into the 3-method scan).
_CHAT_MATH_RE = _re.compile(
    r"how much ((will|would|do|did|can) (i|we) )?(win|make|earn|lose|get|profit)"
    r"|how much (profit|loss|money)"
    r"|if (i|we) (win|make|lose|gain|earn)\b"
    r"|(얼마(를|나)?\s*(벌|이익|남|잃|손해))|(벌|잃)\s*수\s*있|수익.{0,6}얼마|얼마.{0,8}(벌|잃|손해)"
    r"|(이기면|따면|먹으면).{0,10}얼마",
    _re.IGNORECASE)
# a NAMED stock + a freshness word wants live data, not a memory answer
_CONFIRM_FRESH = ("지금", "현재", "실시간", " now", "current", "live")


def _confirm_wants_fresh_data(t: str) -> bool:
    tl = (t or "").lower()
    if not any(k in tl for k in _CONFIRM_FRESH):
        return False
    try:
        from services.stock_resolver import find_all
        return bool(find_all(t))
    except Exception:
        return False


def _confirm_is_topic_switch(t: str) -> bool:
    """'그럼 네이버는?' names a stock without stating any fact/number — that's a
    switch-stock data question (regression SWITCH-FU), not a confirmation."""
    if _re.search(r"\d", t or ""):
        return False
    try:
        from services.stock_resolver import find_all
        return bool(find_all(t))
    except Exception:
        return False


def _ctx_percent_math(question: str, lang: str, history: list[dict]) -> Optional[str]:
    """Deterministic percent-of-context-amount answer — LLM arithmetic is NOT trusted with
    money (live test: llama computed 2% of 22,460,000 as 448,200). Handles the common
    'if I win/lose N% how much?' case; anything fancier falls back to the LLM."""
    en = str(lang or "").lower().startswith("en")
    try:
        pcts = _re.findall(r"(\d+(?:\.\d+)?)\s*%", question or "")
        if len(pcts) != 1:
            return None
        pct = float(pcts[0])
        if not (0 < pct <= 100):
            return None
        base = None
        for h in reversed(history or []):
            if str(h.get("role")) != "assistant":
                continue
            txt = str(h.get("content") or h.get("text") or "")
            nums = [int(x.replace(",", "")) for x in _re.findall(r"\d[\d,]{4,}", txt)]
            nums = [n for n in nums if n >= 10_000]
            if nums:
                base = max(nums)          # the total is the largest figure in a calc answer
                break
        if not base:
            return None
        amt = round(base * pct / 100)
        lose = bool(_re.search(r"lose|loss|잃|손해|손실", question, _re.IGNORECASE))
        if en:
            line = f"₩{base:,} × {pct:g}% = **₩{amt:,}**."
            if lose:
                line += f" A {pct:g}% loss would leave ₩{base - amt:,}."
            else:
                net = round(base * max(pct - 0.25, 0) / 100)
                line += (f" So a +{pct:g}% win on ₩{base:,} makes ₩{amt:,} (total ₩{base + amt:,}); "
                         f"after ~0.25%p fees/tax the real take is about ₩{net:,}.")
            return line
        line = f"{base:,}원의 {pct:g}% = **{amt:,}원**입니다."
        if lose:
            line += f" {pct:g}% 손실이면 {base - amt:,}원이 남습니다."
        else:
            net = round(base * max(pct - 0.25, 0) / 100)
            line += (f" +{pct:g}% 이기면 수익 {amt:,}원 (총 {base + amt:,}원)이고, "
                     f"수수료·세금 ~0.25%p를 빼면 실수익은 약 {net:,}원입니다.")
        return line
    except Exception:
        return None


def _confirm_chat_reply(question: str, lang: str, history: list[dict],
                        allow_math: bool = False) -> Optional[str]:
    """Natural-chat confirmation grounded ONLY on the conversation. None → fall through.
    Confirmations: any number in the draft that isn't verbatim in the conversation triggers
    one strict retry, then a fall-through — the honesty rule beats conversational nicety.
    allow_math=True (context calculations like '1% of that = ?'): arithmetic on the
    conversation's numbers is allowed, shown as a formula."""
    en = str(lang or "").lower().startswith("en")
    try:
        from services.llm_client import chat_completion_sync
        msgs, ground = [], [question]
        for h in (history or [])[-8:]:
            role = "assistant" if str(h.get("role")) == "assistant" else "user"
            txt = str(h.get("content") or h.get("text") or "")[:1500]
            if txt:
                msgs.append({"role": role, "content": txt})
                ground.append(txt)
        msgs.append({"role": "user", "content": question})
        known = set(_re.findall(r"\d[\d,]*(?:\.\d+)?", " ".join(ground)))
        if allow_math:
            sys_p = (
                "You are the same friendly Korean-stocks assistant from this conversation. The "
                "user asks a small CALCULATION based on numbers already in this conversation. "
                "Answer like a normal chat: give the result with the formula in one line "
                "(e.g. ₩22,460,000 × 1% = ₩224,600), double-check the arithmetic digit by digit, "
                "then at most 2 short follow-up sentences (e.g. the after-fees figure ~0.25%p if "
                "relevant). The INPUT numbers must come from this conversation — if the base "
                "amount isn't there, ask which amount to use instead of guessing. No tables, no "
                "headers. Reply in " + ("ENGLISH" if en else "KOREAN (한국어)") + " only.")
        else:
            sys_p = (
                "You are the same friendly Korean-stocks assistant from this conversation. The user "
                "is asking you to CONFIRM or clarify something already discussed. Answer like a "
                "natural chat message: if it's a yes/no question, START with "
                + ("'Yes' or 'No'" if en else "'네' or '아니요'") + ", then explain in 1-3 short plain "
                "sentences. STRICT RULE: every number you write must appear VERBATIM in this "
                "conversation — do NOT compute, derive, or estimate any new number. A % change "
                "shown next to a price means change versus the previous close; trust the stated "
                "numbers instead of recalculating. If the conversation doesn't contain the needed "
                "fact, say so and suggest the exact question to ask instead. No tables, no headers, "
                "no bullet lists. Reply in " + ("ENGLISH" if en else "KOREAN (한국어)") + " only.")
        out = None
        for attempt in range(2):
            draft = chat_completion_sync(system_prompt=sys_p, messages=msgs,
                                         max_tokens=280, temperature=0.1 if allow_math else 0.2,
                                         model="groq-llama-3.3-70b")
            draft = (draft or "").strip()
            if not draft or draft.startswith("[LLM"):
                return None
            if allow_math:
                out = draft                      # derived numbers are the point here
                break
            invented = [x for x in _re.findall(r"\d[\d,]*(?:\.\d+)?", draft)
                        if x not in known and len(x.replace(",", "").replace(".", "")) > 1]
            if not invented:
                out = draft
                break
            msgs = msgs + [{"role": "assistant", "content": draft},
                           {"role": "user", "content":
                            f"Your draft used numbers not in our conversation ({', '.join(invented[:3])}). "
                            "Rewrite using ONLY numbers that appear verbatim above, or say the fact "
                            "isn't in the conversation."}]
        if not out:
            return None
        if not en:
            for _cn, _ko in (("综合", "종합"), ("分析", "분석"), ("市场", "시장"), ("投资", "투자")):
                out = out.replace(_cn, _ko)
        return out[:1200]
    except Exception:
        return None

# Bare follow-up sub-questions of a picks ask — must anchor on the #1 pick, not fall to a
# free LLM (2026-07-08 screenshots: 'How many?' → random tool dump, 'buying and selling
# time?' → generic textbook timetable, different per surface).
_HOWMANY_SUB_RE = _re.compile(
    r"^(and\s+|그리고\s*|그럼\s*)?(how (many|much)( stocks?| shares?)?|몇\s*주(나)?(\s*(살까|사야|사))?"
    r"|얼마나(\s*(살까|사야|사))?)\s*\??$", _re.IGNORECASE)
_TIMING_SUB_RE = _re.compile(
    r"(buy|sell|buying|selling|entry|exit).{0,26}(time|timing)|when to (buy|sell)"
    r"|buying and selling|(매수|매도|사고\s*파는|사는|파는)\s*(시간|타이밍|시점)|언제\s*(사|팔)",
    _re.IGNORECASE)


def _answer_multi_part(db, parts: list[str], language, current_path, selected_id,
                       history, forced_model, user_id, agent_id, page_context) -> dict[str, Any]:
    """Answer each sub-question independently and join them numbered — so '가격? 그리고
    과거? 그리고 살까?' answers ALL three (was: only the last). A part with no stock name
    inherits the last stock mentioned in an earlier part; after a picks part, bare
    'how many?' / 'when to buy·sell?' parts anchor on its #1 pick deterministically."""
    answers, carry, top, hist = [], None, None, list(history or [])
    scanned_empty = False
    _all_txt = " ".join(parts)
    en = (str(language or "").lower().startswith("en")
          or (str(language or "auto").lower() in ("auto", "")
              and not any("가" <= ch <= "힣" for ch in _all_txt)))
    for i, part in enumerate(parts, 1):
        q = part
        found = []
        try:
            found = _all_stocks_in_query(part)
            if found:
                carry = found[0][1] or found[0][0]
            elif carry:
                q = f"{carry} {part}"                    # inherit the stock for bare parts
        except Exception:
            pass
        sub = None
        # A picks part that (honestly) passed nothing leaves no #1 pick — the follow-ups
        # must stay deterministic instead of falling to the free LLM (textbook filler).
        if scanned_empty and not top and not found and _HOWMANY_SUB_RE.match(part.strip()):
            sub = ("Today no stock passed the entry filters, so there is no entry/stop price "
                   "to size against. The rule when a pick exists: shares = the smaller of what "
                   "your budget buys and the 1%-risk rule (one stop-out ≤ 1% of capital). "
                   "Name a stock — e.g. \"how many shares of Samsung Electronics with 5 million "
                   "won?\" — and I'll compute it now." if en else
                   "오늘은 기준을 통과한 종목이 없어 수량을 계산할 진입가·손절가가 없습니다. "
                   "종목이 있을 때의 규칙: 수량 = 자금으로 살 수 있는 최대치와 1%-리스크 룰(한 번의 "
                   "손절 손실 ≤ 자금의 1%) 중 작은 쪽입니다. \"삼성전자 500만원으로 몇 주?\"처럼 "
                   "종목을 지정해 주시면 바로 계산해 드립니다.")
        elif scanned_empty and not top and not found and _TIMING_SUB_RE.search(part):
            sub = ("**⏱ Market-wide timing (measured on our minute data)**: turn signals are most "
                   "reliable **09:00–10:00** (85% of detected turns were real) and least reliable "
                   "**14:00–15:00** (66% — many fake turns). Since no stock passed today's filters, "
                   "there is no per-stock rhythm to time — ask \"when will [name] turn up?\" for any "
                   "stock and I'll give its measured down/up rhythm and live position in the cycle."
                   if en else
                   "**⏱ 시장 공통 타이밍 (실측)**: 턴 신호는 **09~10시**가 가장 잘 맞고(감지된 턴의 "
                   "85%가 진짜), **14~15시**가 가장 안 맞습니다(66% — 가짜 턴 다수). 오늘은 기준 통과 "
                   "종목이 없어 종목별 리듬 타이밍을 드릴 수 없습니다 — \"[종목명] 언제 반등해?\"라고 "
                   "물어보시면 그 종목의 실측 하락/상승 리듬과 현재 위치를 바로 드립니다.")
        # 'How many?' after a picks part → real position sizing on the #1 pick.
        elif top and _HOWMANY_SUB_RE.match(part.strip()):
            try:
                from services.position_size import sizing_line
                _sl = sizing_line(db, transcript=_all_txt, user_key=user_id, lang="en" if en else "ko",
                                  entry=float(top["entry"]) if top.get("entry") else None,
                                  stop=float(top["stop"]) if top.get("stop") else None)
                if _sl:
                    _hd = (f"For the #1 candidate **{top.get('name')}** (entry ~₩{int(float(top['entry'])):,}):"
                           if en and top.get("entry") else
                           f"1번 후보 **{top.get('name')}** 기준 (진입가 ~{int(float(top['entry'])):,}원):"
                           if top.get("entry") else "")
                    _how = ("\n\n**How this is calculated**: share count = the smaller of what your "
                            "budget can buy and the 1%-risk rule (one stopped-out trade may cost at most "
                            "1% of your capital) — a tighter stop allows more shares, a wider stop fewer. "
                            "Fees + tax take ~0.25%p, so a +1% target nets ≈ +0.75%. For a different "
                            "amount just say e.g. \"with 10 million won\"." if en else
                            "\n\n**계산 방식**: 수량 = 자금으로 살 수 있는 최대치와 1%-리스크 룰(한 번의 "
                            "손절 손실이 자금의 1%를 넘지 않게) 중 작은 쪽입니다 — 손절 폭이 좁을수록 수량이 "
                            "늘고, 넓을수록 줄어듭니다. 수수료·세금 ~0.25%p를 빼면 목표 +1%의 실수익은 "
                            "≈ +0.75%입니다. 다른 금액 기준은 \"1,000만원으로\"처럼 말씀해 주세요.")
                    sub = ((_hd + _sl.strip("\n")).strip() + _how) or None
            except Exception:
                sub = None
        # 'buying and selling time?' → the measured turn-timing engine on the #1 pick.
        elif top and top.get("name") and not found and _TIMING_SUB_RE.search(part):
            q = (f"when should I buy {top['name']} and when to sell?" if en
                 else f"{top['name']} 언제 사야 하고 언제 팔아야 해?")
        if sub is None:
            try:
                r = _run_agent_impl(
                    db, transcript=q, language=language, current_path=current_path,
                    selected_id=selected_id, history=hist, confirmed_tool=None,
                    confirmed_args=None, attachment_ids=None, forced_model=forced_model,
                    user_id=user_id, agent_id=agent_id, page_context=page_context)
                sub = str(r.get("reply") or "").strip()
                if isinstance(r, dict) and r.get("top_pick"):
                    top = r["top_pick"]
                    carry = carry or top.get("name")
                elif isinstance(r, dict) and r.get("tool_used") in ("buy_picks", "scalp_watchlist"):
                    scanned_empty = True                 # honest empty scan — no anchor pick
            except Exception as e:
                sub = f"(error: {str(e)[:60]})"
        if len(parts) >= 3 and i > 1:                    # keep combined answer readable —
            sub = _DETAIL_SECTION_RE.sub("", sub)        # part 1 keeps its deep dive
        if len(sub) > 3600:                              # cut on a line, not mid-word
            sub = sub[:3600].rsplit("\n", 1)[0] + (
                "\n… _(ask this part alone for the full detail)_" if en
                else "\n… _(이 질문만 따로 물어보시면 전체 상세를 드립니다)_")
        answers.append(f"**{i}. {part}**\n{sub}")
        hist = hist + [{"role": "user", "content": q}, {"role": "assistant", "content": sub[:400]}]
    return {"intent": "multi_part", "language": language,
            "reply": "\n\n---\n\n".join(answers)[:9000],
            "action": None, "speak": True, "transcript": " ".join(parts),
            "tool_used": "multi_part"}


# ===== ✏️ UNIVERSAL SPELL NORMALIZATION (boss 2026-08-26: "it should not care
# spelling error, like we use normal LLM") — one layer before routing, so EVERY
# keyword lane tolerates typos instead of each learning them one by one. Only
# high-confidence corrections (≥0.8) of unknown 4-12 letter words into the trading
# vocabulary; stock-name-ish tokens are left for the resolver's own fuzzy. =====
_SPELL_VOCAB = (
    "buy", "sell", "stock", "stocks", "share", "shares", "price", "prices", "order",
    "orders", "cancel", "yesterday", "today", "tomorrow", "volume", "chart", "checklist",
    "recommend", "recommendation", "candidate", "candidates", "profit", "holding",
    "waiting", "market", "should", "would", "could", "want", "think", "minimum",
    "maximum", "open", "close", "high", "low", "news", "score", "history", "month",
    "months", "week", "weeks", "days", "current", "hold", "wait", "which", "what",
    "much", "many", "good", "best", "queue", "limit", "trade", "trading", "bought",
    "sold", "please", "again", "change", "changes", "money", "increase", "decrease",
)
_SPELL_SET = frozenset(_SPELL_VOCAB)


def _spell_normalize(q: Optional[str]) -> Optional[str]:
    if not q or not _re.search(r"[a-zA-Z]", q):
        return q
    import difflib
    out = []
    changed = False
    for tok in _re.split(r"([^A-Za-z]+)", q):
        tl = tok.lower()
        if tok.isalpha() and tok.isascii() and 4 <= len(tok) <= 12 and tl not in _SPELL_SET:
            m = difflib.get_close_matches(tl, _SPELL_VOCAB, n=1, cutoff=0.75)
            # 0.75-0.8 band only for pure transpositions ('waht'→'what', 'mnay'→'many'):
            # same letters, same length. 'tell' can never become 'sell' this way.
            if m and difflib.SequenceMatcher(None, tl, m[0]).ratio() < 0.8 \
                    and sorted(tl) != sorted(m[0]):
                m = None
            if m:
                try:                      # never "correct" a stock name/alias
                    from services import stock_resolver as _sr9
                    _sr9._build()
                    if tl in _sr9._ALIAS or difflib.get_close_matches(
                            tl, list(_sr9._ALIAS.keys()), n=1, cutoff=0.8):
                        out.append(tok)
                        continue
                except Exception:
                    pass
                out.append(m[0])
                changed = True
                continue
        out.append(tok)
    return "".join(out) if changed else q


# 💡 FOLLOW-UP OFFERS (boss 2026-08-26: "add some techniques that it can follow with
# people, like 'would you like to do this?'") — data answers end with a natural next
# step. Lanes that already offer (advice, orders, reco) are left alone.
_FOLLOWUP_BY_INTENT = {
    "stock_history": ("원하시면 다른 기간이나 다른 종목도 바로 보여드릴게요 — 예: \"삼성전자 최근 3개월\".",
                      "Want another period or stock? Just say e.g. \"samsung last 3 months\"."),
    "stock_period_stats": ("다른 기간·종목이 필요하시면 말씀만 하세요 — 예: \"1년치 SK하이닉스\".",
                           "Need a different window or stock? e.g. \"skhynix last 1 year\"."),
    "stock_fundamentals": ("목표가·배당·PER 등 더 궁금한 항목을 이어서 물어보세요.",
                           "Ask for more — e.g. \"target price?\" or \"dividend?\"."),
    "top_movers": ("이 중 하나 판단해 드릴까요? — 예: \"1등 살까?\"",
                   "Want a verdict on one of these? e.g. \"should I buy the top one?\""),
    "stock_why_move": ("지금 사도 될지 판단해 드릴까요? \"살까?\"라고 물어보세요.",
                       "Want a buy verdict on it? Just ask \"should I buy it?\""),
    "desk_pnl": ("종목별 상세는 \"오늘 챗봇으로 뭐 샀지?\"라고 물어보시면 됩니다.",
                 "For the detail, ask \"what did I buy today?\""),
    "market_direction": ("오늘의 추천이 궁금하시면 \"3종목 추천해줘\"라고 말씀하세요.",
                         "Want today's picks? Say \"recommend 3 stocks\"."),
}
_FOLLOWUP_MARKERS = ("도와드릴까요", "원하시면", "말씀하세요", "물어보세요", "드릴게요", "볼까요",
                     "Want me", "Want another", "Want a", "Would you like", "Just say",
                     "Just ask", "Say \"", "e.g. \"")


def _append_followup(result: dict, transcript: Optional[str] = None) -> None:
    try:
        it = result.get("intent")
        rep = result.get("reply") or ""
        if it not in _FOLLOWUP_BY_INTENT or not rep or len(rep) < 30:
            return
        if any(m in rep for m in _FOLLOWUP_MARKERS):
            return
        ko, en = _FOLLOWUP_BY_INTENT[it]
        # language by the QUESTION (the EN answer's '자체 DB' footer has Hangul and
        # fooled a reply-based check)
        _q = transcript or ""
        _is_en = bool(_re.search(r"[a-zA-Z]", _q)) and not _re.search(r"[가-힣]", _q)
        result["reply"] = rep.rstrip() + "\n\n💡 " + (en if _is_en else ko)
    except Exception:
        pass


def run_agent(
    db: Session,
    transcript: str,
    language: str = "auto",
    current_path: Optional[str] = None,
    selected_id: Optional[str] = None,
    history: Optional[list[dict]] = None,
    confirmed_tool: Optional[str] = None,
    confirmed_args: Optional[dict] = None,
    attachment_ids: Optional[list[str]] = None,
    forced_model: Optional[str] = None,
    user_id: Optional[str] = None,
    agent_id: str = "vip",
    page_context: Optional[str] = None,
) -> dict[str, Any]:
    """Public entry — wraps the actual implementation with cross-session
    memory persistence (writes each turn to chat_sessions/chat_messages
    under channel='assistant_overlay' so recall_history can find it
    later). Persistence is best-effort and never blocks the response."""
    # ✏️ one spell pass before anything reads the text (boss 2026-08-26)
    transcript = _spell_normalize(transcript) or transcript
    # MULTI-PART: 'A? 그리고 B? 그리고 C?' → answer EVERY sub-question (was: only the last).
    # BUT a single PREDICTION question restated with two '?' ('what will price be tomorrow?
    # How many percent up or down?') is ONE question — don't split it, or it misses the
    # prediction-interval lane (boss 2026-07-20). Same for the future-prediction phrasing.
    _parts = ([] if (confirmed_tool or attachment_ids or _is_future_prediction(transcript))
              else _split_subquestions(transcript))
    # COMPOUND why+advice with a single '?': 'Why skhynix increased 11% and should I buy
    # or hold?' — the '?'-based splitter can't see it, so only the advice part answered
    # (2026-07-10 boss feedback). Split at the advice clause when a why-cue precedes it.
    if not _parts and not (confirmed_tool or attachment_ids):
        _t = (transcript or "").strip()
        if _re.search(r"\b(why|what caused)\b|왜|어째서|무슨 이유", _t, _re.IGNORECASE):
            _m = _re.search(
                r"(?:,?\s+(?:and|so)\s+|그리고\s+|그런데\s+|는데\s*|니까\s+|,\s*)"
                r"((?:should|shall|do|would)\s+i\b.{0,60}|is it (?:a )?good (?:time )?to buy.{0,40}"
                r"|buy or hold.{0,20}|hold or sell.{0,20}"
                r"|(?:지금|오늘|이제|저는|나는|난)?\s*(?:살까(?:요)?.{0,20}|사야\s*(?:해|할까|하나).{0,16}"
                r"|팔까.{0,16}|보유할까.{0,16}|어떡해.{0,10}))\s*\??$",
                _t, _re.IGNORECASE)
            if _m and _m.start() > 8:
                _why = _t[:_m.start()].strip(" ,.;·-")
                _adv = _m.group(1).strip(" ,.;·-")
                if len(_why) >= 8 and len(_adv) >= 4:
                    _parts = [_why.rstrip("?") + "?", _adv.rstrip("?") + "?"]
    if _parts:
        result = _answer_multi_part(db, _parts, language, current_path, selected_id,
                                    history, forced_model, user_id, agent_id, page_context)
    else:
        result = _run_agent_impl(
            db, transcript=transcript, language=language,
            current_path=current_path, selected_id=selected_id, history=history,
            confirmed_tool=confirmed_tool, confirmed_args=confirmed_args,
            attachment_ids=attachment_ids, forced_model=forced_model,
            user_id=user_id, agent_id=agent_id,
            page_context=page_context,
        )
    # $ → (₩) — any dollar price in the answer carries a live-KRW conversion (skip
    # translations: converting inside translated text would corrupt the task output).
    try:
        if (isinstance(result, dict) and result.get("reply")
                and result.get("intent") != "llm_task" and "$" in str(result["reply"])):
            result["reply"] = _append_krw_to_usd(str(result["reply"]))
    except Exception:
        pass
    # 💡 a natural next-step offer on data answers (boss 2026-08-26)
    if isinstance(result, dict):
        _append_followup(result, transcript)
    # LANGUAGE GUARD — English question MUST get an English answer (and vice versa).
    # Catches the case where a delegated (stock-backend) reply comes back in Korean.
    # Skipped for llm_task: 'translate to Korean' answers ARE Korean on purpose.
    try:
        # chat_trade previews/fills are deterministic order text — an LLM translation
        # pass mangled one ('매수' → '구매', reworded quantities; 2026-08-26). Money
        # text never rides through a translator.
        if isinstance(result, dict) and result.get("reply") \
                and result.get("intent") not in ("llm_task", "chat_trade", "chat_trade_confirm"):
            fixed = _enforce_reply_language(str(result["reply"]), language, transcript)
            if fixed:
                result["reply"] = fixed
            # A Korean answer must be Korean all the way through — no leaked 漢字.
            if _wanted_lang(language, transcript) == "ko":
                pure = _purify_korean(str(result["reply"]))
                if pure:
                    result["reply"] = pure
    except Exception as _e:
        log.warning(f"language guard skipped: {str(_e)[:120]}")
    # SPEED (boss 2026-07-27: "reply must be very fast"): cross-session memory
    # persistence + self-improvement logging are best-effort DB writes (a SELECT,
    # a flush and several INSERTs, all cross-region) that USED to run synchronously
    # here — BEFORE the already-computed reply was returned, adding ~1s+ of latency
    # the user felt on every single message. The reply is done; ship it now and
    # write afterwards on a background thread (its OWN DB session — the request's
    # `db` may be closed by the time this runs).
    _final_reply = str(result.get("reply") or "")
    _final_intent = result.get("intent")
    _final_tool = result.get("tool_used")
    _final_needs_llm = bool(result.get("needs_llm"))

    def _persist_and_learn_bg() -> None:
        from db.base import SessionLocal
        db_bg = SessionLocal()
        try:
            # 1. Cross-session memory (so recall_history can find this turn later).
            skip_intents = {"empty", "multimodal_failed", "multimodal_missing", "chain_empty"}
            if user_id and _final_intent not in skip_intents and _final_reply:
                _persist_assistant_turn(
                    db_bg, user_id=user_id, user_text=transcript or "",
                    assistant_reply=_final_reply, intent=_final_intent, tool_used=_final_tool,
                )
            # 2. Self-improvement instrumentation (#12 + #13 + #15): log the turn and,
            #    if low-confidence, kick off background web research.
            skip = {"empty", "multimodal_failed", "multimodal_missing", "chain_empty", "error"}
            if (transcript or "").strip() and _final_intent not in skip and _final_reply:
                from services.assistant_learning import is_low_confidence, log_qa
                low = is_low_confidence(_final_reply) or _final_needs_llm
                log_qa(db_bg, agent_id=agent_id, question=transcript or "", answer=_final_reply,
                       intent=_final_intent, tool_used=_final_tool, low_conf=low, user_id=user_id)
                if low and not confirmed_tool:
                    _spawn_background_research(agent_id, transcript or "")
        except Exception as _e:
            log.warning(f"persist/learn (bg) skipped: {str(_e)[:120]}")
        finally:
            db_bg.close()

    try:
        _persist_executor.submit(_persist_and_learn_bg)
    except Exception as _e:
        # Executor unavailable/saturated → do it inline (correctness over speed).
        log.warning(f"persist/learn: bg submit failed, running inline: {str(_e)[:120]}")
        _persist_and_learn_bg()

    return result


# Bounded background-research executor. Previously every low-confidence reply
# spawned an unbounded daemon thread, each opening its own DB session — a burst
# of "I don't know" answers could exhaust the connection pool and starve the
# request path. Now: at most 2 concurrent research jobs (max_workers), a small
# queue cap, and an in-flight set so the SAME question isn't researched twice
# concurrently.
import threading as _threading
from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor

_research_executor = _ThreadPoolExecutor(max_workers=2, thread_name_prefix="research")
_research_inflight: set[str] = set()
_research_lock = _threading.Lock()
_RESEARCH_MAX_INFLIGHT = 8  # hard cap on queued+running jobs

# Off-critical-path writer for turn persistence + QA logging (see run_agent).
# Bounded worker pool so a burst of chat can't exhaust the DB connection pool;
# each job opens and closes its own short-lived session.
_persist_executor = _ThreadPoolExecutor(max_workers=2, thread_name_prefix="persist")


def _spawn_background_research(agent_id: str, question: str) -> None:
    """Research a low-confidence question off the request path, bounded + deduped.
    Drops the job (rather than piling up) if too many are already in flight."""
    key = f"{agent_id}::{(question or '').strip().lower()[:200]}"
    with _research_lock:
        if key in _research_inflight:
            return  # already researching this exact question
        if len(_research_inflight) >= _RESEARCH_MAX_INFLIGHT:
            return  # backpressure — skip rather than exhaust resources
        _research_inflight.add(key)

    def _work():
        try:
            from db.base import SessionLocal
            from services.assistant_learning import research_and_learn
            db2 = SessionLocal()
            try:
                research_and_learn(db2, agent_id=agent_id, question=question)
            finally:
                db2.close()
        except Exception as e:
            log.warning(f"background research failed: {str(e)[:120]}")
        finally:
            with _research_lock:
                _research_inflight.discard(key)

    try:
        _research_executor.submit(_work)
    except Exception as e:
        with _research_lock:
            _research_inflight.discard(key)
        log.warning(f"could not submit research job: {str(e)[:120]}")


# ---------------------------------------------------------------------------
#  No-LLM (offline) answer path
# ---------------------------------------------------------------------------

_NAV_VERBS = ("open", "go to", "take me", "navigate", "show me",
              "열어", "열어줘", "이동", "가줘", "보여줘")

import re as _re


def _extract_clean_answer(content: str) -> str:
    """Pull a presentable answer out of a KB chunk.

    Learned/self-improvement notes are markdown with structured markers
    (**Good answer:** / **Correct answer / behaviour:** / **Answer:**) — return
    just that answer text. For ordinary uploaded chunks, strip parser noise
    like a leading '[Sheet: …]' tag and the '# heading' line so the user sees
    clean prose, not a raw dump.
    """
    if not content:
        return ""
    text = content.strip()
    # 1. Learned-note answer markers (most authoritative — these are verified).
    for marker in ("**Good answer:**", "**Correct answer / behaviour:**",
                   "**Answer:**", "**Correct answer:**"):
        idx = text.find(marker)
        if idx >= 0:
            ans = text[idx + len(marker):].strip()
            # cut at the next markdown marker / source footer if present
            ans = _re.split(r"\n\s*(?:\*\*|_Sources?:|_\(Learned)", ans)[0].strip()
            if ans:
                return ans
    # 2. Ordinary chunk — drop a leading [Sheet: …] tag and markdown headings.
    text = _re.sub(r"^\[[^\]]+\]\s*", "", text)           # leading [Sheet: X]
    text = _re.sub(r"(?m)^#{1,6}\s.*$", "", text).strip()  # markdown headings
    return text


def _offline_basic_answer(qlc: str, lang: str, agent_id: str) -> Optional[str]:
    """Answer everyday small-talk / capability questions WITHOUT an LLM.

    These are the questions a user expects ANY assistant to handle even in
    offline mode — greetings, thanks, "what can you do", goodbye. Returning a
    friendly canned reply here (instead of "I couldn't find this") makes the
    offline mode feel usable. Matched on whole words so "this" doesn't trip
    the "hi" greeting.
    """
    words = set(_re.findall(r"[a-z0-9가-힣]+", qlc))
    has_hangul = any("가" <= ch <= "힣" for ch in qlc)
    ko = lang == "ko" or has_hangul

    def any_word(*ws: str) -> bool:
        return any(w in words for w in ws)

    def any_sub(*ss: str) -> bool:
        return any(s in qlc for s in ss)

    # Greeting
    if (any_word("hi", "hello", "hey", "hiya", "yo", "하이", "헬로", "안녕")
            or any_sub("안녕하", "good morning", "good afternoon", "good evening", "반가")):
        return ("안녕하세요! 무엇을 도와드릴까요? 메뉴 이동, 업로드된 자료나 현재 화면 내용을 도와드릴 수 있어요."
                if ko else
                "Hi! How can I help? I can open menus for you and answer from your uploaded knowledge and the current page.")
    # Thanks
    if any_word("thanks", "thank", "thx", "감사", "고마워", "고맙") or any_sub("thank you", "감사합니다", "고맙습니다"):
        return ("천만에요! 더 도와드릴 일이 있을까요?" if ko else "You're welcome! Anything else I can help with?")
    # How are you
    if any_sub("how are you", "how's it going", "how is it going", "잘 지내", "잘지내", "어떻게 지내"):
        return ("잘 지내고 있어요, 감사합니다! 무엇을 도와드릴까요?" if ko else "I'm doing well, thanks! What can I help you with?")
    # Goodbye
    if any_word("bye", "goodbye", "ㅂㅂ", "잘가") or any_sub("see you", "안녕히", "잘 가"):
        return ("안녕히 가세요! 필요하면 언제든 불러주세요." if ko else "Goodbye! Call me anytime you need help.")
    # Menu / page listing — "what menus / pages / features do I have?".
    # Skip when there's a navigation verb (that's an "open X" command, handled
    # by the navigation step) so "open the agents menu" still navigates.
    nav_in_q = any_sub("open", "go to", "navigate", "show me", "열어", "이동", "가줘", "보여줘")
    if (not nav_in_q and (
            any_word("menu", "menus", "메뉴", "page", "pages", "페이지", "sections", "tabs")
            or any_sub("what menu", "which menu", "list menu", "메뉴 목록", "어떤 메뉴",
                       "what do i have", "what features", "어떤 기능", "메뉴 알려"))):
        try:
            from services.assistant_agent import AGENT_PROFILES
            prof = AGENT_PROFILES.get((agent_id or "").lower())
            items = []
            if prof:
                for entry in prof.get("pages", []):
                    path, _, lab = str(entry).partition(" — ")
                    path = path.strip()
                    lab = lab.strip()
                    if path:
                        items.append((path, lab or path))
            else:
                from services.assistant_manifest import get_all_pages
                for p in get_all_pages(include_hidden=False):
                    items.append((p["path"], p.get("name") or p["path"]))
            if items:
                body = "\n".join(f"• {lab} — {path}" for path, lab in items[:20])
                return ((f"사용 가능한 메뉴입니다 (이동하려면 \"열어 [메뉴]\"라고 하세요):\n\n{body}")
                        if ko else
                        (f"Here are your menus (say \"open [menu]\" to go there):\n\n{body}"))
        except Exception:
            pass
    # Capabilities / identity / help
    if (any_word("help", "도와줘", "도와", "기능", "누구", "뭐야", "capabilities")
            or any_sub("what can you do", "who are you", "what are you", "무엇을 도와",
                       "뭐 할 수", "무엇을 할 수", "어떤 걸 할 수", "사용법", "what do you do")):
        pages_hint = ""
        try:
            from services.assistant_agent import AGENT_PROFILES
            prof = AGENT_PROFILES.get((agent_id or "").lower())
            if prof:
                labels = []
                for entry in prof.get("pages", [])[:6]:
                    _p, _, lab = str(entry).partition(" — ")
                    if lab.strip():
                        labels.append(lab.strip())
                if labels:
                    pages_hint = ("\n• 이동 가능한 메뉴: " if ko else "\n• Menus I can open: ") + ", ".join(labels)
        except Exception:
            pass
        if ko:
            return ("저는 오프라인(LLM 미사용) 모드에서도 다음을 할 수 있어요:\n"
                    "• 메뉴 열기/이동\n"
                    "• 업로드한 지식자료에서 찾아 답변\n"
                    "• 지금 보고 있는 화면 내용 안내\n"
                    "• 이전에 학습한(👍) 답변 제공"
                    + pages_hint +
                    "\n\n복잡한 분석·요약·작성은 LLM을 켜시면 가능합니다.")
        return ("Even in offline (no-LLM) mode I can:\n"
                "• Open / navigate menus\n"
                "• Answer from your uploaded knowledge\n"
                "• Explain what's on the current screen\n"
                "• Give answers I've previously learned (👍)"
                + pages_hint +
                "\n\nFor deeper reasoning, summaries or drafting, turn the LLM on.")
    return None


def _offline_answer(db, *, transcript: str, lang: str, agent_id: str,
                    page_context: Optional[str], kb_context) -> dict[str, Any]:
    """Answer WITHOUT calling any LLM — knowledge base + current page only.

    1. Everyday small-talk / capability questions → friendly canned reply.
    2. If it's a navigation command (open/go to/열어 + a page name) → navigate.
    3. Else if the KB or the page has a relevant snippet → return it.
    4. Else → tell the user this needs the AI (LLM) turned on.

    Pure string/embedding matching; private, instant, no network LLM call.
    """
    q = (transcript or "").strip()
    qlc = q.lower()

    def _frame(reply: str, action=None, intent="offline"):
        return {
            "intent": intent, "language": lang if lang in ("ko", "en") else "en",
            "reply": reply, "action": action, "speak": True,
            "transcript": q, "tool_used": None, "tool_result": None,
            "offline": True,
        }

    if not q:
        return _frame("무엇을 도와드릴까요?" if lang == "ko" else "How can I help?")

    # --- 0. Everyday small-talk / capability questions (instant, no LLM) ---
    basic = _offline_basic_answer(qlc, lang, agent_id)
    if basic:
        return _frame(basic)

    # --- 1. Navigation by keyword (per-agent pages from the profile) ---
    try:
        from services.assistant_agent import AGENT_PROFILES  # self-ref ok at call time
        prof = AGENT_PROFILES.get((agent_id or "").lower())
        pages = []
        if prof:
            for entry in prof.get("pages", []):
                path, _, label = str(entry).partition(" — ")
                pages.append((path.strip(), label.strip().lower(), str(entry).lower()))
        else:
            from services.assistant_manifest import get_all_pages
            for p in get_all_pages(include_hidden=False):
                pages.append((p["path"], (p.get("name") or "").lower(), (p.get("name", "") + " " + p.get("path", "")).lower()))

        if any(v in qlc for v in _NAV_VERBS):
            # find the page whose name/path appears in the query
            best = None
            for path, label, blob in pages:
                # match on the page name words or the path slug
                name_words = [w for w in label.replace("/", " ").split() if len(w) > 1]
                slug = path.strip("/").split("/")[-1]
                if (slug and slug in qlc) or any(w in qlc for w in name_words[:3]):
                    best = path
                    break
            if best:
                return _frame(
                    (f"{best} 페이지를 엽니다." if lang == "ko" else f"Opening {best}."),
                    action={"type": "navigate", "to": best}, intent="navigate")
    except Exception as e:
        log.warning(f"offline nav match failed: {str(e)[:100]}")

    # --- 2. Best matching snippet from KB or the current page ---
    # KB hits (already retrieved upstream via local embeddings — no network).
    # Reasoning/synthesis verbs can't be answered offline — if the query is one
    # of these, don't surface a weak KB match; tell the user to turn the LLM on.
    reasoning_q = any(m in qlc for m in (
        "summarize", "summary", "explain", "why", "compare", "recommend",
        "suggest", "analyze", "draft", "write a", "pros and cons",
        "요약", "왜", "비교", "추천", "분석",
    ))
    best_snip = ""
    from_learned = False
    try:
        if kb_context:
            # 2a. FIRST prefer a VERIFIED / LEARNED *answer* (from the 👍 +
            #     web-research self-improvement loop). Only `good-` (human
            #     approved Q&A) and `web-` (researched answer) notes are real
            #     answers — `fix-` notes are BEHAVIOURAL corrections ("avoid
            #     starting responses with…") and must NOT be surfaced as
            #     answers. Require a STRONG match (0.58) so an unrelated note
            #     never poses as the answer to a different question.
            for hit in kb_context[:5]:
                fname = (hit.get("filename") or hit.get("location") or "").lower()
                base = fname.rsplit("/", 1)[-1]
                is_answer_note = base.startswith("good-") or base.startswith("web-")
                if is_answer_note and (hit.get("similarity", 0) or 0) >= 0.58:
                    ans = _extract_clean_answer(hit.get("content") or "")
                    if ans:
                        best_snip = ans[:900]
                        from_learned = True
                        break

            # 2b. Otherwise a strong ordinary KB hit (0.42 floor), cleaned up.
            #     Reasoning/synthesis queries are skipped here — a raw uploaded
            #     chunk can't synthesise; those need the LLM.
            if not best_snip and not reasoning_q:
                top = kb_context[0]
                if (top.get("similarity", 0) or 0) >= 0.42:
                    cleaned = _extract_clean_answer(top.get("content") or "")
                    if cleaned:
                        best_snip = cleaned[:700]
    except Exception:
        pass

    # Page context keyword scan — find the line(s) mentioning the query terms.
    if not best_snip and page_context and not reasoning_q:
        terms = [w for w in qlc.split() if len(w) > 1][:6]
        lines = [ln.strip() for ln in page_context.splitlines() if ln.strip()]
        scored = []
        for ln in lines:
            llc = ln.lower()
            hits = sum(1 for t in terms if t in llc)
            if hits:
                scored.append((hits, ln))
        scored.sort(key=lambda x: x[0], reverse=True)
        if scored:
            best_snip = "\n".join(ln for _h, ln in scored[:4])[:700]

    if best_snip:
        if from_learned:
            # A real, verified answer — present it AS the answer, with no "raw
            # dump" framing. A small footer notes it's offline + learned.
            footer = ("\n\n— 오프라인 · 학습된 답변" if lang == "ko"
                      else "\n\n— offline · from learned answers")
            return _frame(best_snip + footer)
        prefix = ("자료에서 찾았습니다:\n\n"
                  if lang == "ko" else "From your data:\n\n")
        return _frame(prefix + best_snip)

    # --- 3. Needs the LLM ---
    # Flag it so run_agent's instrumentation logs this as a knowledge GAP and
    # kicks off background research (server-side, no live LLM call to the user).
    # Next time this is asked, the KB has a learned answer → answerable offline.
    out = _frame(
        "이 질문은 지금 가진 자료로는 답하기 어렵습니다. (학습 중 — 다음엔 답할 수 있어요. 지금 바로 답하려면 LLM을 켜주세요.)"
        if lang == "ko" else
        "I couldn't find this in your knowledge base or this page yet. (Learning it now — I should be able to answer next time. Turn the LLM on for an instant answer.)")
    out["needs_llm"] = True
    return out


def _run_agent_impl(
    db: Session,
    transcript: str,
    language: str = "auto",
    current_path: Optional[str] = None,
    selected_id: Optional[str] = None,
    history: Optional[list[dict]] = None,
    confirmed_tool: Optional[str] = None,
    confirmed_args: Optional[dict] = None,
    attachment_ids: Optional[list[str]] = None,
    forced_model: Optional[str] = None,
    user_id: Optional[str] = None,
    agent_id: str = "vip",
    page_context: Optional[str] = None,
) -> dict[str, Any]:
    """Run one agent turn. Returns:
        {intent, language, reply, action, speak, transcript, tool_used, tool_result,
         proposed_action?}

    confirmed_tool / confirmed_args:
        Set when the user clicked Confirm on a previously-proposed write
        action. We bypass the LLM and execute the tool directly.
    """
    # === Direct execute path (after user confirmed a proposed write) ===
    if confirmed_tool and confirmed_tool in TOOL_REGISTRY:
        tool = TOOL_REGISTRY[confirmed_tool]
        args = confirmed_args or {}
        # Carry the path through if the tool wants it
        if current_path and "current_path" not in args:
            args["current_path"] = current_path
        tool_result = execute_tool(confirmed_tool, args, db=db, agent_id=agent_id, transcript=transcript)
        action = tool_result.get("action") if isinstance(tool_result, dict) else None
        reply = tool_result.get("message") if isinstance(tool_result, dict) else "Done."
        if not reply:
            reply = "Done." if tool_result.get("ok") else f"Failed: {tool_result.get('error', 'unknown')}"
        return {
            "intent": confirmed_tool,
            "language": language if language in ("ko", "en") else "en",
            "reply": reply,
            "action": action,
            "speak": True,
            "transcript": transcript or f"[confirmed: {confirmed_tool}]",
            "tool_used": confirmed_tool,
            "tool_result": tool_result,
            "confirmed": True,
        }

    transcript = (transcript or "").strip()

    # Resolve relative dates ('this week monday', 'last monday', '지난주 화요일') to an
    # explicit date up front, so a past-date question isn't mistaken for a live-price
    # one (and history lookup gets a real date). No-op when a date is already present
    # or it isn't a stock question.
    transcript = _inject_relative_date(transcript) or transcript

    # Detect the QUESTION's language (not the data's). A pinned language wins.
    # Otherwise: if the message has ≥2 English words it's an English question —
    # even when it mentions Korean proper nouns (property/place names like
    # '의정부한양파크뷰'), which previously forced a Korean reply. Only treat it
    # as Korean when there's Hangul and it's not an English sentence.
    if language in ("ko", "en"):
        lang = language
    else:
        eng_words = len(_re.findall(r"[A-Za-z]{2,}", transcript))
        hangul = sum(1 for c in transcript if 0xAC00 <= ord(c) <= 0xD7A3)
        if eng_words >= 2:
            lang = "en"
        elif hangul > 0:
            lang = "ko"
        else:
            lang = "en"

    # `system` is properly built only at the LLM-fallback stage (further below), but
    # several EARLY intercepts pass it to _run_chain — which crashed with
    # UnboundLocalError and silently killed those routes (found 2026-07-16 via the
    # matrix rig: "trade-first route failed: cannot access local variable 'system'").
    system = ""

    # === LLM TASK — translate/summarize/rewrite requests are normal-LLM work; the text
    # they contain must never fire the trading engines. Runs before every stock intent.
    if (not confirmed_tool and not attachment_ids and transcript
            and _LLM_TASK_RE.search(transcript)):
        _lt = _llm_task_reply(transcript, lang, history or [])
        if _lt:
            return {"intent": "llm_task", "language": lang, "reply": _lt,
                    "action": None, "speak": True, "transcript": transcript,
                    "tool_used": "llm_task"}

    # === ADR PRICE — 'SK Hynix ADR price?' answers the US listing, never the Korean one.
    # Concept asks ('what is ADR?') skip through to the concept/LLM path.
    if (not confirmed_tool and not attachment_ids and transcript
            and _re.search(r"\bADRs?\b|\bAARD\b|\bADRS\b|\bADSs?\b|에이디알|주식예탁증서",
                           transcript, _re.IGNORECASE)
            and _re.search(r"price|가격|얼마|시세|trading|quote|how much|현재가", transcript, _re.IGNORECASE)):
        try:
            from services.stock_resolver import resolve_one
            _kc, _kn = resolve_one(transcript or "")
            if not _kc:                     # bare 'what is the US ADR price?' → inherit
                for _h in reversed(history or []):
                    _kc, _kn = resolve_one(str(_h.get("content") or _h.get("text") or ""))
                    if _kc:
                        break
        except Exception:
            _kc = _kn = None
        _en_a = str(lang or "").lower().startswith("en")

        def _kr_line() -> str:
            """One-line Korean-listing quote — ADR asks like 'in USA and Korea' get both."""
            try:
                from services.paper_desk import _live_price
                _px, _ = _live_price(_kc)
                if _px:
                    return (f"\n\n🇰🇷 Korean listing ({_kn}): ₩{int(_px):,} (live)" if _en_a
                            else f"\n\n🇰🇷 한국 원주 ({_kn}): {int(_px):,}원 (실시간)")
            except Exception:
                pass
            return ""
        if _kc and _kc in _ADR_US:
            _us, _exch = _ADR_US[_kc]
            _ans = None
            # PRIMARY: deterministic Yahoo quote — real number, no LLM in the loop
            _yq = _us_quote(_us)
            if _yq:
                _p = _yq["price"]
                _pcts = f" ({_yq['pct']:+.2f}% vs prev close)" if _yq.get("pct") is not None else ""
                _src = _yq.get("exchange") or _exch
                _ans = (f"**${_p:,.2f}**{_pcts} — {_src}, Yahoo Finance (may be slightly delayed)."
                        if _en_a else
                        f"**${_p:,.2f}**{_pcts} — {_src} · Yahoo Finance 기준 (지연 가능).")
            if not _ans:
                try:
                    from services.stock_advisor_chat import ask as _stock_direct
                    _q = (f"What is {_us} trading at right now?" if _en_a else f"{_us} 지금 얼마야?")
                    _d = _stock_direct(_q, lang, [], timeout=_STOCK_TIMEOUT)
                    if isinstance(_d, dict):
                        _c = (_d.get("reply") or "").strip()
                        if _c and "$" in _c:
                            _ans = _c
                except Exception:
                    pass
            if _ans:
                _hd = (f"**{_kn} ADR ({_us} · {_exch})** — the US-listed line:\n\n" if _en_a
                       else f"**{_kn} ADR ({_us} · {_exch})** — 미국 상장 기준:\n\n")
                return {"intent": "adr_price", "language": lang,
                        "reply": (_append_krw_to_usd(_hd + _ans) + _kr_line())[:4000], "action": None,
                        "speak": True, "transcript": transcript, "tool_used": "stock_advisor"}
            _fb = (f"I couldn't fetch a live quote for {_kn}'s US ADR ({_us}, {_exch}) right now — "
                   f"that line isn't in our data source yet."
                   if _en_a else
                   f"{_kn}의 미국 ADR({_us}, {_exch}) 실시간 시세를 지금은 조회하지 못했습니다 — 아직 우리 "
                   f"데이터 소스에 없는 종목입니다.")
            return {"intent": "adr_price", "language": lang, "reply": _fb + _kr_line(), "action": None,
                    "speak": True, "transcript": transcript, "tool_used": None}
        if _kc:
            _fb = (f"{_kn} has no US-listed ADR in our map — its US line either doesn't exist or "
                   f"isn't covered yet. The Korean listing is available with \"{_kn} price\"."
                   if _en_a else
                   f"{_kn}은(는) 우리가 아는 미국 상장 ADR이 없습니다 — 한국 원주 시세는 "
                   f"\"{_kn} 얼마야?\"로 확인하실 수 있습니다.")
            return {"intent": "adr_price", "language": lang, "reply": _fb, "action": None,
                    "speak": True, "transcript": transcript, "tool_used": None}
        _fb = ("Which company's ADR do you mean? (e.g. \"SK Telecom ADR price\" — I can quote "
               "NYSE-listed Korean ADRs like SKM, KB, PKX, KEP directly.)" if _en_a else
               "어느 회사의 ADR을 말씀하시나요? (예: \"SK텔레콤 ADR 가격\" — SKM, KB, PKX, KEP 같은 "
               "NYSE 상장 한국 ADR은 바로 시세를 드릴 수 있습니다.)")
        return {"intent": "adr_price", "language": lang, "reply": _fb, "action": None,
                "speak": True, "transcript": transcript, "tool_used": None}

    # === 🔮 FUTURE / NEXT-DAY PREDICTION (boss 2026-07-20): "predict Skhynix tomorrow at
    # 9:00", "tomorrow morning which price will it open?" were dumping the current price.
    # Now they run ALL 3 algorithms (clean_recommendation) + an LLM final summary. Runs
    # BEFORE the high-forecast / price / P&L intercepts. Both bots, KO/EN. Needs a NEXT-DAY
    # cue so it never grabs plain current-price or today's-high questions. ===
    if not confirmed_tool and not attachment_ids and _is_future_prediction(transcript):
        try:
            _pr_stocks = list(dict.fromkeys(_all_stocks_in_query(transcript)))[:2]
            if not _pr_stocks:
                try:
                    from services.stock_resolver import resolve_one as _r1p
                    _cp, _np = (_r1p(transcript or "") or (None, None))[:2]
                    if _cp:
                        _pr_stocks = [(_cp, _np or _cp)]
                except Exception:
                    pass
            if _pr_stocks:
                from services.decision_brain import prediction_view as _pr_view
                _pr_parts = []
                for _c, _n in _pr_stocks:
                    _res = execute_tool("decide", {"ticker": _c}, db=db,
                                        agent_id=agent_id, transcript=transcript)
                    if not (isinstance(_res, dict) and _res.get("ok")):
                        continue
                    # PREDICTION framing (direction/up-down/flat + why) — NOT buy/sell/hold.
                    _block, _dirs = _pr_view(db, _res, lang)
                    _summary = _llm_prediction_summary(_res, _block, _n, lang, transcript)
                    _one = _block + (("\n\n" + _summary) if _summary else "")
                    if len(_pr_stocks) > 1:
                        _one = f"# 📌 {_n}\n\n{_one}"
                    _pr_parts.append(_one)
                    try:  # grade the DIRECTION prediction vs the real move
                        from services.call_grader import log_call
                        _pa = {"UP": "BUY", "DOWN": "SELL", "FLAT": "HOLD"}.get(_dirs.get("a1"), "HOLD")
                        log_call(db, ticker=_res.get("ticker"), action=_pa,
                                 intent="prediction", ref_price=_res.get("price"),
                                 horizon_min=60, name=_res.get("name"),
                                 agent_id=agent_id, lang=lang)
                    except Exception:
                        pass
                if _pr_parts:
                    _hdr = ("🔮 **Tomorrow / forward prediction — based on all 3 algorithms**\n\n"
                            if str(lang).lower().startswith("en")
                            else "🔮 **내일·향후 예측 — 3개 알고리즘 종합**\n\n")
                    return {"intent": "future_prediction", "language": lang,
                            "reply": (_hdr + "\n\n---\n\n".join(_pr_parts))[:9000],
                            "action": None, "speak": True, "transcript": transcript,
                            "tool_used": "future_prediction"}
        except Exception as e:
            log.warning(f"future-prediction lane failed: {str(e)[:120]}")

    # === 📈 INTRADAY-HIGH FORECAST (boss 2026-07-20): "prediction of today's highest
    # price and what time" was returning the CURRENT price (hallucination). Runs FIRST —
    # before the P&L ('얼마') / portfolio / price intercepts that were swallowing the KO
    # phrasing ("...얼마까지 예측?"). forecast/예측 + high/최고가/what-time → real estimate
    # (range + time window from 1yr intraday pattern). Named stocks → each; both bots. ===
    if not confirmed_tool and not attachment_ids:
        try:
            from services.intraday_high_forecast import is_high_forecast_question as _is_hf
            if _is_hf(transcript):
                from services.intraday_high_forecast import forecast as _hf
                _hf_stocks = list(dict.fromkeys(_all_stocks_in_query(transcript)))[:3]
                if not _hf_stocks:
                    try:
                        from services.stock_resolver import resolve_one as _r1h
                        _ch, _nh = (_r1h(transcript or "") or (None, None))[:2]
                        if _ch:
                            _hf_stocks = [(_ch, _nh or _ch)]
                    except Exception:
                        pass
                if _hf_stocks:
                    _parts = [p for (_c, _n) in _hf_stocks if (p := _hf(db, _c, _n, lang))]
                    if _parts:
                        return {"intent": "high_forecast", "language": lang,
                                "reply": "\n\n".join(_parts)[:9000], "action": None,
                                "speak": True, "transcript": transcript,
                                "tool_used": "high_forecast"}
        except Exception as e:
            log.warning(f"high-forecast lane failed: {str(e)[:120]}")

    # === MY P&L — 'Yesterday how much I won?' → the 모의투자 desk's realized result for
    # that period (must run BEFORE context-math/price-history, which stole this question).
    if (not confirmed_tool and not attachment_ids and transcript
            and _PNL_RE.search(transcript)
            and not _pnl_is_really_price(transcript)):
        _pl = _paper_pnl_reply(db, lang, transcript, agent_id)
        if _pl:
            return {"intent": "paper_pnl", "language": lang, "reply": _pl,
                    "action": None, "speak": True, "transcript": transcript,
                    "tool_used": "paper_desk"}

    # === CONVERSATIONAL CONFIRMATION / CONTEXT MATH — a short 'so it means…, right?' or
    # 'if I win 1% how much will I win?' follow-up gets a natural LLM chat answer from the
    # conversation itself, not another data dump or a picks scan.
    # Skipped when the message wants fresh data or an actual decision (지금/살까/추천…).
    _is_ctx_math = bool(history and transcript and _CHAT_MATH_RE.search(transcript))
    if (history and transcript and len(transcript) <= (120 if _is_ctx_math else 90)
            and not attachment_ids and not confirmed_tool
            and (_CONFIRM_RE.search(transcript) or _is_ctx_math)
            and not any(k in transcript.lower() for k in _CONFIRM_SKIP)
            and not _is_adviceish(transcript)
            and not _confirm_wants_fresh_data(transcript)
            and not _confirm_is_topic_switch(transcript)):
        _cf = (_ctx_percent_math(transcript, lang, history) if _is_ctx_math else None) \
            or _confirm_chat_reply(transcript, lang, history, allow_math=_is_ctx_math)
        if _cf:
            return {"intent": "confirm_chat", "language": lang, "reply": _cf,
                    "action": None, "speak": True, "transcript": transcript,
                    "tool_used": "llm_confirm"}

    # === MY PORTFOLIO — 'how many stocks am I holding?' → the 모의투자 desk's real state.
    # A CHATBOT-orders question ("what did I buy using chatbot?") is NOT the whole
    # portfolio — its own lane below answers from the order record (2026-08-26).
    if (not confirmed_tool and not attachment_ids and transcript
            and not _is_my_chat_orders_q(transcript)
            and not _is_cancelish(transcript)
            and _PORTFOLIO_RE.search(transcript)):
        _pf = _paper_portfolio_reply(db, lang, agent_id, focus_text=transcript)
        if _pf:
            return {"intent": "paper_portfolio", "language": lang, "reply": _pf,
                    "action": None, "speak": True, "transcript": transcript,
                    "tool_used": "paper_desk"}

    # === 🌙 OVERNIGHT hold-or-sell lane (boss 2026-07-16): "팔고 갈까 들고 갈까?"
    # / "should I hold overnight?" — measured-statistics verdict (backtest found
    # NO ML edge; every 15:00-visible condition had positive avg gap this year),
    # per-stock record + fee math, call logged + auto-graded vs the real open.
    # Runs BEFORE the position/turn/analyst lanes (they were swallowing it).
    # Named stock → that stock; none named → every held position (up to 3). ===
    if not confirmed_tool and not attachment_ids:
        try:
            from services.overnight_gap import is_overnight_question as _is_on
            if _is_on(transcript):
                from services.overnight_gap import advise as _on_advise
                _on_stocks = list(dict.fromkeys(_all_stocks_in_query(transcript)))[:3]
                if not _on_stocks:
                    try:
                        from services.stock_resolver import resolve_one as _r1o
                        _co, _no = (_r1o(transcript or "") or (None, None))[:2]
                        if _co:
                            _on_stocks = [(_co, _no or _co)]
                    except Exception:
                        pass
                if not _on_stocks:
                    from sqlalchemy import text as _sql_text
                    _held = db.execute(_sql_text(
                        "SELECT ticker, name FROM paper_desk_positions "
                        "WHERE qty > 0 ORDER BY qty * avg_price DESC LIMIT 3")).fetchall()
                    _on_stocks = [(r[0], r[1] or r[0]) for r in _held]
                if _on_stocks:
                    _parts = []
                    for _c, _n in _on_stocks:
                        _a = _on_advise(db, _c, _n, lang)
                        if _a:
                            _parts.append(_a)
                    if _parts:
                        return {"intent": "overnight_call", "language": lang,
                                "reply": "\n\n".join(_parts)[:9000], "action": None,
                                "speak": True, "transcript": transcript,
                                "tool_used": "overnight_call"}
                else:
                    return {"intent": "overnight_call", "language": lang,
                            "reply": ("Which stock, and are you holding it? Name it "
                                      "(e.g. \"should I hold Samsung overnight?\") — "
                                      "or buy first and ask again; I answer from the "
                                      "measured overnight record."
                                      if str(lang).lower().startswith("en") else
                                      "어느 종목인가요? 종목명을 함께 물어봐 주세요 "
                                      "(예: \"삼성전자 들고 갈까?\") — 보유 중인 종목이 "
                                      "있으면 자동으로 그 종목들로 답합니다."),
                            "action": None, "speak": True, "transcript": transcript,
                            "tool_used": "overnight_call"}
        except Exception as e:
            log.warning(f"overnight lane failed: {str(e)[:120]}")

    # === 🏁 WHICH-ALGORITHM-IS-BEST lane (boss 2026-07-20: "I'll use real money,
    #     which of my 3 algorithms is better/more accurate?"). Answers from the
    #     multi-day, fee-honest scoreboard + the CAREFUL go/no-go gate (net ₩>0
    #     AND ≥5 days AND ≥30 trips). Honest verdict, KO/EN, both bots. ===
    if not confirmed_tool and not attachment_ids:
        try:
            _ql = (transcript or "").lower()
            _has_algo = ("알고리즘" in transcript or "알고리듬" in transcript
                         or "algorithm" in _ql or "algo" in _ql)
            _pick_cue = any(k in transcript for k in
                            ("비교", "제일", "가장", "좋", "나은", "정확", "추천",
                             "실전", "실제 돈", "진짜 돈", "실거래", "뭐가", "어떤", "어느")) \
                        or any(k in _ql for k in
                               ("best", "better", "which", "compare", "accurate",
                                "real money", "recommend", "winning", "most profit"))
            if _has_algo and _pick_cue:
                from routers.paper_desk import _scoreboard as _sb_fn
                _sb = _sb_fn(db, 15)
                _reply = _format_algo_scoreboard(_sb, lang)
                return {"intent": "algo_scoreboard", "language": lang,
                        "reply": _reply[:9000], "action": None, "speak": True,
                        "transcript": transcript, "tool_used": "algo_scoreboard"}
        except Exception as e:
            log.warning(f"algo-scoreboard lane failed: {str(e)[:120]}")

    # === M2 — POSITION-AWARE advice (a holding the user already has) ===
    # "지난주 SK하이닉스 200주 -4% 어떡해?" → 버티기/손절/물타기/익절 with trigger prices,
    # from the 3-method decide + the user's P&L. Runs BEFORE delegation so VIP + AI Advisor
    # both use the SAME local advisor (identical answer). Logged to grading (position, 120m).
    if not confirmed_tool and not attachment_ids:
        try:
            from services.position_parse import is_position_question, parse
            # the CHECKLIST advice engine owns buy/sell verdicts now (boss 2026-08-25:
            # no ML in advice) — this ML-backed lane keeps only what that engine
            # doesn't claim
            from services import checklist_advice as _ca_gate
            if (is_position_question(transcript) and not _ca_gate.kind(transcript)
                    and not _is_cancelish(transcript)):
                from services.position_advice import advise as _pos_advise
                _adv = _pos_advise(db, parse(transcript))
                if _adv.get("ok"):
                    _en = str(lang or "").lower().startswith("en")
                    _reply = _adv.get("reasoning_en" if _en else "reasoning_ko")
                    # DETAIL LAYER: grounded LLM deep-dive — inserted BEFORE the 📌 summary
                    # so the answer still ends with the final answer (boss format).
                    _extra = _elaborate_answer(transcript, lang,
                                               [{"tool": "position_advice", "result": _adv}])
                    if _extra:
                        _reply = _insert_before_summary(_reply or "", "\n\n" + _extra)
                    try:
                        from services.call_grader import log_call
                        _ga = {"CUT": "SELL", "TAKE_PROFIT": "SELL", "HOLD_OR_ADD": "BUY"}.get(_adv.get("action"), "HOLD")
                        log_call(db, ticker=_adv["ticker"], action=_ga, intent="position",
                                 ref_price=_adv.get("price"), stop=_adv.get("stop"), horizon_min=120,
                                 name=_adv.get("name"), agent_id=agent_id, lang=lang)
                    except Exception:
                        pass
                    return {"intent": "position_advice", "language": lang,
                            "reply": (_reply or "")[:6000],
                            "action": None, "speak": True, "transcript": transcript,
                            "tool_used": "position_advice"}
        except Exception as e:
            log.warning(f"position advice failed: {str(e)[:120]}")

    # === TURN TIMING ("언제 반등해?/when will it turn?/언제 팔아야/바닥 언제") — the boss's
    # turn strategy served as INFORMATIONAL timing: measured rhythm card + live turn score,
    # with the honest NO-GO backtest label. Fires get logged for forward grading (intent=turn).
    _turn_q = bool(_re.search(
        r"언제\s*(반등|오르|올라|팔|사야|들어가)|반등\s*언제|바닥\s*언제|고점\s*언제|턴\s*(왔|신호|타이밍)"
        r"|when\s+(will|does|is|should)\b.{0,40}\b(turn|bounce|rebound|bottom|sell|peak)"
        r"|turn signal|bottom yet|hit the bottom|when to (sell|buy in|enter)",
        (transcript or ""), _re.IGNORECASE))
    if not confirmed_tool and not attachment_ids and _turn_q:
        try:
            from services.stock_resolver import resolve_one
            from services.turn_engine import turn_reply, live_turn_status
            _tc, _tn = resolve_one(transcript or "")
            if not _tc:                                  # bare follow-up: inherit from history
                for _h in reversed(history or []):
                    _tc, _tn = resolve_one(str(_h.get("content") or _h.get("text") or ""))
                    if _tc:
                        break
            if _tc:
                if str(lang or "").lower().startswith("en"):
                    try:
                        from services.stock_resolver import display_name_en
                        _tn = display_name_en(_tc) or _tn
                    except Exception:
                        pass
                _rep = turn_reply(db, _tc, _tn or _tc, lang)
                if _rep:
                    try:                                  # E2: measured record of past turn fires
                        from services.call_grader import track_record_line
                        _trl = track_record_line(db, "turn", lang)
                        if _trl:
                            _rep += _trl
                    except Exception:
                        pass
                    try:                                  # E1: grade every FIRING signal forward
                        _st = live_turn_status(db, _tc)
                        if _st.get("fire"):
                            from services.call_grader import log_call
                            log_call(db, ticker=_tc, action="BUY", intent="turn",
                                     ref_price=_st.get("price"),
                                     horizon_min=int((_st.get("rhythm") or {}).get("up_min") or 30),
                                     name=_tn, agent_id=agent_id, lang=lang)
                    except Exception:
                        pass
                    return {"intent": "turn_timing", "language": lang, "reply": _rep,
                            "action": None, "speak": True, "transcript": transcript,
                            "tool_used": "turn_engine"}
        except Exception as e:
            log.warning(f"turn timing failed: {str(e)[:120]}")

    # === 🏁 3-STRATEGY TOURNAMENT ("오늘 전략 대결 / 알고리즘 어느 게 이기고 있어 / which
    # algorithm is winning today") — live shadow race: Algo1 vs Ripple vs Candle 3-2 on
    # the same basket, same day, graded by avg-%-per-trade. Runs BEFORE the Method-4
    # cycle compare so the boss's "which one is winning" reaches the tournament. ===
    if (not confirmed_tool and not attachment_ids and any(
            k in (transcript or "").lower() for k in (
                "전략 대결", "세 전략", "3개 전략", "세개 전략", "오늘 어느 알고리즘", "어느 알고리즘이 이기",
                "알고리즘 어느", "어느 전략이 이기", "누가 이기고", "which algorithm is winning",
                "which strategy is winning", "tournament", "algo1 vs", "three strateg"))):
        try:
            from services.strategy_tournament import report as _trep
            _r = _trep(db, lang)
            if _r:
                return {"intent": "tournament", "language": lang, "reply": _r,
                        "action": None, "speak": True, "transcript": transcript,
                        "tool_used": "tournament"}
        except Exception as e:
            log.warning(f"tournament report failed: {str(e)[:120]}")

    # === Method-4 STRATEGY COMPARISON ("전략 비교/compare strategies/1% 전략") — honest
    # replay of A(fixed ±1%, no time limit) vs B(RSI-timed ±1% cycles) on stored 5-min bars.
    if (not confirmed_tool and not attachment_ids and any(
            k in (transcript or "").lower() for k in (
                "전략 비교", "전략비교", "알고리즘 비교", "두 전략", "1% 전략", "사이클 전략",
                "compare strateg", "compare algorithm", "compare the two", "cycle strategy",
                "1% strategy", "which strategy"))):
        try:
            from services.stock_resolver import resolve_one
            from services.cycle_scalp import compare_reply
            _cc, _cn = resolve_one(transcript or "")
            _cc = _cc or "000660"
            _cn = _cn or ("SK Hynix" if str(lang).startswith("en") else "SK하이닉스")
            _rep = compare_reply(db, _cc, _cn, lang)
            if _rep:
                return {"intent": "cycle_compare", "language": lang, "reply": _rep,
                        "action": None, "speak": True, "transcript": transcript,
                        "tool_used": "cycle_compare"}
        except Exception as e:
            log.warning(f"cycle compare failed: {str(e)[:120]}")

    # === Phase C — READINESS GATE ("실전 매매 준비됐어? / are we ready for real money?") ===
    # A named stock means a stock question ("삼성전자 성적 어때?"), not the gate report.
    if (not confirmed_tool and not attachment_ids and _is_readiness_question(transcript)
            and not _all_stocks_in_query(transcript)):
        try:
            from services.readiness import reply_text as _ready_text
            return {"intent": "readiness", "language": lang,
                    "reply": _ready_text(db, lang), "action": None, "speak": True,
                    "transcript": transcript, "tool_used": "readiness"}
        except Exception as e:
            log.warning(f"readiness failed: {str(e)[:120]}")

    # CONTEXT STOCK for follow-ups (boss 2026-08-26: "if I ask skhynix it must
    # understand next question without naming skhynix") — the most recent stock in
    # the conversation stands in when the new question names none.
    _ctx_stock = None
    try:
        if history and not _all_stocks_in_query(transcript):
            _ctx_stock = _recent_stock_name(history)
    except Exception:
        pass

    # === 🧾 CHAT ORDER DESK (boss 2026-08-25: "if we say then buy samsung electronics
    # is it possible?") — an imperative BUY/SELL command becomes a real desk order via
    # a two-turn confirmation. The confirm word ('네/yes') is checked FIRST so it is
    # never stolen by another lane while an order is pending. ===
    if not confirmed_tool and not attachment_ids:
        try:
            from services import chat_trade as _ct
            _cw = _ct.confirm_check(transcript)
            if _cw is not None:
                _ctr = _ct.finish(db, _cw)
                if _ctr:
                    return {"intent": "chat_trade", "language": lang, "reply": _ctr,
                            "action": None, "speak": True, "transcript": transcript,
                            "tool_used": "chat_trade"}
            _ctq = _ct.qty_reply(db, transcript)
            if _ctq:
                return {"intent": "chat_trade_confirm", "language": lang, "reply": _ctq,
                        "action": None, "speak": True, "transcript": transcript,
                        "tool_used": "chat_trade"}
            _ctx = _ct.cancel_open(db, transcript, lang)
            if _ctx:
                return {"intent": "chat_trade", "language": lang, "reply": _ctx,
                        "action": None, "speak": True, "transcript": transcript,
                        "tool_used": "chat_trade"}
            _ctp = _ct.build_preview(db, transcript, lang)
            # "buy it" after a skhynix turn — the context stock completes the command
            if not _ctp and _ctx_stock:
                _cs9 = _ctx_stock
                if not _re.search(r"[가-힣]", transcript or ""):
                    # an EN command must not inherit a Korean name (it forced the
                    # whole confirmation into Korean)
                    try:
                        from services.stock_resolver import display_name_en, resolve_one
                        _c9, _n9 = resolve_one(_ctx_stock)
                        if _c9:
                            _cs9 = display_name_en(_c9)
                    except Exception:
                        pass
                _ctp = _ct.build_preview(db, f"{transcript} {_cs9}", lang)
            if _ctp:
                return {"intent": "chat_trade_confirm", "language": lang, "reply": _ctp,
                        "action": None, "speak": True, "transcript": transcript,
                        "tool_used": "chat_trade"}
        except Exception as e:
            log.warning(f"chat trade lane failed: {str(e)[:120]}")

    # === 💬 MY CHAT ORDERS ("오늘 내가 챗봇으로 뭐 샀지?" / "what did I buy using
    # chatbot?") — answered from the ORDER RECORD, not the whole desk portfolio
    # (deep audit 2026-08-26: it listed all 21 algo positions instead). ===
    if not confirmed_tool and not attachment_ids and _is_my_chat_orders_q(transcript):
        try:
            _mco = _my_chat_orders_reply(db, transcript, lang)
            if _mco:
                return {"intent": "chat_orders", "language": lang, "reply": _mco,
                        "action": None, "speak": True, "transcript": transcript,
                        "tool_used": "chat_orders"}
        except Exception as e:
            log.warning(f"my-chat-orders lane failed: {str(e)[:120]}")

    # === 💰 DESK P&L TODAY ("how much profit did the desk make today?") — real
    # numbers from the order record (deep audit 2026-08-26: the report lane
    # apologized 'no data available'). ===
    if (not confirmed_tool and not attachment_ids and _is_desk_pnl_q(transcript)
            and not _all_stocks_in_query(transcript)):
        try:
            _dpl = _desk_pnl_reply(db, transcript, lang)
            if _dpl:
                return {"intent": "desk_pnl", "language": lang, "reply": _dpl,
                        "action": None, "speak": True, "transcript": transcript,
                        "tool_used": "desk_pnl"}
        except Exception as e:
            log.warning(f"desk-pnl lane failed: {str(e)[:120]}")

    # === 🧭 CHECKLIST ADVICE (boss 2026-08-25: "when we ask any advise again ML is
    # coming out... it should analyze deeply using 100 checklist, news, then answer" +
    # the holder's case "yesterday I bought, should I sell now?" judged by our own
    # laws: rising→hold, selling zone→sell at 3rd blue, buying zone→never sell).
    # Replaces every ML decide-chain for buy/sell advice, and ships the same animated
    # checklist "process" the reco desk shows. ===
    if not confirmed_tool and not attachment_ids and not _is_movers_q(transcript):
        try:
            from services import checklist_advice as _ca
            if _ca.kind(transcript) and (_all_stocks_in_query(transcript) or _ctx_stock):
                _adv_tx = (transcript if _all_stocks_in_query(transcript)
                           else f"{_ctx_stock} {transcript}")
                _adv = _ca.build(db, _adv_tx, lang)
                if _adv and _adv.get("reply"):
                    return {"intent": "checklist_advice", "language": lang,
                            "reply": _adv["reply"], "action": None, "speak": True,
                            "transcript": transcript, "tool_used": "checklist_advice",
                            "process": _adv.get("process")}
        except Exception as e:
            log.warning(f"checklist advice lane failed: {str(e)[:120]}")

    # === 📚 FUNDAMENTALS / CONSENSUS lane (deep audit 2026-08-25) — before the analyst
    # LLM so PER/배당/시가총액/목표가 get real sourced numbers, never an apology. ===
    if not confirmed_tool and not attachment_ids and _is_fundamentals_q(transcript):
        try:
            _fr = _fundamentals_reply(transcript, lang, db)
            if _fr:
                return {"intent": "stock_fundamentals", "language": lang, "reply": _fr,
                        "action": None, "speak": True, "transcript": transcript,
                        "tool_used": "stock_fundamentals"}
        except Exception as e:
            log.warning(f"fundamentals lane failed: {str(e)[:120]}")

    # === ❓ WHY-DID-IT-MOVE lane (deep audit 2026-08-25) — before the analyst LLM,
    # which invented a 2024 sell-off story for this exact question shape. ===
    _wm_tx = transcript
    if _ctx_stock and not _all_stocks_in_query(transcript):
        _wm_tx = f"{_ctx_stock} {transcript}"     # "why did it drop?" follow-up
    if not confirmed_tool and not attachment_ids and _is_why_move_q(_wm_tx) \
            and not _is_future_outlook(transcript):
        try:
            _wr, _wtr = _why_move_reply(_wm_tx, lang, db)
            if _wr:
                return {"intent": "stock_why_move", "language": lang, "reply": _wr,
                        "action": None, "speak": True, "transcript": transcript,
                        "tool_used": "stock_why_move", "datasource": _wtr}
        except Exception as e:
            log.warning(f"why-move lane failed: {str(e)[:120]}")

    # === 🧠 SMART-ANALYST lane (boss 2026-07-16): analytical/explanatory stock
    # questions (ETF rebalancing mechanics, close quality, 수급 interpretation,
    # "does that mean tomorrow rises?") get a strong LLM briefed with a LIVE
    # Kiwoom/Naver/news data pack — free-form, GPT-grade, question-shaped.
    # Simple data-flow asks (수급/거래량/공매도 얼마?) get the CONCISE mode.
    # Runs BEFORE the one-door/scalp/decide intercepts (a short-selling "what is"
    # was getting swallowed by the setup scanner). Action questions (살까/팔까/
    # should I buy) still fall through to the 3-method decision format. ===
    if not confirmed_tool and not attachment_ids:
        try:
            from services.analyst_answer import answer as _an_answer
            from services.analyst_answer import is_analysis_question as _is_an
            from services.analyst_answer import is_simple_data_question as _is_sd
            _an_stocks = list(dict.fromkeys(_all_stocks_in_query(transcript)))[:3]
            if not _an_stocks:
                try:
                    from services.stock_resolver import resolve_one as _r1a
                    _ca, _na = (_r1a(transcript or "") or (None, None))[:2]
                    if _ca:
                        _an_stocks = [(_ca, _na or _ca)]
                except Exception:
                    pass
            _full = (_is_an(transcript, has_stock=bool(_an_stocks))
                     and not _wants_recommendation(transcript))
            # NOTE: no _wants_recommendation gate here — it false-positives on data
            # nouns ("net BUYING") and sent info questions into the 7,000-char
            # recommendation (boss's screenshot). _is_sd's own _ACTION regex
            # already excludes real 살까/should-I-buy asks.
            _simple = (not _full and _is_sd(transcript))
            # month/year-window stats ("최근 8개월 최저·최고·거래량") belong to the
            # deterministic period-stats lane, not an LLM data-pack answer (boss audit
            # 2026-08-24: the KO phrasing got a "data not included" apology here).
            if (_full or _simple) and _is_period_stats_q(transcript):
                _full = _simple = False
            # score questions ("지금 점수 몇 점이야?") belong to the checklist-score lane —
            # the analyst LLM invented its own 0-10 scale here (boss 2026-08-25).
            _tl_an = (transcript or "").lower()
            if (_full or _simple) and ("점수" in _tl_an or _re.search(r"\bscore\b", _tl_an)) \
                    and not any(k in _tl_an for k in ("체크리스트", "checklist")):
                _full = _simple = False
            if _full or _simple:
                _an_out = _an_answer(db, transcript, lang, _an_stocks, history,
                                     concise=_simple)
                if _an_out:
                    return {"intent": "analyst", "language": lang,
                            "reply": _an_out[:9000]}
        except Exception as e:
            log.warning(f"analyst lane failed: {str(e)[:120]}")

    # === INTRADAY SETUP SCANNER ("지금 뭐 살까?" / "what should I trade now?") — the
    # boss's 1-hour scalp system: ACT_NOW / FORMING / NOTHING with entry/target-band/stop
    # zones. Runs BEFORE dip-bounce/movers (it's the proactive "you tell ME" version).
    if (not confirmed_tool and not attachment_ids and _is_setup_question(transcript)
            and not _all_stocks_in_query(transcript)):     # a named stock → advice instead
        # ONE DOOR (boss 2026-07-09): every broad "what should I buy/trade now?" — any
        # phrasing, any language, both bots — gets the SAME unified answer: the big ⚡
        # 1-hour verdict (scanner + reasons + forming watch) on top, 🛒 multi-day ideas
        # below. Previously KO scalp phrasings hit the raw scanner while EN hit
        # buy_picks — same question, two different answers. Scanner reply = fallback.
        # OUR ALGO, NOT ML (boss 2026-08-24): recommendations come from the 100-item
        # checklist ranking + the algo's own materials (일봉·분봉·거래량·뉴스).
        # buy_picks (ML-led 3-method) survives only as the no-data fallback.
        try:
            from services.checklist_reco import build as _cr_build
            _cr = _cr_build(db, n=3, transcript=transcript, lang=lang)
            if _cr.get("ok") and _cr.get("reply"):
                return {"intent": "checklist_reco", "language": lang, "reply": _cr["reply"],
                        "action": None, "speak": True, "transcript": transcript,
                        "tool_used": "checklist_reco", "process": _cr.get("process")}
        except Exception as e:
            log.warning(f"checklist_reco (setup route) failed: {str(e)[:120]}")
        try:
            from services.buy_picks import build as _bp_build
            _bp = _bp_build(db, n=3, transcript=transcript, user_key=user_id, lang=lang)
            if _bp.get("reply"):
                return {"intent": "buy_picks", "language": lang, "reply": _bp["reply"],
                        "action": None, "speak": True, "transcript": transcript,
                        "tool_used": "buy_picks"}
        except Exception as e:
            log.warning(f"buy_picks (setup route) failed: {str(e)[:120]}")
        try:
            from services.intraday_setup import scan_reply
            _en = str(lang or "").lower().startswith("en")
            return {"intent": "intraday_setup", "language": lang,
                    "reply": scan_reply(db, lang="en" if _en else "ko"),
                    "action": None, "speak": True, "transcript": transcript,
                    "tool_used": "intraday_setup"}
        except Exception as e:
            log.warning(f"intraday_setup failed: {str(e)[:120]}")

    # === DIP-BOUNCE HUNTER — the boss's own strategy ("떨어진 종목 중 반등할 거?"):
    # ≥1.5%/1h dips + tape confirmation, every candidate auto-graded (intent='dip_bounce').
    # Runs BEFORE movers ('많이 빠진' overlaps) — a 반등/rebound word wins.
    if (not confirmed_tool and not attachment_ids
            and any(k in (transcript or "").lower() for k in _DIP_BOUNCE_KW)):
        try:
            from services.dip_bounce import scan as _dbscan
            _r = _dbscan(db, agent_id=agent_id, lang=lang)
            _en = str(lang or "").lower().startswith("en")
            return {"intent": "dip_bounce", "language": lang,
                    "reply": _r.get("reasoning_en" if _en else "reasoning_ko"),
                    "action": None, "speak": True, "transcript": transcript,
                    "tool_used": "dip_bounce"}
        except Exception as e:
            log.warning(f"dip_bounce failed: {str(e)[:120]}")

    # === B3 — LIVE MOVERS ("지금 움직이는 종목?") — real-time move% + volume vs normal ===
    if (not confirmed_tool and not attachment_ids and _is_movers_question(transcript)
            and not _all_stocks_in_query(transcript)):     # a named stock → scalp/advice instead
        try:
            from services.movers import movers as _mv
            _m = _mv(db, n=5)
            _en = str(lang or "").lower().startswith("en")
            return {"intent": "movers", "language": lang,
                    "reply": _m.get("reasoning_en" if _en else "reasoning_ko"),
                    "action": None, "speak": True, "transcript": transcript,
                    "tool_used": "movers"}
        except Exception as e:
            log.warning(f"movers failed: {str(e)[:120]}")

    # === M4 — "what should I buy?" with no ticker ===
    # SPLIT: an explicitly 단타-flavored ask → the scalp watchlist; a GENERIC buy ask
    # ("뭐 살까 / what should I buy / which stock is good") → the detailed 3-method
    # buy_picks answer (per-method verdicts, levels, sizing, market line, triggers).
    def _fuzzy_stock(t):
        """Substring match first; else the resolver's fuzzy tier ('skynix' → 000660)."""
        if _all_stocks_in_query(t):
            return True
        try:
            from services.stock_resolver import resolve_one
            return (resolve_one(t or "") or (None,))[0] is not None
        except Exception:
            return False

    # COMPOUND: 'advise top 3 stocks… AND is SK Hynix ok?' — a picks ask that ALSO names a
    # stock must answer BOTH (picks + that stock's 3-method check), not step aside into the
    # vague delegation. Pure named-stock asks ('how many shares of Samsung') still step aside.
    _tl_w = (transcript or "").lower()
    _picks_cue = any(k in _tl_w for k in (
        "top 3", "top3", "top three", "top stocks", "advise top", "recommend top",
        "best stocks", "stocks to buy", "picks", "what to buy", "which stock", "what stock",
        "종목 추천", "추천 종목", "뭐 살", "무슨 종목", "어떤 종목", "3종목", "종목 3개"))
    _compound_picks = _is_watchlist_question(transcript) and _fuzzy_stock(transcript) and _picks_cue

    def _named_stock_check(reply_so_far: str, en: bool) -> str:
        """Append the named stock's 3-method decision under its own header (compound ask)."""
        try:
            from services.stock_resolver import resolve_one
            from services.decision_agent import decide as _decide
            _cc, _cn = resolve_one(transcript or "")
            if not _cc:
                return reply_so_far
            _d = _decide(db, _cc)
            _body = _d.get("reasoning_en" if en else "reasoning_ko") or ""
            if not _body:
                return reply_so_far
            _hdr = (f"\n\n---\n\n## {_d.get('name') or _cn} — is it OK right now?\n\n" if en
                    else f"\n\n---\n\n## {_d.get('name') or _cn} — 지금 괜찮아?\n\n")
            return (reply_so_far or "") + _hdr + _body
        except Exception as _e:
            log.warning(f"compound named-stock check failed: {str(_e)[:100]}")
            return reply_so_far

    if (not confirmed_tool and not attachment_ids and _is_watchlist_question(transcript)
            and (not _fuzzy_stock(transcript) or _compound_picks)):
        _scalpish = any(k in (transcript or "").lower()
                        for k in ("단타", "초단타", "스캘", "scalp", "intraday",
                                  # EN must route like KO 단타 (EN==KO rule)
                                  "short time trad", "short term trad", "short-term trad",
                                  "day trad"))
        _en = str(lang or "").lower().startswith("en")
        if not _scalpish:
            # OUR ALGO, NOT ML (boss 2026-08-24) — checklist ranking first, buy_picks fallback.
            try:
                from services.checklist_reco import build as _cr_build
                _cr = _cr_build(db, n=3, transcript=transcript, lang=lang)
                if _cr.get("ok") and _cr.get("reply"):
                    return {"intent": "checklist_reco", "language": lang, "reply": _cr["reply"],
                            "action": None, "speak": True, "transcript": transcript,
                            "tool_used": "checklist_reco", "process": _cr.get("process")}
            except Exception as e:
                log.warning(f"checklist_reco (watchlist route) failed: {str(e)[:120]}")
            try:
                from services.buy_picks import build as _bp_build
                _bp = _bp_build(db, n=3, transcript=transcript, user_key=user_id, lang=lang)
                if _bp.get("reply"):
                    _rp = _bp["reply"]
                    if _compound_picks:
                        _rp = _named_stock_check(_rp, _en)
                    # top pick travels with the result so multi-part follow-ups ('how
                    # many?', 'when to buy/sell?') can anchor on it deterministically
                    _tpk = None
                    try:
                        _tp = ((_bp.get("buys") or []) + (_bp.get("watches") or []) or [None])[0]
                        if _tp:
                            _m3t = _tp.get("method3_wave") or {}
                            _tht = _tp.get("technicals") or {}
                            _wb = _m3t.get("verdict") == "BUY" and _m3t.get("entry")
                            _tpk = {"ticker": _tp.get("ticker"), "name": _tp.get("name"),
                                    "price": _tp.get("price"),
                                    "entry": _m3t.get("entry") if _wb else (_tht.get("support") or _tp.get("price")),
                                    "stop": _m3t.get("stop") if _wb else (
                                        int(_tht["support"] * 0.98) if _tht.get("support") else None)}
                    except Exception:
                        pass
                    return {"intent": "buy_picks", "language": lang, "reply": _rp[:9000],
                            "action": None, "speak": True, "transcript": transcript,
                            "tool_used": "buy_picks", "top_pick": _tpk}
            except Exception as e:
                log.warning(f"buy_picks (watchlist route) failed: {str(e)[:120]}")
        try:
            from services.scalp_watchlist import build as _wl_build
            _wl = _wl_build(db, n=5)
            _reply = _wl.get("reasoning_en" if _en else "reasoning_ko")
            # size the #1 pick when the budget is known — "how many" answered here too
            try:
                _p0 = (_wl.get("picks") or [None])[0]
                if _p0 and _p0.get("buy"):
                    from services.position_size import sizing_line
                    _sl = sizing_line(db, transcript=transcript, user_key=user_id, lang=lang,
                                      entry=float(_p0["buy"]),
                                      stop=float(_p0["stop"]) if _p0.get("stop") else None)
                    if _sl:
                        if "수량" in _sl or "Sizing" in _sl:   # budget-based line → say WHICH pick
                            _tag = (f" · for pick #1 {_p0.get('name_en') or _p0.get('name')}" if _en
                                    else f" · 기준: 1번 {_p0.get('name')}")
                            _sl = _sl.rstrip(".") + _tag + "."
                        _reply = (_reply or "") + _sl
            except Exception:
                pass
            try:
                from services.call_grader import track_record_line
                _tr = track_record_line(db, "scalp", lang)
                if _tr:
                    _reply = _insert_before_summary(_reply or "", _tr)
            except Exception:
                pass
            if _compound_picks:
                _reply = _named_stock_check(_reply, _en)
            # DETAIL LAYER — grounded deep dive over the watchlist's own numbers,
            # same as buy_picks/reco answers (boss: scalp answers must be detailed too).
            # On empty days it elaborates the per-candidate rejection diagnostics instead.
            try:
                _dd_data = (_wl.get("picks") or
                            ({"passed": 0, "rejected_candidates": _wl.get("rejects")}
                             if _wl.get("rejects") else None))
                if _dd_data:
                    _extra = _elaborate_answer(transcript, lang,
                                               [{"tool": "scalp_watchlist", "result": _dd_data}])
                    if _extra:
                        _reply = _insert_before_summary(_reply or "", "\n\n" + _extra)
            except Exception:
                pass
            _tpk = None
            try:
                _p1 = (_wl.get("picks") or [None])[0]
                if _p1:
                    _tpk = {"ticker": _p1.get("ticker") or _p1.get("code"), "name": _p1.get("name"),
                            "price": _p1.get("buy"), "entry": _p1.get("buy"), "stop": _p1.get("stop")}
            except Exception:
                pass
            return {"intent": "scalp_watchlist", "language": lang, "reply": (_reply or "")[:9000],
                    "action": None, "speak": True, "transcript": transcript,
                    "tool_used": "scalp_watchlist", "top_pick": _tpk}
        except Exception as e:
            log.warning(f"scalp watchlist failed: {str(e)[:120]}")

    # === M3 — SCALP signal (live intraday entry / +X% / exit timing) ===
    # "삼성전자 지금 단타 1% 가능해?" → 진입/대기 + 매수가/목표/손절/예상시간, gated by M1&3 bias.
    if not confirmed_tool and not attachment_ids and _is_scalp_question(transcript):
        try:
            from services.stock_resolver import resolve_one
            _c, _n = resolve_one(transcript or "")
            if _c:
                _m = _re.search(r"([\d.]+)\s*%", transcript or "")
                _tgt = min(max(float(_m.group(1)) if _m else 1.0, 0.3), 5.0)
                from services.day_trade import scalp_signal
                _sig = scalp_signal(db, _c, _tgt, with_backdrop=True)
                _en = str(lang or "").lower().startswith("en")
                _reply = _sig.get("reasoning_en" if _en else "reasoning_ko")
                # 몇 주? — budget-aware position sizing (1%-risk rule), only for tradable calls
                if _sig.get("entry") in ("ENTER", "WAIT"):
                    try:
                        from services.position_size import sizing_line
                        _bz = _sig.get("buy_zone") or []
                        _sl = sizing_line(db, transcript=transcript, user_key=user_id,
                                          lang=lang, entry=(_bz[1] if len(_bz) > 1 else _sig.get("current")),
                                          stop=_sig.get("stop_price"))
                        if _sl:
                            _reply = (_reply or "") + _sl
                    except Exception:
                        pass
                # measured trust — real graded record of past scalp answers
                try:
                    from services.call_grader import track_record_line
                    _tr = track_record_line(db, "scalp", lang)
                    if _tr:
                        _reply = _insert_before_summary(_reply or "", _tr)
                except Exception:
                    pass
                try:
                    from services.call_grader import log_call
                    _ga = {"ENTER": "BUY", "WAIT": "HOLD", "SKIP": "HOLD", "AVOID": "AVOID"}.get(_sig.get("entry"), "HOLD")
                    log_call(db, ticker=_c, action=_ga, intent="scalp", ref_price=_sig.get("current"),
                             target=_sig.get("target_price"), stop=_sig.get("stop_price"),
                             horizon_min=_sig.get("est_minutes") or 30, name=_n, agent_id=agent_id, lang=lang)
                except Exception:
                    pass
                # D5 — TURN TIMING line: the boss's turn strategy as the scalp's timing layer
                # (rhythm + live leg state + bottom-turn score, honest informational label).
                try:
                    from services.turn_engine import live_turn_status
                    _ts = live_turn_status(db, _c)
                    if _ts.get("ok"):
                        _rh = _ts.get("rhythm") or {}
                        _en2 = str(lang or "").lower().startswith("en")
                        _leg = _ts.get("leg")
                        if _en2:
                            _tl = (f"\n\n**⏱ Turn timing:** currently {'falling' if _leg == 'down' else 'up/flat'} "
                                   f"{_ts.get('leg_run_min')}m ({_ts.get('leg_run_pct')}%) · bottom-turn score "
                                   f"{_ts.get('bottom_turn_score')}"
                                   + (" — 🔔 FIRING" if _ts.get("fire") else "")
                                   + (f" · this stock's typical fall {_rh.get('dn_min')}m/{_rh.get('dn_pct')}% → "
                                      f"rise {_rh.get('up_min')}m/+{_rh.get('up_pct')}%" if _rh.get("ok") else ""))
                        else:
                            _tl = (f"\n\n**⏱ 턴 타이밍:** 현재 {'하락' if _leg == 'down' else '상승/보합'} "
                                   f"{_ts.get('leg_run_min')}분째 ({_ts.get('leg_run_pct')}%) · 바닥턴 점수 "
                                   f"{_ts.get('bottom_turn_score')}"
                                   + (" — 🔔 신호 점등" if _ts.get("fire") else "")
                                   + (f" · 이 종목 평균: 하락 {_rh.get('dn_min')}분/{_rh.get('dn_pct')}% → "
                                      f"상승 {_rh.get('up_min')}분/+{_rh.get('up_pct')}%" if _rh.get("ok") else ""))
                        _reply = (_reply or "") + _tl
                except Exception:
                    pass
                # DETAIL LAYER: advice must be detailed — grounded LLM deep-dive (as reco/outlook).
                _extra = _elaborate_answer(transcript, lang, [{"tool": "scalp_signal", "result": _sig}])
                if _extra:
                    _reply = _insert_before_summary(_reply or "", "\n\n" + _extra)
                return {"intent": "scalp", "language": lang, "reply": (_reply or "")[:6000],
                        "action": None, "speak": True, "transcript": transcript, "tool_used": "scalp_signal"}
        except Exception as e:
            log.warning(f"scalp signal failed: {str(e)[:120]}")

    # ===== CHECKLIST — the boss's 100-item pre-trade checklist, agent-run. "삼성전자
    # 체크리스트" → full per-stock scorecard; bare "체크리스트" → today's market pre-flight.
    # MUST run BEFORE the stock-backend relay below, or the relay's LLM composes its own
    # slow (~30s) checklist-ish answer instead of the deterministic 36-item card. KO/EN.
    if not confirmed_tool and not attachment_ids and any(
            # loose stems so typos still land ('checlkis', 'cheklist' — boss types fast)
            k in (transcript or "").lower() for k in ("체크리스트", "체크 리스트", "체크리",
                                                      "checklist", "check list", "checkl",
                                                      "chekl", "checl")):
        try:
            from services.checklist_engine import (CATEGORY_ALIASES, render_category,
                                                   render_en, render_full_en, render_full_ko,
                                                   render_items, render_ko, render_market_en,
                                                   render_market_ko, stock_scorecard)
            from services.stock_resolver import resolve_one
            _cc, _cn = resolve_one(transcript or "")
            _en_l = str(lang or "").lower().startswith("en")
            _tl_ck = (transcript or "").lower()
            if not _en_l and not _re.search(r"[가-힣]", transcript or "") \
                    and _re.search(r"[a-zA-Z]", transcript or ""):
                _en_l = True
            # ① SPECIFIC ITEM(S): "59번이 뭐야", "what is the 59 th of the checklist",
            # "checklist 12, 43" → those exact items from the stored 100 (boss 2026-08-24:
            # "whatever we ask related to the 100 checklist it should tell us").
            # (no \b after 번/th: Hangul counts as \w, so '59번이' has no boundary there)
            _ord = [int(x) for x in _re.findall(r"(\d{1,3})\s*(?:번째|번|th|st|nd|rd)", _tl_ck)]
            # lookarounds instead of \b (Hangul is \w); block decimals/thousands (1,228 / 1.5 / 59%)
            _bare = [int(x) for x in _re.findall(r"(?<![\d.,])(\d{1,2})(?![\d%])(?![.,]\d)", _tl_ck)]
            _nos = sorted({x for x in _ord if 1 <= x <= 100} | {x for x in _bare if 1 <= x <= 99})
            # REASONING beats recital (boss 2026-08-25: "knows the checklist, not
            # memorizing — reasoning"): '왜/why/설명…' about an item falls through to
            # the LLM, which carries the verbatim 100 as injected knowledge.
            if _nos and any(w in _tl_ck for w in ("왜", "이유", "설명", "why", "reason", "explain",
                                                  "어떤 의미", "무슨 의미", "protect")):
                _nos = []
            # ② FULL LIST: "체크리스트 전체/다 보여줘", "list up all checklist", "checklist 100"
            _wants_full = any(k in _tl_ck for k in (
                "전체", "전부", "모든", "모두", "100", "다 보여", "다보여", "리스트업", "list up",
                "all", "full", "whole", "entire", "complete", "everything"))
            # ③ ONE CATEGORY: "준비 항목", "market checklist items" — needs an explicit
            # list word so bare "market checklist" still means the LIVE market pre-flight.
            _cat_key = None
            if any(w in _tl_ck for w in ("항목", "items", "item", "질문", "questions", "list", "보여")):
                for _kw, _key in CATEGORY_ALIASES:
                    if _kw in _tl_ck:
                        _cat_key = _key
                        break
            if _nos:
                _reply = render_items(_nos, en=_en_l)
            elif _cc:
                _card = stock_scorecard(db, _cc)
                _reply = render_en(_card) if _en_l else render_ko(_card)
            elif _cat_key and not _wants_full:
                _reply = render_category(_cat_key, en=_en_l)
            elif _wants_full:
                _reply = render_full_en() if _en_l else render_full_ko()
            elif ((len(_tl_ck) <= 32 or any(w in _tl_ck for w in ("오늘", "today", "지금", "now")))
                  and not any(w in _tl_ck for w in ("왜", "이유", "설명", "why", "reason", "explain",
                                                    "어떤 의미", "무슨 의미"))):
                # short/today-flavored ask → the LIVE market pre-flight (original behavior);
                # reasoning words always fall through to the LLM (knows-not-memorizes)
                _reply = render_market_en(db) if _en_l else render_market_ko(db)
            else:
                # free-form checklist question ("감정 관련 항목은 왜 있어?") → fall through
                # to the LLM, which gets the verbatim 100 items injected as knowledge below.
                _reply = None
            if _reply:
                return {"intent": "checklist", "language": lang, "reply": _reply, "action": None,
                        "speak": True, "transcript": transcript, "tool_used": "checklist"}
        except Exception as e:
            log.warning(f"checklist intent failed: {str(e)[:120]}")

    # ===== MARKET DIRECTION (오늘 코스피/코스닥 방향? / VIX / 유가 / 환율) — deterministic
    # snapshot from the live indicators; steps aside when a specific stock is named. =====
    if (not confirmed_tool and not attachment_ids
            and any(k in (transcript or "").lower() for k in _MKT_DIR_KW)
            and not _all_stocks_in_query(transcript)):
        try:
            _mdr = _market_direction_reply(db, transcript, lang)
            if _mdr:
                return {"intent": "market_direction", "language": lang, "reply": _mdr,
                        "action": None, "speak": True, "transcript": transcript,
                        "tool_used": "market_direction"}
        except Exception as e:
            log.warning(f"market direction lane failed: {str(e)[:120]}")

    # ===== TOP MOVERS ("yesterday which stock mostly increased/decreased?") — real
    # daily data across the 40 candidates, never a recommendation hijack (boss
    # 2026-08-25: this question was answered with the trading top-3). =====
    if (not confirmed_tool and not attachment_ids and _is_movers_q(transcript)
            and not _all_stocks_in_query(transcript)):
        try:
            _mv = _movers_reply(db, transcript, lang)
            if _mv:
                return {"intent": "top_movers", "language": lang, "reply": _mv,
                        "action": None, "speak": True, "transcript": transcript,
                        "tool_used": "top_movers"}
        except Exception as e:
            log.warning(f"movers lane failed: {str(e)[:120]}")

    # ===== STOCK NEWS, deterministic ("한미반도체 뉴스 알려줘") — from OUR news engine
    # (Qwen-scored stamps + live headlines), never an LLM "search tool not working"
    # apology (boss audit 2026-08-24). =====
    _tl_nw = (transcript or "").lower()
    if (not confirmed_tool and not attachment_ids
            and ("뉴스" in _tl_nw or _re.search(r"\bnews\b", _tl_nw))
            and not any(k in _tl_nw for k in ("체크리스트", "checklist"))):
        try:
            from services.stock_resolver import resolve_one as _rn1
            _nc, _nn = (resolve_one_r := _rn1(transcript or "")) or (None, None)
            if _nc:
                _en_n = str(lang or "").lower().startswith("en") or (
                    not _re.search(r"[가-힣]", transcript or "") and _re.search(r"[a-zA-Z]", transcript or ""))
                from services.decision_agent import _news as _news_fn
                _nv = _news_fn(db, _nc, _nn) or {}
                _titles = _nv.get("titles") or []
                _Ln = [(f"**📰 {_nn} — recent news (our Qwen-scored stream)**" if _en_n
                        else f"**📰 {_nn} — 최근 뉴스 (Qwen 판독 기준)**"),
                       (f"News score {_nv.get('score', 0):+d} · {_nv.get('count', 0)} items"
                        if _en_n else f"뉴스 점수 {_nv.get('score', 0):+d} · {_nv.get('count', 0)}건"), ""]
                if _titles:
                    _Ln += [f"- {t_}" for t_ in _titles[:6]]
                else:
                    _Ln.append("최근 수집된 헤드라인이 없습니다 — 뉴스 스탬프는 장중에 갱신됩니다."
                               if not _en_n else
                               "No recent headlines collected — news stamps refresh during market hours.")
                _Ln += ["", (f"Checklist view: [{'evidence' if _en_n else '근거'} 🔍](evidence:{_nc}) · "
                             + (f"ask \"{_nn} checklist\" for the live 100-item card." if _en_n
                                else f"\"{_nn} 체크리스트\"로 100문항 실측을 볼 수 있습니다."))]
                return {"intent": "stock_news", "language": lang, "reply": "\n".join(_Ln),
                        "action": None, "speak": True, "transcript": transcript,
                        "tool_used": "stock_news"}
        except Exception as e:
            log.warning(f"stock news lane failed: {str(e)[:120]}")

    # ===== CHECKLIST SCORE ("한화에어로 지금 점수?" / "what is the score now X") — the
    # ONE true score (0~100 checklist 총점, base + live), never an invented LLM scale
    # (boss 2026-08-25: the analyst lane free-styled a made-up 0-10 'bullish score'). =====
    _tl_sc = (transcript or "").lower()
    if (not confirmed_tool and not attachment_ids
            and ("점수" in _tl_sc or _re.search(r"\bscore\b", _tl_sc))
            and not any(k in _tl_sc for k in ("체크리스트", "checklist"))):
        try:
            from services.stock_resolver import resolve_one as _rs1
            _sc_c, _sc_n = _rs1(transcript or "") or (None, None)
            if _sc_c:
                _en_s = str(lang or "").lower().startswith("en") or (
                    not _re.search(r"[가-힣]", transcript or "") and _re.search(r"[a-zA-Z]", transcript or ""))
                from services.checklist_reco import GROUP_EN, GROUP_KO, _live_state, _ranking
                _rk = _ranking() or {}
                _rows_s = sorted(_rk.get("rows", []), key=lambda r: -(r.get("score") or 0))
                _row_s = next((r for r in _rows_s if r["code"] == _sc_c), None)
                _lv_s = _live_state(db, _sc_c)
                _nm_s = _sc_n or _sc_c
                _Ls = []
                if _row_s:
                    _tot = round((_row_s.get("score") or 0) + _lv_s.get("adj", 0), 1)
                    _rank_pos = _rows_s.index(_row_s) + 1
                    _ap = _lv_s.get("adj_parts") or {}
                    if _en_s:
                        _Ls = [f"**🎯 {_nm_s} — checklist score right now: {_tot} / 100**",
                               f"= morning base {_row_s.get('score')} + live {_lv_s.get('adj', 0):+g} "
                               f"(price {_ap.get('price', 0):+g} · order book {_ap.get('book', 0):+g} · year zone {_ap.get('zone', 0):+g})",
                               f"Rank today: {_rank_pos} of {len(_rows_s)} candidates"
                               + (" · 🟢 trading on the reco desk" if _row_s.get("by_score") else ""),
                               "Groups: " + " · ".join(f"{GROUP_EN[k]} {v}" for k, v in (_row_s.get('groups') or {}).items()),
                               "", f"Details: [근거 🔍](evidence:{_sc_c}) · weights: trend25·liq20·flex20·levels15·mom10·flows10"]
                    else:
                        _Ls = [f"**🎯 {_nm_s} — 지금 체크리스트 점수: {_tot} / 100점**",
                               f"= 아침 기준 {_row_s.get('score')} + 실시간 {_lv_s.get('adj', 0):+g} "
                               f"(등락 {_ap.get('price', 0):+g} · 호가 {_ap.get('book', 0):+g} · 연중구간 {_ap.get('zone', 0):+g})",
                               f"오늘 순위: {len(_rows_s)}종목 중 {_rank_pos}위"
                               + (" · 🟢 추천 데스크에서 매매중" if _row_s.get("by_score") else ""),
                               "그룹: " + " · ".join(f"{GROUP_KO[k]} {v}" for k, v in (_row_s.get('groups') or {}).items()),
                               "", f"자세히: [근거 🔍](evidence:{_sc_c}) · 가중치: 추세25·유동성20·유연성20·지지저항15·모멘텀10·수급10"]
                else:
                    _Ls = [(f"**{_nm_s}** is not in today's 40-candidate scoring universe — "
                            f"the checklist score exists only for tracked candidates." if _en_s else
                            f"**{_nm_s}**은(는) 오늘 채점 대상 40종목에 없어 체크리스트 점수가 계산되지 않습니다.")]
                return {"intent": "checklist_score", "language": lang, "reply": "\n".join(_Ls),
                        "action": None, "speak": True, "transcript": transcript,
                        "tool_used": "checklist_score"}
        except Exception as e:
            log.warning(f"score lane failed: {str(e)[:120]}")

    # ===== RECOMMENDATION EVIDENCE (the proof click): "한미반도체 추천 근거" / "evidence
    # for X recommendation" → the checklist/일봉/분봉/거래량/뉴스 breakdown with item
    # numbers. MUST run before trade-intent — '추천' alone would route to decide(). =====
    _tl_ev = (transcript or "").lower()
    if (not confirmed_tool and not attachment_ids
            and ("추천" in _tl_ev or "recommend" in _tl_ev)
            and any(k in _tl_ev for k in ("근거", "이유", "왜 ", "evidence", "why", "reason", "proof"))):
        try:
            from services.checklist_reco import detail as _cr_detail
            _dv = _cr_detail(db, transcript, lang)
            if _dv:
                return {"intent": "reco_evidence", "language": lang, "reply": _dv,
                        "action": None, "speak": True, "transcript": transcript,
                        "tool_used": "reco_evidence"}
        except Exception as e:
            log.warning(f"reco evidence lane failed: {str(e)[:120]}")

    # ===== STOCK CANDIDATES, typo-tolerant ("give em 3 sotck condidate for trading")
    # → ALWAYS the checklist engine, NEVER the ML predictions tool (boss 2026-08-24). =====
    if (not confirmed_tool and not attachment_ids
            and _wants_candidates(transcript)
            and not _all_stocks_in_query(transcript)):
        try:
            from services.checklist_reco import build as _cr_build2
            _cr2 = _cr_build2(db, n=3, transcript=transcript, lang=lang)
            if _cr2.get("ok") and _cr2.get("reply"):
                return {"intent": "checklist_reco", "language": lang, "reply": _cr2["reply"],
                        "action": None, "speak": True, "transcript": transcript,
                        "tool_used": "checklist_reco", "process": _cr2.get("process")}
        except Exception as e:
            log.warning(f"checklist_reco (candidates route) failed: {str(e)[:120]}")

    # ===== TRADE-INTENT FIRST (order beats guards): any buy/sell ask on a resolvable
    # stock — even misspelled ('skynix') — goes to the 3-method decide composer BEFORE
    # the relay/price routes can swallow it ('from which price should I buy' kept
    # getting price tables because 'price' matched earlier price intercepts). =====
    # (No watchlist exclusion here: the watchlist intercept runs EARLIER and already
    # steps aside when a specific stock resolves — 'how many stock' phrasing must not
    # block a named-stock decision.)
    # (Trade intent TRUMPS the past-price heuristic: '...from which price should I BUY'
    # was still classified past-price in prod. A genuine past-price ask has no buy/sell
    # verb, so wants_reco alone is the right gate.)
    if (not confirmed_tool and not attachment_ids
            and _wants_recommendation(transcript)):
        try:
            from services import prediction_service as _psd0
            _tf = list(dict.fromkeys(c for (c, _n) in _all_stocks_in_query(transcript)
                                     if c in _psd0.NAMES))[:3]
            if not _tf:
                from services.stock_resolver import resolve_one as _r1
                _c0 = (_r1(transcript or "") or (None,))[0]
                if _c0 and _c0 in _psd0.NAMES:
                    _tf = [_c0]
            if _tf:
                _steps = []
                for _dc in _tf:
                    _args = {"ticker": _dc}
                    if _is_sell_timing_q(transcript):
                        _args["focus"] = "sell"
                    _steps.append({"tool": "decide", "args": _args})
                return _run_chain(db, transcript, lang, _steps, current_path,
                                  selected_id, system, history or [], agent_id=agent_id, user_id=user_id)
        except Exception as e:
            log.warning(f"trade-first route failed: {str(e)[:120]}")

    # === Stock agent = relay the Stock-Advisor app (single source of truth) ===
    # The stock agent's answers (and therefore VIP's delegated answers) come from
    # the SAME backend that powers the Stock app's "주식 AI" box, so every surface
    # shows identical data + structure. On failure we fall through to the
    # in-process stock engine below.
    if (not confirmed_tool and not attachment_ids
            and (agent_id or "").lower() == "stock"
            # ADVICE / DECISION / OUTLOOK are handled by OUR local 3-method 'decide'
            # composer (same as VIP), so AI Advisor and VIP give the IDENTICAL answer.
            # Everything else (price, general) still relays to the Stock backend.
            and not _is_stock_advice(transcript, agent_id)
            and not _wants_recommendation(transcript)
            and not _is_future_outlook(transcript)):
        try:
            from services import stock_advisor_chat
            ext = stock_advisor_chat.ask(transcript, lang=lang, history=history or [])
        except Exception as e:
            log.warning(f"stock-advisor relay failed: {str(e)[:120]}")
            ext = None
        if ext and ext.get("reply"):
            return {
                "intent": ext.get("intent") or "stock_advisor",
                "language": lang, "reply": str(ext["reply"])[:9000],
                "action": ext.get("action"), "speak": True,
                "transcript": transcript,
                "tool_used": ext.get("tool_used") or "stock_advisor",
                "source": "stock_advisor",
            }

    # === Multimodal handling (Slice 3) ===
    # When the user attached files, we now have TWO possible flows:
    #
    #   (a) Q&A about the file ("what's in this image?", "summarize this PDF")
    #       → short-circuit to Gemini Vision, no tool routing needed.
    #
    #   (b) ACTION on the file ("send Davronbek this image", "email this PDF
    #       to Kim", "broadcast this screenshot") → go through normal tool
    #       routing with attachment_ids exposed so the LLM passes them to
    #       send_dm / send_email / broadcast.
    #
    # We disambiguate by scanning the transcript for action verbs. If none
    # match, short-circuit to vision (cheap + fast). Otherwise tool-route.
    if attachment_ids:
        action_markers = (
            "send", "email", "broadcast", "share", "forward", "attach",
            "post", "publish", "upload to", "give it to", "give to",
            "보내", "전송", "공유", "전달", "올려",
        )
        tlow = (transcript or "").lower()
        is_action = any(m in tlow for m in action_markers)
        if not is_action:
            return _run_multimodal_path(transcript, lang, history or [], attachment_ids)
        # else: fall through to tool routing — the system prompt will tell
        # the LLM about the pending attachments so it can pass them to
        # the right write tool.
        from routers.chatbot import load_attachment
        pending: list[dict] = []
        for aid in attachment_ids:
            a = load_attachment(aid)
            if a:
                pending.append({
                    "attachment_id": aid,
                    "filename": a.get("filename"),
                    "kind": a.get("kind"),
                    "mime_type": a.get("mime_type"),
                })
        # Carry pending attachments into the system prompt below
        _pending_attachments = pending
    else:
        _pending_attachments = None

    if not transcript:
        return {
            "intent": "empty",
            "language": "en",
            "reply": "I didn't hear anything.",
            "action": None,
            "speak": True,
            "transcript": transcript,
        }

    # === RAG-first retrieval ===
    # Vector-search the agent's uploaded knowledge base BEFORE the LLM
    # decision. Top matches are injected into the system prompt as verbatim
    # excerpts with file/sheet citations. When nothing scores above the
    # similarity floor (rag_retrieve returns []), the prompt has no kb_block
    # and the LLM falls back to its own knowledge — exactly the behaviour
    # the user requested ("first search inside our DB locally, then answer
    # based on his knowledge").
    # SPEED (boss 2026-07-27: "reply must be very fast"): a plain live-price or
    # past-date/history question is answered further below by a DETERMINISTIC lane
    # (the VIP current-price / history tables) — WITHOUT the KB and WITHOUT the LLM.
    # The vector search + file-index lookup here are two cross-region Supabase
    # round-trips (plus an embedding call when embeddings are on) whose results those
    # lanes never read, so skip them for these queries. Guarded to mirror those
    # lanes exactly (advice/outlook/recommendation still get full KB), and offline
    # mode is excluded because it answers straight from the KB.
    _offline_mode = (forced_model or "").strip().lower() in ("none", "offline", "no-llm", "nollm")
    _kb_not_needed = (
        not _offline_mode and not attachment_ids and bool(transcript)
        and (
            _is_vip_current_price_q(transcript, agent_id)
            or (
                bool(_requested_history_dates(transcript))
                and not _is_future_outlook(transcript)
                and not _is_stock_advice(transcript, agent_id)
                and not _wants_recommendation(transcript)
            )
        )
    )

    kb_hits: list[dict] = []
    rag_error: Optional[str] = None
    if not _kb_not_needed:
        try:
            from services.knowledge_ingest import rag_retrieve, EMBED_PROVIDER as _EMB
            # Embedding cosine scores run lower than keyword scores (and lower still
            # cross-lingual EN<->KO), so use a lower floor in semantic mode to avoid
            # filtering valid matches; keyword scores are high so keep 0.35 there.
            _min_sim = 0.25 if _EMB != "none" else 0.35
            kb_hits = rag_retrieve(
                db,
                agent_id=agent_id,
                query=transcript,
                top_k=8,
                min_sim=_min_sim,
            )
            if kb_hits:
                log.info(
                    "rag: %d hits for agent=%s query=%r (top sim=%.2f)",
                    len(kb_hits), agent_id, transcript[:60], kb_hits[0]["similarity"],
                )
        except Exception as e:
            rag_error = str(e)[:200]
            log.warning("rag retrieval failed (continuing without KB): %s", e)

    # Pull the file index regardless of chunk matches so the LLM always
    # knows which files the boss has uploaded. Critical for vague queries
    # like "what files do I have?" / "what do you know about me?" /
    # "내가 올린 파일 알려줘" — questions that don't keyword-match any
    # individual chunk but obviously refer to the uploaded KB.
    kb_files: list[dict] = []
    if not _kb_not_needed:
        try:
            from sqlalchemy import text as _sa_text
            rows = db.execute(_sa_text("""
                SELECT f.filename,
                       f.size_bytes,
                       f.chunk_count,
                       (SELECT c.content FROM assistant_knowledge_chunks c
                        WHERE c.file_id = f.id
                        ORDER BY c.id ASC
                        LIMIT 1) AS preview
                FROM assistant_knowledge_files f
                WHERE f.agent_id = :agent_id
                  AND f.status = 'indexed'
                ORDER BY f.uploaded_at DESC NULLS LAST
                LIMIT 30
            """), {"agent_id": agent_id}).fetchall()
            kb_files = [
                {
                    "filename": r.filename,
                    "size_bytes": r.size_bytes,
                    "chunk_count": r.chunk_count,
                    "preview": r.preview,
                }
                for r in rows
            ]
            if kb_files:
                log.info("file-index: %d files for agent=%s", len(kb_files), agent_id)
        except Exception as e:
            log.warning("file-index lookup failed (continuing without it): %s", e)

    system = _build_system_prompt(
        current_path=current_path,
        selected_id=selected_id,
        pending_attachments=_pending_attachments if attachment_ids else None,
        kb_context=kb_hits,
        kb_files=kb_files,
        agent_id=agent_id,
        page_context=page_context,
    )
    # STRICT per-turn language lock. The soft "match the user's language" hint
    # was being overridden by conversation history (replies drifting to Korean
    # on the 2nd turn, or answering Korean to English). This hard rule, based on
    # the CURRENT message's detected language, fixes voice + text replies.
    _lang_rule = (
        "■ 언어 규칙(필수): 사용자의 이번 메시지는 한국어입니다. 이전 대화 언어나 "
        "지식자료(영어일 수 있음)와 상관없이 반드시 한국어로만 답변하세요. 필요하면 "
        "자료 내용을 한국어로 번역해서 답변하세요.\n\n"
        if lang == "ko" else
        "■ LANGUAGE RULE (strict): The user's CURRENT message is in English. "
        "Reply in English ONLY — ignore the language of earlier turns AND of the "
        "knowledge base. If the source data is in Korean, TRANSLATE the facts "
        "into English in your reply (keep proper nouns/IDs as-is).\n\n"
    )
    system = _lang_rule + system
    # ANSWER-FIRST rule (all agents): a question must be ANSWERED, never auto-open
    # a menu. Navigation only on an explicit open command. Prepended so the model
    # cannot drift into opening pages instead of replying.
    _intent_rule = (
        "■ ANSWER-FIRST (critical): If the user asks a QUESTION or requests "
        "INFORMATION (what / how / why / how much / 얼마 / 뭐 / 무엇 / 어디 / 있어 / "
        "알려줘 / 보여줘-as-question), you MUST ANSWER it in words using your tools "
        "and knowledge. Do NOT call navigate() or open_portal() for a question. "
        "Navigate ONLY when the user gives an explicit OPEN command (open / go to / "
        "take me to / 열어 / 열어줘 / 이동 / 가줘) or confirms an open offer you made. "
        "If you cannot fully answer, say what you found and OFFER 'Want me to open "
        "it?' — never open it unasked.\n\n"
    )
    system = _intent_rule + system
    # GENERAL-LLM style (boss: the chatbot is also his everyday LLM; VIP and the AI
    # Advisor must answer off-topic questions the SAME way — helpful + structured).
    _general_rule = (
        "■ GENERAL QUESTIONS (non-stock: health, travel, writing, life, anything): "
        "answer them HELPFULLY and completely like ChatGPT/Claude would — never refuse, "
        "never say you only handle stocks, never just redirect to a professional "
        "(a one-line 'check with a doctor' note is fine where relevant). FORMAT: start "
        "with a short BOLD one-line direct answer, then 3-6 concise bullet points with "
        "the key facts/steps (bold the keyword of each bullet), and end with one short "
        "practical tip or follow-up question. The bullets are MANDATORY — never answer "
        "in prose paragraphs only. Korean answers use the exact same shape: 굵은 한 줄 "
        "직접 답변 → '- **키워드**: 설명' 불릿 3~6개 → 실용 팁 한 줄.\n\n"
    )
    system = _general_rule + system
    # CURRENT DATE — without this the LLM defaults to its training-cutoff year and
    # wrongly treats recent/past dates as 'the future', refusing answerable
    # questions (e.g. calling a date 7 days ago '12 months in the future').
    from datetime import datetime as _dt2, timezone as _tz2, timedelta as _td2
    _today = _dt2.now(_tz2(_td2(hours=9))).strftime("%Y-%m-%d")
    _date_rule = (
        f"■ TODAY (current date, KST) is {_today}. Treat THIS as 'now' — NOT your "
        f"training cutoff. Any date on or before {_today} is the PAST and you CAN "
        f"answer it from real data; only dates strictly AFTER {_today} are the "
        f"future. Never call a date that is on/before {_today} 'the future'.\n\n"
    )
    system = _date_rule + system
    # Anti-hallucination grounding (Phase 2 / RAG): never invent specific facts;
    # ground them in retrieved knowledge or a tool, else say so / search.
    _ground_rule = (
        "■ GROUNDING (avoid hallucination): Never invent specific facts — prices, "
        "dates, figures, ownership, events, quotes. State such facts ONLY from (a) a "
        "tool result, (b) the KNOWLEDGE BASE excerpts, or (c) a web_search result. If "
        "you cannot ground a factual claim in one of those, say you are not certain or "
        "use web_search — do NOT guess from memory. For past-date stock prices use the "
        "history tool, never recall. You MAY briefly note the basis (e.g. '실시간 시세 기준', "
        "'최근 리포트 기준', 'per web search') without exposing internal filenames.\n\n"
    )
    system = _ground_rule + system
    # KO==EN parity (boss 2026-08-24: "it must answer same format and exactly same in
    # all aspects in Korean and English"): the language changes, the answer does not.
    _parity_rule = (
        "■ LANGUAGE PARITY: A Korean question and its English equivalent MUST get the "
        "IDENTICALLY structured answer — same sections in the same order, same tables "
        "with the same columns, same numbers, same level of detail. Only the language "
        "of the words changes. Never give one language a shorter or differently "
        "shaped answer than the other.\n\n"
    )
    system = _parity_rule + system
    # NO-ML RULE (boss 2026-08-24: retired ML from recommendations — a typo'd ask still
    # reached stock_predictions): any "which stocks to buy/trade/candidates" question is
    # the checklist engine's job; the LLM must never answer it with ML tools.
    _no_ml_rule = (
        "■ RECOMMENDATIONS: NEVER call stock_predictions (ML) for any 'which stocks "
        "should I buy/trade' or 'give me N candidates' question — the boss retired ML "
        "from recommendations. Such questions are answered by the 100-item checklist "
        "engine; if one reaches you, answer that the checklist recommendation handles "
        "it and do NOT produce an ML-based stock list.\n\n"
    )
    system = _no_ml_rule + system
    # 100-ITEM CHECKLIST KNOWLEDGE (boss 2026-08-24: "whatever we ask related to the
    # 100 checklist it should tell us"): free-form checklist questions that the
    # deterministic intercept doesn't cover get the verbatim list as grounded context.
    if any(k in (transcript or "").lower() for k in ("체크리스트", "체크 리스트", "체크리",
                                                     "checklist", "check list", "checkl",
                                                     "chekl", "checl")):
        try:
            from services.checklist_engine import full_checklist
            _ckd = full_checklist()
            _ck_lines = "\n".join(
                f"{i['no']}. [{i['cat']}] {i['q']} / EN: {i.get('q_en', '')}"
                + (" (auto-checked by the agent)" if i.get("auto") else "")
                for i in _ckd["items"])
            system = ("■ THE BOSS'S 100-ITEM TRADING CHECKLIST (authoritative, verbatim — "
                      "answer any checklist question from THIS list, never invent items):\n"
                      + _ck_lines + "\n\n") + system
        except Exception:
            pass
    # UNIVERSAL LIVE GROUND PACK (boss 2026-08-25: "not only for specific questions —
    # it must answer in all cases"): whatever the question shape, a named stock puts
    # its REAL numbers in front of the LLM so there is nothing left to invent.
    try:
        _gp_st = _all_stocks_in_query(transcript)[:2]
        if _gp_st:
            _gp = _ground_pack(db, _gp_st)
            if _gp:
                system = ("■ LIVE DATA for the stock(s) in the question (real, from our own "
                          "systems — ground every number/date in THIS or a tool result; if "
                          "the asked figure is not here and no tool provides it, say exactly "
                          "what is missing and give the closest real figure — NEVER apologize "
                          "into a non-answer, NEVER invent):\n" + _gp + "\n\n") + system
    except Exception:
        pass
    # Deterministic cross-agent pre-router (VIP only): force ask_agent for clear
    # stock/asset questions so they never fall through to web_search.
    _route_hint = _cross_agent_route_hint(transcript, agent_id)
    if _route_hint:
        system = _route_hint + system
    _debug_kb = {
        "agent_id": agent_id,
        "hit_count": len(kb_hits),
        "file_count": len(kb_files),
        "files": [f["filename"] for f in kb_files[:10]],
        "top_hits": [
            {"location": h.get("location"), "similarity": h.get("similarity"),
             "preview": (h.get("content") or "")[:120]}
            for h in kb_hits[:3]
        ],
        "rag_error": rag_error,
        "system_prompt_chars": len(system),
        "page_context_chars": len(page_context) if page_context else 0,
    }

    # Auto-fill ID args from selected_id when the LLM picks a tool that
    # needs an ID but the user said "this" (LLM may not include the ID).
    # Done after LLM decision, see below.

    # ===== No-LLM (offline) mode =====
    # When the user pins model="none"/"offline", answer purely from the
    # knowledge base + the current page — NO cloud LLM call. Returns the best
    # matching snippet, resolves simple navigation by keyword, and otherwise
    # tells the user to turn the LLM on. Private, cheap, LLM-free.
    if (forced_model or "").strip().lower() in ("none", "offline", "no-llm", "nollm"):
        return _offline_answer(db, transcript=transcript, lang=lang,
                               agent_id=agent_id, page_context=page_context,
                               kb_context=kb_hits)

    # ===== NAVER search (web + 네이버 부동산) — deterministic, any agent. Runs BEFORE
    # stock/delegation routing so '네이버에 우리 땅 매물 있어?' isn't handed to the Stock
    # agent. =====
    _naver_prev = None if _is_naver_search_q(transcript) else _naver_more_followup(transcript, history)
    if not confirmed_tool and (_is_naver_search_q(transcript) or _naver_prev):
        # For a bare 'more' follow-up, reuse the PREVIOUS query for the subject and add a
        # clean more-marker (avoids duplicating the subject / leaking '더' into it).
        _tx = transcript if _is_naver_search_q(transcript) else f"{_naver_prev} 더 보여줘"
        _nr = _vip_naver_search_reply(_tx, lang, db)
        if _nr:
            return _nr

    # ===== VIP LOCAL history (past dates / ranges) — deterministic OHLCV table =====
    # 'naver price on 18th/17th/16th of June', 'last 4 days' → a fixed table from VIP's
    # Naver daily data (no LLM), so VIP and the relaying AI Advisor read IDENTICALLY.
    # Falls through (US / unresolved) to the LLM/web-search past path below.
    # ===== BARE stock-switch follow-up ('how about NAVER?', '네이버는?') → INHERIT the
    # previous turn's intent for the new stock, so a price→price follow-up stays a short
    # price answer (not a fresh long analysis). =====
    if not confirmed_tool and history and _is_bare_switch_followup(transcript):
        _sw = _all_stocks_in_query(transcript)
        if _sw:
            _prev = _prev_user_msg(history)
            # prev was a PAST-DATA question ("last 6 days ... prices table" → "how about
            # S-Oil?") → re-run the SAME question for the new stock. Boss 2026-08-25: this
            # switch fell to the LLM chain and answered with Hold ADVICE instead of the
            # same 6-day table. Checked BEFORE the price branch — a dated question can
            # contain price words too.
            if _is_period_stats_q(_prev) or _requested_history_dates(_prev):
                _syn = f"{_sw[0][1]} {_strip_stock_names(_prev)}"
                if _is_period_stats_q(_prev):
                    _pst, _pst_tr = _period_stats_reply(_syn, lang, db=db)
                    if _pst:
                        return {"intent": "stock_period_stats", "language": lang, "reply": _pst,
                                "action": None, "speak": True, "transcript": transcript,
                                "tool_used": "stock_period_stats", "datasource": _pst_tr}
                _hin, _hin_tr = _vip_history_reply(_syn, lang, db=db)
                if _hin:
                    return {"intent": "stock_history", "language": lang, "reply": _hin,
                            "action": None, "speak": True, "transcript": transcript,
                            "tool_used": "stock_history", "datasource": _hin_tr}
            if _is_vip_current_price_q(_prev, agent_id):           # prev was price → price
                _vp = _vip_live_price_reply(transcript, lang, db)
                if _vp:
                    return _vp
            elif _wants_recommendation(_prev) or _is_future_outlook(_prev) or _is_stock_advice(_prev, agent_id):
                try:
                    from services import prediction_service as _ps2
                    _c = next((c for (c, _n) in _sw if c in _ps2.NAMES), None)
                    # prev was a buy/sell RECOMMENDATION → keep advising via the CHECKLIST
                    # engine (boss 2026-08-25: no ML in advice — the decide chain retired);
                    # prev was a pure OUTLOOK → keep forecasting (two_method).
                    if _c and _wants_recommendation(_prev):
                        try:
                            from services import checklist_advice as _ca2
                            _adv2 = _ca2.build(db, f"{_sw[0][1]} 살까?", lang)
                            if _adv2 and _adv2.get("reply"):
                                return {"intent": "checklist_advice", "language": lang,
                                        "reply": _adv2["reply"], "action": None, "speak": True,
                                        "transcript": transcript, "tool_used": "checklist_advice",
                                        "process": _adv2.get("process")}
                        except Exception:
                            pass
                    if _c and "two_method_view" in TOOL_REGISTRY:
                        _st = [{"tool": "two_method_view", "args": {"ticker": _c}}]
                        if "read_chart" in TOOL_REGISTRY:
                            _st.append({"tool": "read_chart", "args": {"ticker": _c}})
                        return _run_chain(db, transcript, lang, _st, current_path,
                                          selected_id, system, history or [], agent_id=agent_id, user_id=user_id)
                except Exception:
                    pass

    # ===== PERIOD STATS ("최근 6개월 최저/최고/거래량", "last 6 months min/max/volume") —
    # months-long summary table + explanation. Runs BEFORE the history-table lane (which
    # only knows day/week ranges) and before current-price (which would answer TODAY). =====
    if (not confirmed_tool and not _is_future_outlook(transcript)
            and not _is_stock_advice(transcript, agent_id)
            and not _wants_recommendation(transcript)
            and _is_period_stats_q(transcript)):
        _pstats, _ps_trace = _period_stats_reply(transcript, lang, history=history or [], db=db)
        if _pstats:
            return {"intent": "stock_period_stats", "language": lang, "reply": _pstats,
                    "action": None, "speak": True, "transcript": transcript,
                    "tool_used": "stock_period_stats", "datasource": _ps_trace}

    if (not confirmed_tool and (agent_id or "vip").lower() != "stock"
            and not _is_future_outlook(transcript)        # '앞으로 5일 전망' is a FORECAST, not history
            and not _is_stock_advice(transcript, agent_id)   # 'last week I bought X, hold or sell?' = ADVICE
            and not _wants_recommendation(transcript)               # not a price-history dump
            and _requested_history_dates(transcript)):
        _hist, _h_tr = _vip_history_reply(transcript, lang, history=history or [], db=db)
        if _hist:
            return {"intent": "stock_history", "language": lang, "reply": _hist,
                    "action": None, "speak": True, "transcript": transcript,
                    "tool_used": "stock_history", "datasource": _h_tr}

    # ===== VIP LOCAL current-price (the rich single source) =====
    # VIP holds the Kiwoom key, so it answers current-price HERE — Kiwoom during market
    # / Naver after, with opening/high/low/volume when asked and an always-on source
    # label. The AI Advisor relays this exact answer, so both surfaces read identically.
    # A bare field follow-up ('시가, 고가, 저가, 거래량' after '삼성전자 현재가') has no
    # stock name → borrow it from the conversation so we answer the SAME stock's
    # current-day fields, not a 5-day history dump.
    _cp_tx = transcript
    if (not confirmed_tool and not _is_vip_current_price_q(transcript, agent_id)
            and _is_price_field_followup(transcript)):
        _ps = _recent_stock_name(history)
        if _ps:
            _cp_tx = f"{_ps} {transcript}"
    if not confirmed_tool and _is_vip_current_price_q(_cp_tx, agent_id):
        _vp = _vip_live_price_reply(_cp_tx, lang, db)
        if _vp:
            return _vp

    # ===== Day-trade / stop follow-up ('손절은?', 'where's the stop?', 'target?') with NO
    # stock named → borrow the stock from the recent context and answer with the REAL
    # day_trade levels (stop / buy zone / target), never an LLM-guessed number. =====
    if (not confirmed_tool and "day_trade" in TOOL_REGISTRY
            and _is_daytrade_followup(transcript) and not _all_stocks_in_query(transcript)):
        _dt_code = None
        for _h in reversed(history or []):
            _st = _all_stocks_in_query(_h.get("content") or _h.get("text") or _h.get("transcript") or "")
            if _st:
                _dt_code = _st[0][0]
                break
        try:
            from services import prediction_service as _ps2
            if _dt_code and _dt_code in _ps2.NAMES:
                return _run_chain(db, transcript, lang,
                                  [{"tool": "day_trade", "args": {"ticker": _dt_code}}],
                                  current_path, selected_id, system, history or [], agent_id=agent_id, user_id=user_id)
        except Exception:
            pass

    # ===== Market-wide investor flow ('who's buying KOSPI today', '오늘 외국인 순매수?')
    # → real market_flows tool, IDENTICAL EN + KO (before stock-delegation, which would
    # otherwise send KO to the Stock agent and EN to the LLM = inconsistent). =====
    if (not confirmed_tool and "market_flows" in TOOL_REGISTRY
            and _is_market_flow_q(transcript)):
        return _run_chain(db, transcript, lang, [{"tool": "market_flows", "args": {}}],
                          current_path, selected_id, system, history or [], agent_id=agent_id, user_id=user_id)

    # Fetch evidence before prose for current market-overview questions. This
    # prevents the downstream Stock Advisor from treating tool use as optional
    # and producing a generic market narrative instead.
    if not confirmed_tool and _requires_fresh_market_evidence(transcript):
        evidence_steps = [
            {"tool": "stock_get_market_summary", "args": {}},
            {"tool": "stock_get_investor_flow", "args": {}},
            {"tool": "stock_get_news", "args": {}},
        ]
        evidence_steps = [step for step in evidence_steps if step["tool"] in TOOL_REGISTRY]
        if evidence_steps:
            return _run_chain(
                db, transcript, lang, evidence_steps, current_path, selected_id,
                system, history or [], agent_id=agent_id, user_id=user_id,
                fresh_market_evidence=True,
            )
        unavailable = (
            "실시간 시장 데이터 도구를 사용할 수 없어 오늘 시장 흐름을 근거 기반으로 분석할 수 없습니다."
            if lang == "ko" else
            "Live market-data tools are unavailable, so I cannot provide an evidence-based analysis of today's market."
        )
        return {
            "intent": "market_evidence_unavailable", "language": lang,
            "reply": unavailable, "action": None, "speak": True,
            "transcript": transcript,
        }

    # ===== 공매도 (short-selling) — answer LOCALLY from VIP's Kiwoom (ka10014). The
    # Stock backend's 공매도 tool currently returns '확인 불가' (no data), but VIP's Kiwoom
    # key returns REAL short-selling figures, so VIP serves this one itself. =====
    if (not confirmed_tool and (agent_id or "vip").lower() != "stock"
            and _is_short_selling_q(transcript)):
        ss = _vip_short_selling_reply(transcript, lang)
        if ss:
            return ss

    # ===== BUY/SELL DECISION agent ('사야 할까/팔까', 'buy or sell', '종합 판단') → the
    # comprehensive 3-factor decision (News + Flows + Technicals + ML). Runs BEFORE
    # stock-delegation so it isn't swallowed by the generic Stock-agent path. =====
    _pending_dec = _pending_clarify_decision(history)
    if not confirmed_tool and "decide" in TOOL_REGISTRY and (_is_decision_q(transcript)
                                                             or _is_sell_timing_q(transcript)
                                                             or _pending_dec):
        try:
            from services import prediction_service as _psd
            # MULTI-STOCK: '삼성전자랑 SK하이닉스 살까?' → decide per stock (up to 3), so
            # every asked name gets its own verdict (boss feedback: no stock skipped).
            _dcs = list(dict.fromkeys(c for (c, _n) in _all_stocks_in_query(transcript)
                                      if c in _psd.NAMES))[:3]
            if not _dcs:
                # FUZZY fallback for misspellings ('skynix') the substring pass misses.
                # ALSO the bare stock-name reply to the clarify prompt ('skhynix').
                try:
                    from services.stock_resolver import resolve_one
                    _fz = (resolve_one(transcript or "") or (None,))[0]
                    if _fz and _fz in _psd.NAMES:
                        _dcs = [_fz]
                except Exception:
                    pass
            # PRONOUN FOLLOW-UP (2026-08-03): '그거 사야 할까?' / 'should I buy it?' right
            # after a turn about a stock used to hit the clarify prompt — asking the boss
            # for a name the conversation had just established. Borrow the last stock we
            # actually discussed. Guarded so an open-ended '뭐 사야 할까?' / 'what should I
            # buy?' still reaches the watchlist scanner instead of being answered about
            # whatever happened to be mentioned earlier.
            if (not _dcs and history and not _is_watchlist_question(transcript)
                    and (_PRONOUN_REF_RE.search(transcript or "")
                         or len((transcript or "").strip()) <= 25)):
                for _h in reversed(history):
                    _body = str(_h.get("content") or _h.get("text") or "")
                    _prev = [c for (c, _n) in _all_stocks_in_query(_body) if c in _psd.NAMES]
                    if _prev:
                        _dcs = _prev[:1]
                        break
            if _dcs:
                _sell_focus = _is_sell_timing_q(transcript) or (_pending_dec and _recent_sell_intent(history))
                _steps = []
                for _dc in _dcs:
                    _args = {"ticker": _dc}
                    if _sell_focus:
                        _args["focus"] = "sell"
                    _steps.append({"tool": "decide", "args": _args})
                return _run_chain(db, transcript, lang, _steps,
                                  current_path, selected_id, system, history or [], agent_id=agent_id, user_id=user_id)
            if not _is_watchlist_question(transcript):
                # trade intent but NO resolvable stock: ASK — never fall through to a
                # price-history/LLM guess (the 'silly answer' class the boss keeps hitting)
                _en_l = str(lang or "").lower().startswith("en")
                _cl = ("Which stock do you mean? Please give the name a bit more precisely "
                       "(e.g. 'SK Hynix', 'Samsung Electronics') — then I'll run the full "
                       "buy/sell analysis with sizing and levels."
                       if _en_l else
                       "어떤 종목을 말씀하시는지 정확히 알려주시겠어요? (예: 'SK하이닉스', "
                       "'삼성전자') — 종목이 확인되면 매수/매도 판단과 수량·가격까지 바로 분석해 드릴게요.")
                return {"intent": "clarify_stock", "language": lang, "reply": _cl,
                        "action": None, "speak": True, "transcript": transcript,
                        "tool_used": None}
        except Exception:
            pass

    # ===== VIP → Stock delegation (single source of truth) =====
    # ANY stock question asked in VIP (or another non-stock agent) is answered by
    # the Stock agent itself — verbatim transcript, same engine — so VIP and Stock
    # ALWAYS give the same answer. Runs before the per-topic short-circuits below.
    _stock_turn = (_is_stock_question(transcript)
                   or (_recent_stock_context(history) and any(
                       k in (transcript or "").lower() for k in _STOCK_FOLLOWUP_KW)))
    if (not confirmed_tool and _STOCK_DELEGATE and (agent_id or "vip").lower() != "stock"
            and "ask_agent" in TOOL_REGISTRY and _stock_turn
            and not _is_past_price(transcript)
            and not _is_future_outlook(transcript)        # forecast → local two-method, not delegate
            and not _is_stock_advice(transcript, agent_id)  # advice ('살까/사는 게 좋아?') → local 3-method
            and not _wants_recommendation(transcript)              # 'buy or sell/사야 할까' → local decide (3-method)
            and not _is_report_question(transcript)
            and not _is_concept_question(transcript)):
        # FAST PATH (latency): the Stock backend is the single source of truth, so
        # call it DIRECTLY — capped at _STOCK_TIMEOUT so a slow/cold Render peer never
        # hangs the reply. If it returns nothing in time, fall straight through to VIP's
        # OWN local handling below (never the 2nd 45s ask_agent round-trip to the same peer).
        ans = None
        try:
            from services.stock_advisor_chat import ask as _stock_direct
            _d = _stock_direct(transcript, lang, history or [], timeout=_STOCK_TIMEOUT)
            if isinstance(_d, dict):
                cand = (_d.get("reply") or "").strip()
                if cand and not cand.startswith(("{", "[")):
                    ans = cand
        except Exception as _e:
            log.warning(f"stock direct fast-path failed: {str(_e)[:120]}")
        if ans:
            return {"intent": "stock_delegated", "language": lang,
                    "reply": str(ans)[:1600], "action": None, "speak": True,
                    "transcript": transcript, "tool_used": "ask_agent",
                    "tool_result": {"direct": True}}
        # Resilience: the Stock backend returned nothing usable (down, mid-deploy, or a
        # query type it currently fails on). Answer current-price and 공매도 from VIP's
        # OWN Kiwoom so the user never sees a blank reply.
        if _is_short_selling_q(transcript):
            _ss_fb = _vip_short_selling_reply(transcript, lang)
            if _ss_fb:
                return _ss_fb
        _vp_fb = _vip_live_price_reply(transcript, lang, db)
        if _vp_fb:
            return _vp_fb
        # If the Stock agent gave nothing usable, fall through to the normal path.

    # ===== Deterministic CURRENT-PRICE (stock agent) =====
    # Bare price questions are answered by formatting the quote directly — no LLM,
    # so it never garbles or leaks raw JSON, and is byte-identical to VIP's relay.
    if (not confirmed_tool and (agent_id or "vip").lower() == "stock"
            and _is_price_question(transcript)):
        # Use the SAME canonical live-price path as the VIP agent (Kiwoom during
        # market / Naver after, shared formatter) so this stock-agent fallback —
        # reached only when the Stock backend relay is down — still reads identically.
        vp = _vip_live_price_reply(transcript, lang, db)
        if vp:
            return vp

    # ===== Deterministic PAST-DATE price routing =====
    # 'X 어제 종가 / 10일 전 주가 / June 10 close' MUST come from real daily history,
    # never the LLM's memory (it otherwise hallucinates a number). The live Naver
    # daily-history tool is the authority and is the SAME tool for VIP & Stock, so
    # both give identical answers. If there is no KR daily data (US stock, ticker
    # unresolved, or the date is out of range), fall back to web-search grounding so
    # we still answer past dates like a general assistant would (with sources) —
    # i.e. the same behavior as Google AI Mode, instead of relaying a "can't" reply.
    if not confirmed_tool and _is_past_price(transcript) and not _wants_recommendation(transcript):
        # Delegate past-date prices to the Stock backend FIRST (single deterministic
        # source of truth) so VIP and the AI Advisor give the IDENTICAL concise
        # answer. Fall back to the local daily-history chain / web search only if the
        # relay returns nothing usable.
        try:
            from services.stock_advisor_chat import ask as _stock_past
            _p = _stock_past(transcript, lang, history or [], timeout=_STOCK_TIMEOUT) if _STOCK_DELEGATE else None
            if isinstance(_p, dict):
                cand = (_p.get("reply") or "").strip()
                # The Stock backend's history window is short (~2 months); a date beyond
                # it comes back as "not available / out of range". DON'T surface that —
                # fall through to our local Naver chain (now ~18 months) + web search.
                _unavail = any(k in cand.lower() for k in (
                    "not available", "out of range", "outside", "falls outside",
                    "don't have", "do not have", "unable to", "cannot", "can't",
                    "제공되지 않", "제공하지 않", "범위를 벗어", "범위 밖", "없습니다", "확인되지 않"))
                if cand and not cand.startswith(("{", "[")) and not _unavail:
                    return {"intent": "stock_past_price", "language": lang,
                            "reply": str(cand)[:1600], "action": None, "speak": True,
                            "transcript": transcript, "tool_used": "stock_advisor",
                            "tool_result": {"direct": True}}
        except Exception as _e:
            log.warning(f"stock past relay failed: {str(_e)[:120]}")
        days = _history_days_for(transcript)
        if "stock_get_daily_history" in TOOL_REGISTRY:
            probe = execute_tool("stock_get_daily_history",
                                 {"query": transcript, "days": days},
                                 db=db, agent_id=agent_id, transcript=transcript)
            if isinstance(probe, dict) and probe.get("ok") and probe.get("history"):
                hist_steps = [{"tool": "stock_get_daily_history",
                               "args": {"query": transcript, "days": days}}]
                return _run_chain(db, transcript, lang, hist_steps, current_path,
                                  selected_id, system, history or [], agent_id=agent_id, user_id=user_id)
        if "web_search" in TOOL_REGISTRY:
            ws_steps = [{"tool": "web_search", "args": {"query": transcript}}]
            return _run_chain(db, transcript, lang, ws_steps, current_path,
                              selected_id, system, history or [], agent_id=agent_id, user_id=user_id)

    # ===== Deterministic STOCK-ADVICE routing =====
    # The LLM (especially for Korean) tends to answer 'should I buy X / X 어때?'
    # with the bare current price. For a clear single-stock ADVICE question we
    # force the analysis path instead — chain the live tools (stock agent) or
    # relay to the Stock agent verbatim (VIP / others) so the user always gets a
    # reasoned 매수/보유/매도 view, never just a number.
    # Advice ('살까/sell?') OR a future-outlook ('전망/outlook/5-day forecast') on a
    # registered stock → force the deterministic two-method chain. Including outlook here
    # keeps EN and KO IDENTICAL: KO '전망' matched advice, but EN 'outlook' did not, so EN
    # fell to a prose LLM summary while KO got the structured 방법1/방법2 block.
    if not confirmed_tool and (_is_stock_advice(transcript, agent_id)
                               or _is_future_outlook(transcript)
                               or _wants_recommendation(transcript)):   # situational: 'sell now?/팔면 이득?'
        # TWO-METHOD FIRST: for advice on a stock that's in OUR model universe, force
        # our own ML + Analysis view (+ chart) so the answer ALWAYS shows BOTH methods
        # explicitly — the LLM tended to pick read_chart alone and merge them.
        try:
            from services import prediction_service as _ps
            _found = _all_stocks_in_query(transcript)         # [(code, name), ...]
            _codes = list(dict.fromkeys(c for (c, _n) in _found if c in _ps.NAMES))[:3]
            _code = _codes[0] if _codes else None
        except Exception:
            _codes, _code = [], None
        # SPLIT (do not merge): a buy/sell/hold ACTION ('사야 할까/살까/should I buy') → the
        # friend-style 'decide' recommendation (verdict + sizing + proof). A pure OUTLOOK
        # ('전망/향후/outlook/어때') → the detailed forecast (two_method_view). Both composers
        # keep EN==KO and VIP==AI Advisor, but the two answers are DIFFERENT by design.
        if _code and _wants_recommendation(transcript) and "decide" in TOOL_REGISTRY:
            _steps = []
            for _dc in _codes:                    # MULTI-STOCK: verdict per asked name
                _args = {"ticker": _dc}
                if _is_sell_timing_q(transcript):
                    _args["focus"] = "sell"
                _steps.append({"tool": "decide", "args": _args})
            return _run_chain(db, transcript, lang, _steps,
                              current_path, selected_id, system, history or [], agent_id=agent_id, user_id=user_id)
        if _code and "two_method_view" in TOOL_REGISTRY:
            tm_steps = [{"tool": "two_method_view", "args": {"ticker": _code}}]
            if "read_chart" in TOOL_REGISTRY:
                tm_steps.append({"tool": "read_chart", "args": {"ticker": _code}})
            return _run_chain(db, transcript, lang, tm_steps, current_path,
                              selected_id, system, history or [], agent_id=agent_id, user_id=user_id)
        # GENERIC "what should I buy?" (recommendation wanted, NO stock named) →
        # deterministic 3-method top-picks. This used to fall through to a raw LLM
        # chain that gave vague/truncated answers ("Final Recommendation" with no body).
        if _wants_recommendation(transcript) and not _code and not _is_sell_timing_q(transcript):
            # OUR ALGO, NOT ML (boss 2026-08-24) — checklist ranking first, buy_picks fallback.
            try:
                from services.checklist_reco import build as _cr_build
                _cr = _cr_build(db, n=3, transcript=transcript, lang=lang)
                if _cr.get("ok") and _cr.get("reply"):
                    return {"intent": "checklist_reco", "language": lang, "reply": _cr["reply"],
                            "action": None, "speak": True, "transcript": transcript,
                            "tool_used": "checklist_reco", "process": _cr.get("process")}
            except Exception as e:
                log.warning(f"checklist_reco (generic route) failed: {str(e)[:120]}")
            try:
                from services.buy_picks import build as _bp_build
                _bp = _bp_build(db, n=3, transcript=transcript,
                                user_key=user_id, lang=lang)
                if _bp.get("reply"):
                    return {"intent": "buy_picks", "language": lang, "reply": _bp["reply"],
                            "action": None, "speak": True, "transcript": transcript,
                            "tool_used": "buy_picks"}
            except Exception as e:
                log.warning(f"buy_picks failed: {str(e)[:160]}")
        aid = (agent_id or "vip").lower()
        if aid == "stock":
            advice_steps = [
                {"tool": "stock_quote", "args": {"query": transcript}},
                {"tool": "stock_get_investor_flow", "args": {}},
                {"tool": "stock_get_intraday_signals", "args": {}},
                {"tool": "stock_get_recommendations", "args": {}},
                {"tool": "stock_get_news", "args": {}},
            ]
            advice_steps = [s for s in advice_steps if s["tool"] in TOOL_REGISTRY]
            if advice_steps:
                return _run_chain(db, transcript, lang, advice_steps, current_path,
                                  selected_id, system, history or [], agent_id=agent_id, user_id=user_id)
        elif "ask_agent" in TOOL_REGISTRY:
            # Pass the user's EXACT question (verbatim) so nothing is garbled, and
            # let the Stock agent run its own advice chain.
            res = execute_tool("ask_agent", {"agent": "stock", "question": transcript},
                               db=db, agent_id=agent_id, transcript=transcript)
            ans = None
            if isinstance(res, dict):
                for a in (res.get("answers") or []):
                    if a.get("answer"):
                        ans = a["answer"]
                        break
            if ans:
                return {
                    "intent": "stock_advice", "language": lang,
                    "reply": str(ans)[:1400], "action": None, "speak": True,
                    "transcript": transcript, "tool_used": "ask_agent",
                    "tool_result": res,
                }
        # else: fall through to the normal LLM path

    # ===== Turn 1: decision =====
    decision = _call_llm_for_decision(system, transcript, history or [], forced_model=forced_model)

    # Safety net: the model emitted a tool/steps decision that didn't parse
    # (usually truncated). NEVER show raw JSON — retry once forcing a direct
    # natural-language answer on the stronger model.
    if decision.get("_unparsed_decision"):
        decision = _call_llm_for_decision(
            system + "\n\n■ Your previous output was unparseable. Reply with the "
            "{\"answer\": \"...\"} shape ONLY — a direct, complete natural-language "
            "answer in the user's language. Do NOT use tools or steps.",
            transcript, history or [], forced_model="claude-sonnet-4-6")
        if decision.get("_unparsed_decision") or (not decision.get("answer")
                                                  and not decision.get("tool")
                                                  and not decision.get("steps")):
            return {
                "intent": "llm_chat", "language": lang,
                "reply": ("죄송합니다, 방금 질문을 처리하지 못했습니다. 조금만 다르게 다시 "
                          "물어봐 주시겠어요?" if lang == "ko" else
                          "Sorry — I couldn't process that just now. Could you rephrase it?"),
                "action": None, "speak": True, "transcript": transcript,
                "tool_used": None,
            }

    # ===== Phase 5: Multi-step chain =====
    steps = decision.get("steps")
    if isinstance(steps, list) and len(steps) > 0:
        return _run_chain(db, transcript, lang, steps, current_path, selected_id, system, history or [], agent_id=agent_id, user_id=user_id)

    # If the LLM chose to answer directly, return it
    if decision.get("answer") and not decision.get("tool"):
        # UNIVERSAL NO-APOLOGY GUARD (boss 2026-08-25 "must answer in all cases"): a
        # refusal about a NAMED stock is replaced with what we actually know.
        if _looks_refusal(str(decision.get("answer") or "")):
            try:
                if _all_stocks_in_query(transcript):
                    _fb = (_fundamentals_reply(transcript, lang, db)
                           or _vip_stock_data_reply(transcript, lang, db=db))
                    if _fb:
                        return {"intent": "stock_data", "language": lang, "reply": _fb,
                                "action": None, "speak": True, "transcript": transcript,
                                "tool_used": "stock_data"}
            except Exception as e:
                log.warning(f"no-apology fallback failed: {str(e)[:120]}")
        return {
            "intent": "llm_chat",
            "language": lang,
            "reply": str(decision["answer"])[:2400],
            "action": None,
            "speak": True,
            "transcript": transcript,
            "tool_used": None,
            "_debug_kb": _debug_kb,
            "suggestions": [
                ("What can you do?" if lang != "ko" else "뭘 할 수 있어?"),
                ("Show today's situation" if lang != "ko" else "오늘 상황 보여줘"),
                ("Open the dashboard" if lang != "ko" else "대시보드 열어"),
            ],
        }

    # If the LLM chose a tool
    tool_name = (decision.get("tool") or "").strip()
    args = decision.get("args") or {}

    if not tool_name or tool_name not in TOOL_REGISTRY:
        # Hallucinated tool — degrade to answer
        log.warning(f"assistant_agent: LLM picked unknown tool '{tool_name}'")
        return {
            "intent": "llm_chat",
            "language": lang,
            "reply": (decision.get("answer") or "I'm not sure how to help with that — could you rephrase?")[:2400],
            "action": None,
            "speak": True,
            "transcript": transcript,
            "tool_used": None,
        }

    tool = TOOL_REGISTRY[tool_name]

    # === Phase 4: Page-context auto-fill ===
    # If the user said "this" and the tool needs an ID arg that wasn't
    # populated by the LLM, fill from selected_id.
    if selected_id:
        id_keys = ("conversation_id", "report_id", "twin_id", "meeting_id",
                   "handoff_id", "task_id", "knowledge_id")
        for k in id_keys:
            if k in (tool.parameters.get("properties") or {}) and not args.get(k):
                args[k] = selected_id
                break

    # === PERMISSION GATE for WRITE tools (Phase 3) ===
    # If the picked tool is a write/destructive action, DO NOT execute.
    # Instead return a proposed_action so the frontend can render a
    # confirm card. User clicks Confirm → widget re-calls /chat/agent
    # with confirmed_tool + confirmed_args.
    if tool.requires_confirmation:
        # Carry current_path so previews / re-runs have it
        preview_args = dict(args or {})
        if current_path and "current_path" not in preview_args:
            preview_args["current_path"] = current_path
        # Compose a human-readable preview
        preview = _compose_write_preview(tool_name, preview_args)
        return {
            "intent": tool_name,
            "language": lang,
            "reply": preview["message"],
            "action": None,
            "speak": True,
            "transcript": transcript,
            "tool_used": None,
            "proposed_action": {
                "tool": tool_name,
                "args": preview_args,
                "summary": preview["message"],
                "details": preview.get("details"),
                "requires_confirmation": True,
            },
        }

    # READ tools execute immediately
    # recall_history needs to know whose history to search — inject user_id
    if tool_name == "recall_history" and user_id and "user_id" not in args:
        args["user_id"] = user_id
    tool_result = execute_tool(tool_name, args, db=db, agent_id=agent_id, transcript=transcript)

    # ANSWER-FIRST guard: never auto-navigate for a QUESTION. If the LLM chose
    # navigate/open_portal but the user didn't explicitly ask to open (or confirm
    # an offer), turn it into an OFFER instead of moving the page.
    if (tool_name in ("navigate", "open_portal")
            and isinstance(tool_result, dict) and tool_result.get("action")
            and not _is_open_intent(transcript)):
        to = (tool_result.get("action") or {}).get("to") or ""
        if lang == "ko":
            msg = (f"원하시면 {to + ' ' if to else ''}페이지를 열어드릴까요? "
                   f"'열어'라고 말씀해 주세요.")
        else:
            msg = (f"I can open {to or 'that page'} for you — just say "
                   f"\"open it\" and I will.")
        tool_result = {"ok": True, "message": msg, "action": None, "_offer": True}
        tool_name = "offer"

    # === Phase 6: Build inline result card ===
    card = _build_card(tool_name, tool_result)

    # If the tool itself returned an action (navigate, open_portal, etc.),
    # surface it to the frontend so the widget can execute it.
    action = tool_result.get("action") if isinstance(tool_result, dict) else None

    # Compose the natural-language reply from tool data
    if tool_result.get("_offer") and tool_result.get("message"):
        # Use the clean canned offer message verbatim (no 2nd LLM rephrase).
        reply = tool_result["message"]
    elif action and tool_result.get("message"):
        # Navigation tools have a clean canned message — skip a 2nd LLM call
        reply = tool_result["message"]
        # The canned navigate message is English; localize it for Korean users
        # so voice/text confirmations match the spoken language.
        if lang == "ko" and isinstance(action, dict) and action.get("type") == "navigate":
            if action.get("external"):
                reply = "새 탭에서 페이지를 엽니다. 🙂"
            else:
                _to = action.get("to") or ""
                reply = (f"{_to} 페이지를 엽니다." if _to else "페이지를 엽니다.")
    else:
        reply = _compose_final_answer(system, transcript, tool_name, tool_result, history or [])

    return {
        "intent": tool_name,
        "language": lang,
        "reply": reply[:1500] if reply else "",
        "action": action,
        "speak": True,
        "transcript": transcript,
        "tool_used": tool_name,
        "tool_result": tool_result if tool.kind == "read" else None,
        "card": card,
        # Notion-AI-style follow-up chips (rendered by the overlay)
        "suggestions": _suggest_followups(tool_name, tool_result, lang),
    }
