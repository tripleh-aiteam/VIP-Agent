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
from services import catalyst_news as _cat

# Named newspapers — each searched with a site: filter so the news really comes
# from that outlet. (name, domain, region)
NEWSPAPERS: list[dict] = [
    {"name": "Bloomberg", "site": "bloomberg.com", "region": "US"},
    {"name": "The Wall Street Journal", "site": "wsj.com", "region": "US"},
    {"name": "Maeil Business (매일경제)", "site": "mk.co.kr", "region": "KR"},
    {"name": "Korea Economic Daily (한국경제)", "site": "hankyung.com", "region": "KR"},
    {"name": "MoneyToday (머니투데이)", "site": "mt.co.kr", "region": "KR"},
    {"name": "SBS Biz", "site": "biz.sbs.co.kr", "region": "KR"},
]

# Hidden sources — searched and used in the Company Analysis + Recommendations,
# but NEVER named or given their own section in the report (per request).
HIDDEN_SOURCES: list[dict] = [
    {"name": "Yahoo Finance", "site": "finance.yahoo.com", "region": "US"},
]

# Company keywords used to scope each outlet's search to our watchlist.
_KR_KEYWORDS = "SK하이닉스 OR 삼성전자 OR 네이버 OR SK텔레콤 OR 삼성SDS OR 반도체 OR 코스피"
_US_KEYWORDS = "Samsung OR SK Hynix OR Nvidia OR AMD OR Micron OR Broadcom OR semiconductor OR Naver"


def _queries_for(paper: dict) -> list[str]:
    """Two scoped queries per outlet — watchlist names + broad market/economy —
    so we capture ALL stock-relevant reporting, not only watchlist mentions.
    Each query is still 1 Serper credit (returns up to 10 results)."""
    if paper["region"] == "KR":
        kw, macro = _KR_KEYWORDS, "증시 OR 코스피 OR 경제 OR 금리 OR 환율 OR 반도체 OR 실적"
    else:
        kw, macro = _US_KEYWORDS, ("stock market OR Nasdaq OR economy OR Fed OR "
                                   "interest rates OR semiconductor OR AI OR earnings")
    return [
        f"site:{paper['site']} ({kw}) stock news today",
        f"site:{paper['site']} ({macro}) today",
    ]


def _recency_search(site: str, kr: bool, per_query: int = 10) -> list[dict]:
    """Last-24h site-scoped search → [{title,url,snippet}]."""
    try:
        from services.web_search import search_web
    except Exception:
        return []
    kw = (_KR_KEYWORDS + " OR 정책 OR 트럼프 OR 금리 OR 환율") if kr else \
         (_US_KEYWORDS + " OR Fed OR tariff OR Trump OR Iran OR policy")
    out: list[dict] = []
    seen: set[str] = set()
    try:
        res = search_web(f"site:{site} ({kw})", num_results=per_query, recency="d")
        for h in (res.get("results") or []):
            t = (h.get("title") or "").strip()
            key = t[:80].lower()
            if not t or key in seen:
                continue
            seen.add(key)
            out.append({"title": t, "url": h.get("url", ""), "snippet": (h.get("snippet") or "").strip()})
    except Exception as e:
        log.warning(f"newspaper: recency search {site} failed: {str(e)[:80]}")
    return out


def _gather_news_by_source(cap_kr: int = 7, cap_paid: int = 8) -> dict[str, list[dict]]:
    """Collect last-24h articles per outlet.
      - Korean (free) outlets: RSS list (timestamped) + FULL article text
        (trafilatura); paywalled premium → its RSS summary.
      - Paid (WSJ/Bloomberg): last-24h headlines + free snippets only (no body).
    Returns {name: [{title,url,text,full,paid}]}."""
    from services import news_fetch
    grouped: dict[str, list[dict]] = {}
    for paper in NEWSPAPERS:
        name, site, kr = paper["name"], paper["site"], paper["region"] == "KR"
        paid = name in ("Bloomberg", "The Wall Street Journal")
        arts: list[dict] = []
        if paid:
            for h in _recency_search(site, kr, per_query=cap_paid + 2)[:cap_paid]:
                arts.append({"title": h["title"], "url": h["url"], "text": h["snippet"],
                             "full": False, "paid": True})
        else:
            # Korean free outlet — RSS list (last 24h), with search fallback.
            items = news_fetch.rss_items(name, hours=24, cap=cap_kr + 6)
            if len(items) < 4:
                items += _recency_search(site, kr, per_query=10)
            picked: list[dict] = []
            seen: set[str] = set()
            for it in items:
                u = it.get("url", "")
                if not u or u in seen:
                    continue
                seen.add(u)
                picked.append(it)
                if len(picked) >= cap_kr:
                    break
            for it in picked:
                body = news_fetch.fetch_fulltext(it["url"], max_chars=3000)
                arts.append({"title": it.get("title", ""), "url": it.get("url", ""),
                             "text": body or it.get("summary", ""),
                             "full": bool(body), "paid": False})
        grouped[name] = arts
    return grouped


