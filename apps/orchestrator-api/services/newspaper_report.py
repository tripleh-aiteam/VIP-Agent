"""
newspaper_report — daily NEWS-DRIVEN market analysis, sourced from a fixed set
of named financial newspapers. For each outlet it pulls that paper's latest
stock / company news (via live web search scoped with site:), then the LLM
writes a per-newspaper analysis and a final BUY / HOLD / SELL recommendation
grounded in the collected news.

Outlets:
  KR — Korea Economic Daily (KED Global), Pulse (Maeil Business), Seoul Economic Daily
  US — Wall Street Journal, Bloomberg, Barron's, Yahoo Finance

Reuses the Kiwoom price layer (same 11 companies + table). Saved as
report_type='newspaper_report' (period='daily'); also Telegram + Word email.
"""

from __future__ import annotations

from datetime import datetime

from services.logger import log
from services import kiwoom_report as _kr

# Named newspapers — each searched with a site: filter so the news really comes
# from that outlet. (name, domain, region)
NEWSPAPERS: list[dict] = [
    {"name": "Korea Economic Daily (KED Global)", "site": "kedglobal.com", "region": "KR"},
    {"name": "Pulse by Maeil Business", "site": "pulsenews.co.kr", "region": "KR"},
    {"name": "Seoul Economic Daily", "site": "sedaily.com", "region": "KR"},
    {"name": "The Wall Street Journal", "site": "wsj.com", "region": "US"},
    {"name": "Bloomberg", "site": "bloomberg.com", "region": "US"},
    {"name": "Barron's", "site": "barrons.com", "region": "US"},
    {"name": "Yahoo Finance", "site": "finance.yahoo.com", "region": "US"},
]

# Company keywords used to scope each outlet's search to our watchlist.
_KR_KEYWORDS = "SK하이닉스 OR 삼성전자 OR 네이버 OR SK텔레콤 OR 삼성SDS OR 반도체 OR 코스피"
_US_KEYWORDS = "Samsung OR SK Hynix OR Nvidia OR AMD OR Micron OR Broadcom OR semiconductor OR Naver"


def _query_for(paper: dict) -> str:
    kw = _KR_KEYWORDS if paper["region"] == "KR" else _US_KEYWORDS
    return f"site:{paper['site']} ({kw}) stock market news today"


def _gather_news_by_source(per_source: int = 4) -> dict[str, list[dict]]:
    """For each newspaper, fetch its latest watchlist news. Returns
    {newspaper_name: [{title,url,snippet}]}. Empty lists if no provider/results."""
    try:
        from services.web_search import search_web
    except Exception as e:
        log.warning(f"newspaper: web_search import failed: {e}")
        return {p["name"]: [] for p in NEWSPAPERS}

    grouped: dict[str, list[dict]] = {}
    for paper in NEWSPAPERS:
        hits: list[dict] = []
        try:
            res = search_web(_query_for(paper), num_results=per_source)
            if res.get("ok"):
                seen: set[str] = set()
                for h in res.get("results", []):
                    title = (h.get("title") or "").strip()
                    snippet = (h.get("snippet") or "").strip()
                    key = (title or snippet)[:80].lower()
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    hits.append({"title": title, "url": h.get("url", ""), "snippet": snippet})
        except Exception as e:
            log.warning(f"newspaper: search {paper['site']} failed: {e}")
        grouped[paper["name"]] = hits
    return grouped


def _news_block_by_source(grouped: dict[str, list[dict]]) -> str:
    """Per-newspaper text block for the LLM, grouped under each outlet name."""
    parts = []
    for paper in NEWSPAPERS:
        items = grouped.get(paper["name"], [])
        parts.append(f"### {paper['name']} ({paper['region']})")
        if not items:
            parts.append("(no fresh items returned for this source)")
        else:
            for n in items:
                t = n["title"][:140]
                s = n["snippet"][:240]
                parts.append(f"- {t} — {s}" if s else f"- {t}")
    return "\n".join(parts)


