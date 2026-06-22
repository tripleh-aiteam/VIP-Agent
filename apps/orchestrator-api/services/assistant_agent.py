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
    "should i buy", "should i sell", "should i hold", "worth buying", "worth it",
    "good buy", "good time to", "entry point", "buy or sell", "sell or hold",
    "hold or sell", "is it a good", "invest in", "go long", "thoughts on", "a good buy",
)


def _stock_in_query(transcript: Optional[str]) -> Optional[str]:
    """Return a known stock name / 6-digit code found in the text, else None."""
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
_PRICE_WORDS = ("현재가", "시세", "주가", "얼마", "price", "quote",
                "시가", "고가", "저가", "opening", "open price", "high price", "low price")
# Generic stock-price phrasing with NO specific company → show the watchlist.
_GENERIC_STOCK_WORDS = ("stock", "주가", "주식", "종목", "시세", "현재가")
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
        keys = list(norm.keys())
        for w in _re.split(r"[\s,/&]+", consumed):
            w = w.strip()
            if len(w) < 4 or w in _STOCK_FUZZY_STOP:
                continue
            hit = difflib.get_close_matches(w, keys, n=1, cutoff=0.82)
            if hit:
                name = norm[hit[0]]
                code = str(_NAME_TO_TICKER[name])
                if code.isdigit() and code not in seen:
                    seen.add(code)
                    out.append((code, name))
    return out


def _requested_price_fields(transcript: Optional[str]) -> list[str]:
    """Which quote values the user asked for, in display order. 'opening and current
    price' -> ['open','price']; 'high and low' -> ['high','low']; bare price -> ['price'].
    Lets the answer cover MULTIPLE fields, not just current."""
    t = (transcript or "").lower()
    fields: list[str] = []
    if "시가" in t or "open price" in t or "opening" in t or _re.search(r"\bop\w*ning\b", t):
        fields.append("open")
    if "고가" in t or "highest" in t or "high price" in t or "day high" in t or "intraday high" in t:
        fields.append("high")
    if "저가" in t or "lowest" in t or "low price" in t or "day low" in t or "intraday low" in t:
        fields.append("low")
    cur = (any(k in t for k in ("current", "현재", "지금", "real-time", "실시간", "latest", "now"))
           or any(k in t for k in ("price", "주가", "시세", "얼마", "quote")))
    if cur or not fields:
        fields.append("price")
    if "거래량" in t or "volume" in t or "거래대금" in t:
        fields.append("volume")
    return fields


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