def _news_block_by_source(grouped: dict[str, list[dict]]) -> str:
    """Per-newspaper corpus for the LLM. Korean outlets carry FULL article text;
    paid outlets carry HEADLINE+summary only."""
    parts = []
    for paper in NEWSPAPERS:
        name = paper["name"]
        items = grouped.get(name, [])
        parts.append(f"### {name} ({paper['region']})")
        if not items:
            parts.append("(no fresh items in the last 24 hours)")
        else:
            for n in items:
                tag = "FULL ARTICLE" if n.get("full") else ("HEADLINE" if n.get("paid") else "SUMMARY")
                t = (n.get("title") or "")[:170]
                body = (n.get("text") or "")[:2600]
                parts.append(f"[{tag}] {t}\n{body}" if body else f"[{tag}] {t}")
        parts.append("")
    return "\n".join(parts)


def _gather_hidden(per_query: int = 10, cap: int = 14) -> list[dict]:
    """Gather HIDDEN-source results (e.g. Yahoo) — fed to the analysis but never
    named or given a section in the report."""
    try:
        from services.web_search import search_web
    except Exception:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for src in HIDDEN_SOURCES:
        for q in _queries_for(src):
            if len(out) >= cap:
                break
            try:
                res = search_web(q, num_results=per_query)
            except Exception:
                continue
            if not res.get("ok"):
                continue
            for h in res.get("results", []):
                title = (h.get("title") or "").strip()
                snippet = (h.get("snippet") or "").strip()
                key = (title or snippet)[:80].lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                out.append({"title": title, "snippet": snippet})
                if len(out) >= cap:
                    break
    return out


def _hidden_block(items: list[dict]) -> str:
    if not items:
        return "(none)"
    return "\n".join(
        (f"- {n['title'][:140]} — {n['snippet'][:220]}" if n.get("snippet") else f"- {n['title'][:140]}")
        for n in items)


