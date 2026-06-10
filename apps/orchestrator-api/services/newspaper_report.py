"""
newspaper_report — daily NEWS-DRIVEN market analysis. Explains *why* the market
and the watchlist moved by tying live news (war, oil, rates, FX, geopolitics +
company-specific events like "Nvidia CEO visits Korea to meet Naver") to the
real price action.

Reuses the Kiwoom price layer (same 11 companies + table) and adds a live-news
layer via services.web_search (Serper / Tavily / Google / Gemini grounding).
Produces a structured bilingual (EN/KO) report, saved as
report_type='newspaper_report' (period='daily'); also Telegram + Word email.
"""

from __future__ import annotations

from datetime import datetime

from services.logger import log
from services import kiwoom_report as _kr

# News queries — macro (Korea/USA/global) + the key individual names. Kept short
# so we stay within the search provider's free tier.
_MACRO_QUERIES = [
    "stock market news today war oil interest rates Fed",
    "KOSPI Korea stock market news today 코스피 증시",
    "US market Nasdaq S&P semiconductor news today",
    "Philadelphia semiconductor index SOX Nvidia AMD news today",
    "global geopolitics oil price USD KRW exchange rate market impact today",
]
_COMPANY_QUERIES = [
    "SK Hynix 005930 SK하이닉스 news today",
    "Samsung Electronics 삼성전자 news today",
    "Nvidia Naver 네이버 news today",
    "AMD Micron Broadcom news today",
    "SK Telecom Samsung SDS 삼성SDS news today",
]


def _gather_news(max_per_query: int = 4, cap: int = 28) -> list[dict]:
    """Run the news queries through the live web-search helper. Returns a
    deduped list of {title, url, snippet, q}. Empty if no provider configured."""
    try:
        from services.web_search import search_web
    except Exception as e:
        log.warning(f"newspaper: web_search import failed: {e}")
        return []

    seen: set[str] = set()
    out: list[dict] = []
    for q in (_MACRO_QUERIES + _COMPANY_QUERIES):
        try:
            res = search_web(q, num_results=max_per_query)
        except Exception as e:
            log.warning(f"newspaper: search '{q[:40]}' failed: {e}")
            continue
        if not res.get("ok"):
            continue
        for hit in res.get("results", []):
            title = (hit.get("title") or "").strip()
            snippet = (hit.get("snippet") or "").strip()
            key = (title or snippet)[:80].lower()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append({"title": title, "url": hit.get("url", ""), "snippet": snippet, "q": q})
            if len(out) >= cap:
                return out
    return out


def _news_block(news: list[dict]) -> str:
    """Compact text block of headlines+snippets for the LLM."""
    if not news:
        return "(no live news available — analyse from the price action + general knowledge)"
    lines = []
    for n in news:
        t = n["title"][:140]
        s = n["snippet"][:240]
        lines.append(f"- {t} — {s}" if s else f"- {t}")
    return "\n".join(lines)