def _is_vip_current_price_q(transcript: Optional[str], agent_id: Optional[str]) -> bool:
    if (agent_id or "vip").lower() == "stock":
        return False
    t = (transcript or "").lower()
    if not any(w in t for w in _PRICE_WORDS):
        return False
    if (_is_past_price(transcript) or _is_stock_advice(transcript, agent_id)
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


def _vip_live_price_reply(transcript: Optional[str], lang: str) -> Optional[dict]:
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

    # Format via the shared canonical formatter so VIP and the AI Advisor (which
    # relays this answer) phrase it the same way.
    from services import price_format
    fmt_quotes = [{"name": q["name"], "price": q["price"],
                   "change_pct": q["change_pct"], "market": "KR",
                   "open": q.get("open"), "high": q.get("high"), "low": q.get("low"),
                   "volume": q.get("volume"),
                   "source": _canon_price_src(q["source"])} for q in quotes]
    reply = price_format.format_current(
        fmt_quotes, lang=("en" if _en else "ko"),
        used_watchlist=used_watchlist, as_of=now, fields=fields)

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
    if _is_history_range_query(q):
        return ("range", 7)
    iso = _relative_date_iso(q)
    if iso:
        try:
            return ("dates", [_date.fromisoformat(iso)])
        except ValueError:
            pass
    return None


def _vip_history_reply(transcript: Optional[str], lang: str, hist=None) -> Optional[str]:
    """Deterministic multi-day OHLCV table from Naver daily history — past specific
    dates AND ranges ('last 4 days'). Single source, so VIP and the relaying AI Advisor
    read IDENTICALLY. None → caller falls through."""
    hist = hist or _requested_history_dates(transcript)
    if not hist:
        return None
    stocks = _all_stocks_in_query(transcript)
    if not stocks:
        return None
    from services import naver_stock, price_format
    _en = (lang or "").lower().startswith("en")
    if not _en and not _re.search(r"[가-힣]", transcript or "") \
            and _re.search(r"[a-zA-Z]", transcript or ""):
        _en = True
    kind, payload = hist
    out = []
    for code, name in stocks[:6]:
        try:
            rows = naver_stock.daily_history(code, days=(payload + 3 if kind == "range" else 60))
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
                    if row:
                        sel.append(row)
        out.append({"name": (name or code).upper(), "code": code, "rows": sel})
    if not any(s["rows"] for s in out):
        return None
    return price_format.format_history(out, lang=("en" if _en else "ko"))


def _vip_stock_data_reply(transcript: Optional[str], lang: str) -> Optional[str]:
    """Unified VIP stock-data answer (the single source the AI Advisor relays):
    공매도 (Kiwoom ka10014) → history table (past/range) → live current price (with
    volume / requested fields). None when no stock/data resolves."""
    if _is_short_selling_q(transcript):
        ss = _vip_short_selling_reply(transcript, lang)
        if ss and ss.get("reply"):
            return ss["reply"]
    h = _requested_history_dates(transcript)
    if h:
        r = _vip_history_reply(transcript, lang, h)
        if r:
            return r
    cur = _vip_live_price_reply(transcript, lang)
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
                r"올라와\s*있는지|올라와|올라온|등록\s*되어|등록|있는지요|있는지|있나요|있나|있어요|있어|"
                r"좀|찾아봐줘|찾아봐|찾아|please|search|for|on|in|is|are|our|whether|listed|price|"
                r"advertis\w*|우리|저희|the|check|real\s*estate)", " ", t, flags=_re.I)
    t = _re.sub(r"(?<=\s)(에|에서|이|가|을|를|은|는|도|의|로)(?=\s|$)", " ", t)
    t = _re.sub(r"[?!.,]+", " ", t)
    return _re.sub(r"\s+", " ", t).strip()


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