def build_newspaper_report(db, trace_id: str) -> dict:
    """Build the daily per-newspaper news report — same price table as Kiwoom +
    per-outlet news analysis + a BUY/HOLD/SELL recommendation, bilingual EN/KO."""
    rows, table_en, table_ko, rate = _kr.gather_priced_rows()
    ok_rows = [r for r in rows if r.get("ok")]
    grouped = _gather_news_by_source()
    total_news = sum(len(v) for v in grouped.values())
    kst_date = datetime.utcnow().strftime("%Y-%m-%d")

    movers = sorted([r for r in ok_rows if r.get("change_pct") is not None],
                    key=lambda r: r["change_pct"])
    sum_en = (f"Newspaper analysis ({len(ok_rows)}/{len(rows)} tickers, {total_news} articles "
              f"from {len(NEWSPAPERS)} outlets): "
              + (f"weakest {movers[0]['en']} {_kr._fmt_chg(movers[0]['change_pct'])}, "
                 f"strongest {movers[-1]['en']} {_kr._fmt_chg(movers[-1]['change_pct'])}."
                 if movers else "data limited."))
    sum_ko = (f"신문 분석 ({len(ok_rows)}/{len(rows)} 종목, {len(NEWSPAPERS)}개 매체 {total_news}건): "
              + (f"최약 {movers[0]['ko']} {_kr._fmt_chg(movers[0]['change_pct'])}, "
                 f"최강 {movers[-1]['ko']} {_kr._fmt_chg(movers[-1]['change_pct'])}."
                 if movers else "데이터 제한."))

    papers_list = ", ".join(p["name"] for p in NEWSPAPERS)
    detail_en = detail_ko = ""
    try:
        from services.llm_client import chat_completion_sync
        sysmsg = (
            "You are TripleH's market-news analyst writing the DAILY NEWSPAPER "
            "report after the US close (~7:00 AM KST). You read a fixed set of "
            f"financial newspapers ({papers_list}) and tie their reporting to the "
            "price action of the watchlist. Use ONLY the provided data + news — "
            "NEVER invent quotes or numbers. ALL prices are Korean Won (KRW). "
            "Produce this EXACT structure:\n"
            "## 1. General Overview\n## 2. Market Data\n## 3. News by Newspaper\n"
            "## 4. Company-Specific Analysis\n## 5. Recommendations\n\n"
            "Rules:\n"
            "- Section 2: insert the provided data table VERBATIM.\n"
            "- Section 3 (News by Newspaper): for EACH newspaper, a '### <Newspaper "
            "Name>' sub-heading followed by (a) that paper's STOCK/MARKET take and "
            "(b) its COMPANY-SPECIFIC news for our watchlist — cite the actual "
            "headlines provided for that source. If a source returned nothing, say "
            "'No fresh items today' for it.\n"
            "- Section 4 (Company-Specific Analysis): a DEDICATED paragraph per key "
            "name (SK Hynix, Samsung, AMD, Micron, Broadcom, SanDisk, SOXX, SK "
            "Telecom, Samsung SDS, Naver, KODEX 200) — synthesise what the combined "
            "newspaper coverage says and connect it to the real change% (e.g. "
            "'Nvidia CEO visits Korea to meet Naver → Naver +X%').\n"
            "- Section 5 (Recommendations): FIRST a Markdown table | Stock | Action | "
            "Reason | with Action = BUY / HOLD / SELL; THEN a '### Rationale' "
            "subsection with a paragraph per recommendation explaining — grounded in "
            "the NEWS above + the price/technicals — WHY to buy, hold, or sell.\n"
            "Be specific, cite the newspapers by name, ~800+ words.\n"
            "Output ONLY the finished English Markdown report — no preamble."
        )
        user = (f"Date (KST): {kst_date} · USD/KRW: {rate:,.0f}\n\n"
                f"DATA TABLE (insert verbatim in Section 2):\n{table_en}\n\n"
                f"PRICE TECHNICALS:\n{_kr._facts(rows)}\n\n"
                f"NEWS BY NEWSPAPER:\n{_news_block_by_source(grouped)}")
        out = chat_completion_sync(
            system_prompt=sysmsg, messages=[{"role": "user", "content": user[:13000]}],
            max_tokens=7500, temperature=0.5, model="groq-llama-3.3-70b") or ""
        bad = (not out.strip()) or out.lstrip().startswith(("[LLM unavailable]", "[server error]"))
        if not bad:
            detail_en = out.strip()
            try:
                ko_sys = (
                    "You are a professional Korean financial translator. Translate the "
                    "ENTIRE English newspaper report into natural, professional Korean "
                    "(존댓말). Translate EVERYTHING — never summarise or stub. Preserve "
                    "ALL Markdown, headings, sub-headings and tables; keep every number, "
                    "%, 원, ticker and newspaper name IDENTICAL. Replace the Section 2 "
                    f"table with this EXACT Korean table:\n{table_ko}\n"
                    "Output ONLY the Korean Markdown report.")
                ko_out = chat_completion_sync(
                    system_prompt=ko_sys,
                    messages=[{"role": "user", "content": detail_en[:15000]}],
                    max_tokens=7500, temperature=0.3, model="groq-llama-3.3-70b") or ""
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
        src_lines = []
        for p in NEWSPAPERS:
            items = grouped.get(p["name"], [])
            src_lines.append(f"### {p['name']}")
            src_lines += [f"- {n['title']}" for n in items[:5]] or ["- No fresh items today"]
        detail_en = (f"# Newspaper Market Analysis\n*{kst_date} (after US close)*\n\n"
                     f"## 1. General Overview\n{sum_en}\n\n## 2. Market Data\n{table_en}\n\n"
                     f"## 3. News by Newspaper\n" + "\n".join(src_lines) + "\n\n"
                     f"## 4. Company-Specific Analysis\nSee headlines above.\n\n"
                     f"## 5. Recommendations\n| Stock | Action | Reason |\n|---|---|---|\n"
                     f"| — | HOLD | LLM unavailable — manual review |")
    if not detail_ko:
        detail_ko = detail_en

    sources_flat = [{"title": n["title"], "url": n["url"], "newspaper": name}
                    for name, items in grouped.items() for n in items]
    return {
        "agent_type": "newspaper", "name": "Newspaper Market Analysis", "emoji": "📰",
        "status": "ok" if ok_rows else "partial",
        "summary_en": sum_en, "summary_ko": sum_ko,
        "detail_en": detail_en, "detail_ko": detail_ko,
        "table_en": table_en, "table_ko": table_ko,
        "rows": rows,
        "news_sources": sources_flat[:40],
        "newspapers": [p["name"] for p in NEWSPAPERS],
        "source": "TripleH Newspaper Analysis (KED/Pulse/Seoul Econ/WSJ/Bloomberg/Barron's/Yahoo + OHLCV)",
    }