def build_newspaper_report(db, trace_id: str) -> dict:
    """Build the daily per-newspaper news report — same price table as Kiwoom +
    per-outlet news analysis + a BUY/HOLD/SELL recommendation, bilingual EN/KO."""
    rows, table_en, table_ko, rate = _kr.gather_priced_rows()
    ok_rows = [r for r in rows if r.get("ok")]
    grouped = _gather_news_by_source()
    hidden = _gather_hidden()
    catalysts = _cat.gather_catalysts()
    total_news = sum(len(v) for v in grouped.values())
    from services.kst import kst_date as _kst_date
    kst_date = _kst_date()

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
            "Produce this EXACT structure (do NOT include a price/market-data table — "
            "the price table lives only in the Kiwoom report):\n"
            "## 1. General Overview\n## 2. News by Newspaper\n"
            "## 3. Company-Specific Analysis\n## 4. Catalysts & Schedule (일정매매)\n"
            "## 5. Recommendations\n\n"
            "All material provided was published in the LAST 24 HOURS. Cover ALL of "
            "it that can MOVE STOCK PRICES — direct stock/market news AND market-moving "
            "POLITICS/policy (Trump, tariffs, Iran, Fed, elections), macro (FX, rates), "
            "sector, and the watchlist.\n"
            "Rules:\n"
            "- Section 2 (News by Newspaper) — the CORE, longest section. For EACH "
            "newspaper a '### <Newspaper Name>' sub-heading. DEPTH DEPENDS ON THE DATA:\n"
            "  • Outlets whose items are marked [FULL ARTICLE] — you have the full "
            "text. Write a DEEP 3-4 PAGE analysis (700-1000 words) per such outlet: "
            "synthesise every price-relevant article (stock + political + macro), cite "
            "concrete facts/figures from the BODY text, and explain the price impact in "
            "flowing analytical prose (not a headline list).\n"
            "  • Outlets whose items are marked [HEADLINE] (Bloomberg, WSJ — paywalled, "
            "headline+summary only) — write a HONEST ~½ PAGE (150-200 words) from the "
            "headlines/summaries only. Do NOT fabricate article detail you don't have.\n"
            "  • If an outlet has no items, write 'No fresh items in the last 24 hours.'\n"
            "  Use ONLY the provided text; NEVER invent quotes or numbers.\n"
            "- Section 3 (Company-Specific Analysis) — a DEDICATED paragraph of 4-6 "
            "sentences for EACH name (SK Hynix, Samsung, AMD, Micron, Broadcom, "
            "SanDisk, SOXX, SK Telecom, Samsung SDS, Naver, KODEX 200). For each: tie "
            "the SPECIFIC news (from the newspapers above) to its real change%, then "
            "add the technical read (price vs MA5/MA20/MA60, volume) and a forward "
            "view. Be SPECIFIC per name — never reuse a generic line like 'affected "
            "by the broader sector' for multiple companies. You MAY also draw on the "
            "ADDITIONAL UNATTRIBUTED MARKET DATA here — but NEVER name its source.\n"
            "- IMPORTANT: ONLY the named newspapers above get a '### ' section in "
            "Section 2. The ADDITIONAL UNATTRIBUTED MARKET DATA must NOT get a section "
            "and its source must NEVER be named anywhere in the report.\n"
            f"- Section 4 (Catalysts & Schedule / 일정매매): {_cat.CATALYST_SECTION_RULE}\n"
            "- Section 5 (Recommendations): FIRST a Markdown table | Stock | Action | "
            "Reason | with Action = BUY / HOLD / SELL; THEN a '### Rationale' "
            "subsection with a paragraph per recommendation. TIE each call to a "
            "CATALYST and TIMING where possible (event-driven / 일정매매 — e.g. 'BUY "
            "before <event/date>, sell into the attention') grounded in the news + "
            "price/technicals.\n"
            "Be specific, cite the newspapers by name. This is a LONG report — the "
            "full-text Korean outlets each get 3-4 pages, so Section 2 alone is very "
            "long (4000+ words). Never truncate a section.\n"
            "Output ONLY the finished English Markdown report — no preamble."
        )
        user = (f"Date (KST): {kst_date} · USD/KRW: {rate:,.0f}\n\n"
                f"PRICE CONTEXT (for your analysis only — do NOT print a table):\n{_kr._facts(rows)}\n\n"
                f"NEWS BY NEWSPAPER:\n{_news_block_by_source(grouped)}\n\n"
                f"ADDITIONAL UNATTRIBUTED MARKET DATA (use ONLY in Section 3 Company "
                f"Analysis and Section 5 Recommendations — do NOT name the source and "
                f"do NOT give it a '### ' section):\n{_hidden_block(hidden)}\n\n"
                f"CATALYST / EVENT DATA (for Section 4):\n{_cat.catalyst_block(catalysts)}")
        out = chat_completion_sync(
            system_prompt=sysmsg, messages=[{"role": "user", "content": user[:60000]}],
            max_tokens=16000, temperature=0.5, model="groq-llama-3.3-70b") or ""
        bad = (not out.strip()) or out.lstrip().startswith(("[LLM unavailable]", "[server error]"))
        if not bad:
            detail_en = out.strip()
            try:
                ko_sys = (
                    "You are a professional Korean financial translator. Translate the "
                    "ENTIRE English newspaper report into natural, professional Korean "
                    "(존댓말). Translate EVERYTHING — never summarise or stub. Preserve "
                    "ALL Markdown, headings and sub-headings; keep every number, %, 원, "
                    "ticker and newspaper name IDENTICAL. Output ONLY the Korean Markdown.")
                ko_out = chat_completion_sync(
                    system_prompt=ko_sys,
                    messages=[{"role": "user", "content": detail_en[:45000]}],
                    max_tokens=16000, temperature=0.3, model="groq-llama-3.3-70b") or ""
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
                     f"## 1. General Overview\n{sum_en}\n\n"
                     f"## 2. News by Newspaper\n" + "\n".join(src_lines) + "\n\n"
                     f"## 3. Company-Specific Analysis\nSee headlines above.\n\n"
                     f"## 4. Catalysts & Schedule (일정매매)\n"
                     + _cat.catalyst_block(catalysts) + "\n\n"
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
        "news_sources": sources_flat[:50],
        "newspapers": [p["name"] for p in NEWSPAPERS],
        "source": "TripleH Newspaper Analysis (last-24h full-text: 매일경제·한국경제·머니투데이·SBS Biz; "
                  "headlines: Bloomberg·WSJ + OHLCV)",
    }