def _vip_naver_search_reply(transcript: Optional[str], lang: str, db=None) -> Optional[dict]:
    from services.naver_search import naver_search
    tl = (transcript or "").lower()
    re_estate = any(k in tl for k in _NAVER_RE_KW)
    subject = _naver_subject(transcript) or (transcript or "")
    _en = (lang or "").lower().startswith("en")

    def _fmt(rs):
        out = []
        for r in rs:
            title = (r.get("title") or "").strip()
            url = (r.get("url") or "").strip()
            snip = (r.get("snippet") or "").strip()[:120]
            out.append(f"• {title}" + (f"\n  🔗 {url}" if url else "") + (f"\n  {snip}" if snip else ""))
        return out

    # ===== Step 1: resolve the address from OUR uploaded asset file first =====
    # '낙하리' → ['낙하리 301-7', '낙하리 301-9', ...]. If we own matching properties we
    # Naver-check each PRECISE address; otherwise it's a generic Naver search.
    our_addrs = _our_property_addresses(db, subject) if re_estate else []

    if our_addrs:
        # ===== Step 2: Naver-check each of our addresses (cap 5 for latency) =====
        listed, missing, unverified, last_res = [], [], [], None
        for addr in our_addrs[:5]:
            r = naver_search(addr, realestate=True, num_results=5)
            last_res = r
            # Count as listed ONLY when a deep Naver link ALSO genuinely matches this
            # address (area/lot), so we never link an unrelated complex as "proof".
            deep = [x for x in (r.get("results") or [])
                    if _is_deep_naver_url(x.get("url") or "") and _addr_on_naver(addr, x)]
            if deep:
                listed.append((addr, (deep[0].get("url") or "").strip()))
            elif _naver_provider_authoritative(r.get("provider")):
                missing.append(addr)   # an authoritative source genuinely found none
            else:
                unverified.append(addr)  # only the weak fallback ran — don't claim "not listed"
        # ===== Step 3/4: short answer — link only for the ones actually on Naver =====
        lines = []
        if listed:
            lines.append("✅ 네이버 부동산에 올라와 있는 자산:" if not _en else "✅ Listed on NAVER 부동산:")
            for addr, url in listed:
                lines.append(f"• {addr}\n  🔗 {url}")
        if missing:
            joined = ", ".join(missing)
            lines.append(
                (f"아직 네이버 부동산에 올라오지 않은 자산: {joined}" if not _en
                 else f"Not yet uploaded to NAVER 부동산: {joined}"))
        if unverified and not listed and not missing:
            # Provider degraded for the whole batch — be honest, don't fake a verdict.
            lines.append(
                ("네이버 매물 조회 서비스가 일시적으로 제한되어 지금은 확인할 수 없습니다 "
                 "(검색 API 키 필요)." if not _en else
                 "Couldn't check NAVER right now — the listing search provider is "
                 "unavailable (search API key needed)."))
        elif unverified:
            joined = ", ".join(unverified)
            lines.append(
                (f"확인 불가(조회 제한): {joined}" if not _en
                 else f"Could not verify (provider limited): {joined}"))
        reply = "\n\n".join(lines)
        return {"intent": "naver_search", "language": lang, "reply": reply[:1900],
                "action": None, "speak": True, "transcript": transcript,
                "tool_used": "naver_search",
                "tool_result": {"ok": True, "our_assets": our_addrs,
                                "listed": [a for a, _ in listed], "missing": missing,
                                "unverified": unverified, "last": last_res}}

    # ===== No matching asset in our file → plain Naver search =====
    res = naver_search(subject, realestate=re_estate, num_results=6)
    results = res.get("results") or []
    if re_estate:
        deep = [r for r in results if _is_deep_naver_url(r.get("url") or "")]
        # We don't own it (or couldn't resolve it) — report listings generically.
        if deep:
            head = (f"네이버 부동산에서 '{subject}' 매물입니다:" if not _en else
                    f"NAVER 부동산 listings for '{subject}':")
            reply = head + "\n\n" + "\n".join(_fmt(deep[:5]))
        elif _naver_provider_authoritative(res.get("provider")):
            # An authoritative source genuinely found nothing → confident, NO link.
            reply = (f"'{subject}'은(는) 아직 네이버 부동산에 올라오지 않았습니다."
                     if not _en else
                     f"'{subject}' has not been uploaded to NAVER 부동산 yet.")
        else:
            # Only the weak fallback ran — be honest instead of claiming "not listed".
            reply = ("네이버 매물 조회 서비스가 일시적으로 제한되어 지금은 확인할 수 없습니다 "
                     "(검색 API 키 필요)." if not _en else
                     "Couldn't check NAVER right now — the listing search provider is "
                     "unavailable (search API key needed).")
    else:
        if results:
            head = (f"네이버 검색 결과 — '{subject}':" if not _en else f"NAVER results for '{subject}':")
            reply = head + "\n\n" + "\n".join(_fmt(results[:6]))
        else:
            reply = (f"네이버에서 '{subject}' 검색 결과를 찾지 못했습니다." if not _en else
                     f"No Naver results found for '{subject}'.")
    return {"intent": "naver_search", "language": lang, "reply": reply[:1900],
            "action": None, "speak": True, "transcript": transcript,
            "tool_used": "naver_search", "tool_result": res}


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
    """How many trading days of history to pull for a past-date question."""
    m = _re.search(r"(\d+)\s*일", transcript or "")
    if m:
        try:
            return max(5, min(int(m.group(1)) + 6, 120))
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
        "Be concise, warm, and conversational — like a smart human assistant. "
        "1-3 sentences for chat; longer only when listing data.\n\n"
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
        "alone. This is the DEFAULT for any non-price stock question. Gather evidence "
        "FIRST, then give a reasoned recommendation:\n"
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
        return {"answer": raw[:500], "_model": primary}
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
        "어때, 전망, is it a good buy), use EXACTLY this two-part shape — and your "
        "VERY FIRST words MUST be the verdict sentence (NEVER open with a "
        "descriptive paragraph or the price):\n"
        "  (1) FIRST line = the VERDICT in ONE sentence: your stance + the single "
        "biggest risk. English example: 'I would recommend a BUY stance, with the "
        "single biggest risk being a potential reversal in foreign investor "
        "sentiment.' Korean example: '매수(BUY)를 추천합니다. 가장 큰 리스크는 외국인 "
        "수급의 반전 가능성입니다.' Use BUY/HOLD/SELL (매수/보유/매도).\n"
        "  (2) Then a line 'Because:' (Korean: '근거:') followed by a NUMBERED list "
        "(1. 2. 3. …) of the concrete supporting reasons — current price/level, "
        "수급(외국인/기관) direction, momentum/technical read, live recommendation/"
        "news — EACH with the real numbers from the results. Keep each item to one "
        "line, no repetition. Do NOT restate the price as the whole answer, and do "
        "NOT add a preamble before the verdict sentence."
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
            max_tokens=max(_max_tok, 650), temperature=0.4,
            model="groq-llama-3.3-70b",
        )
    except Exception:
        reply = "Done — checked the data."

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
        "reply": (reply or "Done.")[:1500],
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
        "Summarize it for the boss in 1-3 sentences. Be conversational.\n",
        300, 1500,
    )


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
    summary_messages = [
        {"role": "user", "content": f"My question: {user_msg}"},
        {"role": "user", "content": f"Tool '{tool_name}' returned:\n{json.dumps(tool_result, ensure_ascii=False, default=str)[:_data_cap]}"},
    ]
    try:
        reply = chat_completion_sync(
            system_prompt=follow_system,
            messages=summary_messages,
            max_tokens=_max_tokens,
            temperature=0.4,
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
            model="gemini-3.1-pro-preview",
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
    result = _run_agent_impl(
        db, transcript=transcript, language=language,
        current_path=current_path, selected_id=selected_id, history=history,
        confirmed_tool=confirmed_tool, confirmed_args=confirmed_args,
        attachment_ids=attachment_ids, forced_model=forced_model,
        user_id=user_id, agent_id=agent_id,
        page_context=page_context,
    )
    # Persist meaningful turns only — skip empty / multimodal_failed / errors
    skip_intents = {"empty", "multimodal_failed", "multimodal_missing", "chain_empty"}
    if user_id and result.get("intent") not in skip_intents and result.get("reply"):
        _persist_assistant_turn(
            db,
            user_id=user_id,
            user_text=transcript or "",
            assistant_reply=str(result.get("reply") or ""),
            intent=result.get("intent"),
            tool_used=result.get("tool_used"),
        )

    # --- Self-improvement instrumentation (#12 + #13 + #15) ---
    # Log the answered turn and, if it looks low-confidence, kick off background
    # web research so the next time this is asked the KB has an answer. All
    # best-effort — never blocks or breaks the response.
    try:
        reply_text = str(result.get("reply") or "")
        skip = {"empty", "multimodal_failed", "multimodal_missing", "chain_empty", "error"}
        if (transcript or "").strip() and result.get("intent") not in skip and reply_text:
            from services.assistant_learning import is_low_confidence, log_qa
            # An offline turn that couldn't be answered is a real knowledge gap —
            # treat it as low-confidence so the gap report + background research
            # pick it up (research runs server-side; it doesn't break offline UX).
            low = is_low_confidence(reply_text) or bool(result.get("needs_llm"))
            log_qa(db, agent_id=agent_id, question=transcript or "", answer=reply_text,
                   intent=result.get("intent"), tool_used=result.get("tool_used"),
                   low_conf=low, user_id=user_id)
            if low and not (confirmed_tool):
                _spawn_background_research(agent_id, transcript or "")
    except Exception as _e:
        log.warning(f"self-improve instrumentation skipped: {str(_e)[:120]}")

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

    # === Stock agent = relay the Stock-Advisor app (single source of truth) ===
    # The stock agent's answers (and therefore VIP's delegated answers) come from
    # the SAME backend that powers the Stock app's "주식 AI" box, so every surface
    # shows identical data + structure. On failure we fall through to the
    # in-process stock engine below.
    if (not confirmed_tool and not attachment_ids
            and (agent_id or "").lower() == "stock"):
        try:
            from services import stock_advisor_chat
            ext = stock_advisor_chat.ask(transcript, lang=lang, history=history or [])
        except Exception as e:
            log.warning(f"stock-advisor relay failed: {str(e)[:120]}")
            ext = None
        if ext and ext.get("reply"):
            return {
                "intent": ext.get("intent") or "stock_advisor",
                "language": lang, "reply": str(ext["reply"])[:1800],
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
    kb_hits: list[dict] = []
    rag_error: Optional[str] = None
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
    if not confirmed_tool and _is_naver_search_q(transcript):
        _nr = _vip_naver_search_reply(transcript, lang, db)
        if _nr:
            return _nr

    # ===== VIP LOCAL history (past dates / ranges) — deterministic OHLCV table =====
    # 'naver price on 18th/17th/16th of June', 'last 4 days' → a fixed table from VIP's
    # Naver daily data (no LLM), so VIP and the relaying AI Advisor read IDENTICALLY.
    # Falls through (US / unresolved) to the LLM/web-search past path below.
    if (not confirmed_tool and (agent_id or "vip").lower() != "stock"
            and _requested_history_dates(transcript)):
        _hist = _vip_history_reply(transcript, lang)
        if _hist:
            return {"intent": "stock_history", "language": lang, "reply": _hist,
                    "action": None, "speak": True, "transcript": transcript,
                    "tool_used": "stock_history"}

    # ===== VIP LOCAL current-price (the rich single source) =====
    # VIP holds the Kiwoom key, so it answers current-price HERE — Kiwoom during market
    # / Naver after, with opening/high/low/volume when asked and an always-on source
    # label. The AI Advisor relays this exact answer, so both surfaces read identically.
    if not confirmed_tool and _is_vip_current_price_q(transcript, agent_id):
        _vp = _vip_live_price_reply(transcript, lang)
        if _vp:
            return _vp

    # ===== 공매도 (short-selling) — answer LOCALLY from VIP's Kiwoom (ka10014). The
    # Stock backend's 공매도 tool currently returns '확인 불가' (no data), but VIP's Kiwoom
    # key returns REAL short-selling figures, so VIP serves this one itself. =====
    if (not confirmed_tool and (agent_id or "vip").lower() != "stock"
            and _is_short_selling_q(transcript)):
        ss = _vip_short_selling_reply(transcript, lang)
        if ss:
            return ss

    # ===== VIP → Stock delegation (single source of truth) =====
    # ANY stock question asked in VIP (or another non-stock agent) is answered by
    # the Stock agent itself — verbatim transcript, same engine — so VIP and Stock
    # ALWAYS give the same answer. Runs before the per-topic short-circuits below.
    _stock_turn = (_is_stock_question(transcript)
                   or (_recent_stock_context(history) and any(
                       k in (transcript or "").lower() for k in _STOCK_FOLLOWUP_KW)))
    if (not confirmed_tool and (agent_id or "vip").lower() != "stock"
            and "ask_agent" in TOOL_REGISTRY and _stock_turn
            and not _is_past_price(transcript)
            and not _is_report_question(transcript)
            and not _is_concept_question(transcript)):
        # FAST PATH (latency): the Stock backend is the single source of truth, so
        # call it DIRECTLY instead of the heavier nested run_agent(agent_id='stock')
        # (which re-runs RAG + an LLM decision before reaching the same backend).
        # Same answer, ~5-6s faster. Falls back to ask_agent if it returns nothing.
        ans = None
        try:
            from services.stock_advisor_chat import ask as _stock_direct
            _d = _stock_direct(transcript, lang, history or [])
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
        # Fallback: nested ask_agent (internal stock engine + its own fallbacks).
        res = execute_tool("ask_agent",
                           {"agent": "stock", "question": transcript, "history": history or []},
                           db=db, agent_id=agent_id, transcript=transcript)
        ans = None
        if isinstance(res, dict):
            for a in (res.get("answers") or []):
                cand = (a.get("answer") or "").strip()
                # Guard: never relay a raw decision-JSON leak ('{"tool": ...}').
                if cand and not cand.startswith(("{", "[")):
                    ans = cand
                    break
        if ans:
            return {"intent": "stock_delegated", "language": lang,
                    "reply": str(ans)[:1600], "action": None, "speak": True,
                    "transcript": transcript, "tool_used": "ask_agent",
                    "tool_result": res}
        # Resilience: the Stock backend returned nothing usable (down, mid-deploy, or a
        # query type it currently fails on). Answer current-price and 공매도 from VIP's
        # OWN Kiwoom so the user never sees a blank reply.
        if _is_short_selling_q(transcript):
            _ss_fb = _vip_short_selling_reply(transcript, lang)
            if _ss_fb:
                return _ss_fb
        _vp_fb = _vip_live_price_reply(transcript, lang)
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
        vp = _vip_live_price_reply(transcript, lang)
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
    if not confirmed_tool and _is_past_price(transcript):
        # Delegate past-date prices to the Stock backend FIRST (single deterministic
        # source of truth) so VIP and the AI Advisor give the IDENTICAL concise
        # answer. Fall back to the local daily-history chain / web search only if the
        # relay returns nothing usable.
        try:
            from services.stock_advisor_chat import ask as _stock_past
            _p = _stock_past(transcript, lang, history or [])
            if isinstance(_p, dict):
                cand = (_p.get("reply") or "").strip()
                if cand and not cand.startswith(("{", "[")):
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
                                  selected_id, system, history or [], agent_id=agent_id)
        if "web_search" in TOOL_REGISTRY:
            ws_steps = [{"tool": "web_search", "args": {"query": transcript}}]
            return _run_chain(db, transcript, lang, ws_steps, current_path,
                              selected_id, system, history or [], agent_id=agent_id)

    # ===== Deterministic STOCK-ADVICE routing =====
    # The LLM (especially for Korean) tends to answer 'should I buy X / X 어때?'
    # with the bare current price. For a clear single-stock ADVICE question we
    # force the analysis path instead — chain the live tools (stock agent) or
    # relay to the Stock agent verbatim (VIP / others) so the user always gets a
    # reasoned 매수/보유/매도 view, never just a number.
    if not confirmed_tool and _is_stock_advice(transcript, agent_id):
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
                                  selected_id, system, history or [], agent_id=agent_id)
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
        return _run_chain(db, transcript, lang, steps, current_path, selected_id, system, history or [], agent_id=agent_id)

    # If the LLM chose to answer directly, return it
    if decision.get("answer") and not decision.get("tool"):
        return {
            "intent": "llm_chat",
            "language": lang,
            "reply": str(decision["answer"])[:1000],
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
            "reply": (decision.get("answer") or "I'm not sure how to help with that — could you rephrase?")[:500],
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