def build_newspaper_report(db, trace_id: str) -> dict:
    """Build the daily newspaper (news analysis) report — same price table as
    Kiwoom + live-news-driven narrative, bilingual EN/KO."""
    rows, table_en, table_ko, rate = _kr.gather_priced_rows()
    ok_rows = [r for r in rows if r.get("ok")]
    news = _gather_news()
    kst_date = datetime.utcnow().strftime("%Y-%m-%d")

    movers = sorted([r for r in ok_rows if r.get("change_pct") is not None],
                    key=lambda r: r["change_pct"])
    sum_en = (f"News analysis ({len(ok_rows)}/{len(rows)} tickers, {len(news)} sources): "
              + (f"weakest {movers[0]['en']} {_kr._fmt_chg(movers[0]['change_pct'])}, "
                 f"strongest {movers[-1]['en']} {_kr._fmt_chg(movers[-1]['change_pct'])}."
                 if movers else "data limited."))
    sum_ko = (f"뉴스 분석 ({len(ok_rows)}/{len(rows)} 종목, 출처 {len(news)}건): "
              + (f"최약 {movers[0]['ko']} {_kr._fmt_chg(movers[0]['change_pct'])}, "
                 f"최강 {movers[-1]['ko']} {_kr._fmt_chg(movers[-1]['change_pct'])}."
                 if movers else "데이터 제한."))

    detail_en = detail_ko = ""
    try:
        from services.llm_client import chat_completion_sync
        sysmsg = (
            "You are TripleH's market-news analyst writing the DAILY NEWSPAPER "
            "analysis after the US close (~7:00 AM KST). You connect NEWS to PRICE "
            "— explain WHY each move happened. Use ONLY the data + news provided; "
            "NEVER invent quotes or numbers. ALL prices are Korean Won (KRW). "
            "Produce this EXACT structure:\n"
            "## 1. General Overview\n## 2. Market Data\n## 3. Global & Macro News\n"
            "## 4. Company-Specific News\n## 5. Risks & Watch-items\n## 6. Opportunities & Outlook\n\n"
            "Rules:\n"
            "- Section 2: insert the provided data table VERBATIM.\n"
            "- Section 3 (Global & Macro): analyse the cross-border drivers from the "
            "news — war/conflict, oil & energy, central-bank/interest-rate moves, "
            "USD/KRW & FX, and geopolitics — covering KOREA, the USA, and OTHER "
            "countries. Tie each to the market tone. Cite the headline.\n"
            "- Section 4 (Company-Specific): a DEDICATED paragraph per key name that "
            "had news (e.g. 'Nvidia CEO visits Korea to meet Naver → Naver +X%'). For "
            "EACH, state the news event, the source, and how it moved the price (use "
            "the real change% from the data). If a name has no news, infer from sector "
            "read-through. Cover the watchlist: SK Hynix, Samsung, AMD, Micron, "
            "Broadcom, SanDisk, SOXX, SK Telecom, Samsung SDS, Naver, KODEX 200.\n"
            "- Be specific and cite headlines; ~700+ words across sections 3-4.\n"
            "Output ONLY the finished English Markdown report — no preamble."
        )
        user = (f"Date (KST): {kst_date} · USD/KRW: {rate:,.0f}\n\n"
                f"DATA TABLE (insert verbatim in Section 2):\n{table_en}\n\n"
                f"PRICE TECHNICALS:\n{_kr._facts(rows)}\n\n"
                f"LIVE NEWS HEADLINES:\n{_news_block(news)}")
        out = chat_completion_sync(
            system_prompt=sysmsg, messages=[{"role": "user", "content": user[:11000]}],
            max_tokens=7000, temperature=0.5, model="groq-llama-3.3-70b") or ""
        bad = (not out.strip()) or out.lstrip().startswith(("[LLM unavailable]", "[server error]"))
        if not bad:
            detail_en = out.strip()
            try:
                ko_sys = (
                    "You are a professional Korean financial translator. Translate the "
                    "ENTIRE English news report into natural, professional Korean (존댓말). "
                    "Translate EVERYTHING — never summarise or stub. Preserve ALL Markdown, "
                    "headings, and tables; keep every number, %, 원, ticker IDENTICAL. "
                    "Replace the Section 2 table with this EXACT Korean table:\n"
                    f"{table_ko}\nOutput ONLY the Korean Markdown report.")
                ko_out = chat_completion_sync(
                    system_prompt=ko_sys,
                    messages=[{"role": "user", "content": detail_en[:14000]}],
                    max_tokens=7000, temperature=0.3, model="groq-llama-3.3-70b") or ""
                ko_bad = ((not ko_out.strip())
                          or ko_out.lstrip().startswith(("[LLM unavailable]", "[server error]"))
                          or len(ko_out.strip()) < 400)
                if not ko_bad:
                    detail_ko = ko_out.strip()
            except Exception as e:
                log.warning(f"newspaper KO translation failed: {e}")
    except Exception as e:
        log.warning(f"newspaper LLM compose failed: {e}")

    if not detail_en:
        srcs = "\n".join(f"- {n['title']}" for n in news[:10]) or "- (no live news source configured)"
        detail_en = (f"# Newspaper Market Analysis\n*{kst_date} (after US close)*\n\n"
                     f"## 1. General Overview\n{sum_en}\n\n## 2. Market Data\n{table_en}\n\n"
                     f"## 3. Global & Macro News\n{srcs}\n\n"
                     f"## 4. Company-Specific News\nSee headlines above.\n\n"
                     f"## 5. Risks & Watch-items\n- Review the weakest movers.\n\n"
                     f"## 6. Opportunities & Outlook\n- Review the strongest movers.")
    if not detail_ko:
        detail_ko = detail_en

    return {
        "agent_type": "newspaper", "name": "Newspaper Market Analysis", "emoji": "📰",
        "status": "ok" if ok_rows else "partial",
        "summary_en": sum_en, "summary_ko": sum_ko,
        "detail_en": detail_en, "detail_ko": detail_ko,
        "table_en": table_en, "table_ko": table_ko,
        "rows": rows,
        "news_sources": [{"title": n["title"], "url": n["url"]} for n in news[:20]],
        "source": "TripleH News Analysis (live web search + Stock Advisor OHLCV)",
    }
