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

import re
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


def _split_enko(out: str) -> tuple[str, str]:
    """Split an LLM response on ===EN=== / ===KO=== markers → (en, ko)."""
    out = (out or "").strip()
    if (not out) or out.lstrip().startswith(("[LLM unavailable]", "[server error]")):
        return "", ""
    if "===KO===" in out:
        a, b = out.split("===KO===", 1)
        return a.replace("===EN===", "").strip(), b.strip()
    return out.replace("===EN===", "").strip(), ""


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

    # Fetch stats (how much real full text we got per outlet) — for verification.
    fetch_stats = {name: {"n": len(arts),
                          "full": sum(1 for a in arts if a.get("full")),
                          "chars": sum(len(a.get("text", "") or "") for a in arts)}
                   for name, arts in grouped.items()}

    _WATCHLIST = ("SK Hynix, Samsung Electronics, Naver, SK Telecom, Samsung SDS, "
                  "AMD, Micron, Broadcom, SanDisk, SOXX, KODEX 200")
    detail_en = detail_ko = ""
    try:
        from services.llm_client import chat_completion_sync
        import time as _t

        # ---- 1) Per-outlet sections (each its OWN call → guaranteed depth) ----
        sec_en: dict[str, str] = {}
        sec_ko: dict[str, str] = {}
        for paper in NEWSPAPERS:
            name = paper["name"]
            arts = grouped.get(name, [])
            paid = name in ("Bloomberg", "The Wall Street Journal")
            if not arts:
                sec_en[name] = f"### {name}\nNo fresh items in the last 24 hours."
                sec_ko[name] = f"### {name}\n지난 24시간 내 신규 기사가 없습니다."
                continue
            has_full = any(a.get("full") for a in arts)
            corpus = "\n\n".join(
                f"[{'FULL ARTICLE' if a.get('full') else 'HEADLINE/SUMMARY'}] {a.get('title','')}\n{(a.get('text') or '')[:3000]}"
                for a in arts)
            if paid or not has_full:
                length = "150-220 words (about half a page)"
                note = ("These are paywalled HEADLINE/SUMMARY only — analyse what is "
                        "there, do NOT fabricate article detail you don't have.")
            else:
                length = "700-1100 words (a deep 3-4 page analysis)"
                note = ("[FULL ARTICLE] items give you the full body — read it and cite "
                        "concrete facts/figures.")
            sysd = (
                "You are TripleH's market-news analyst. Below are articles from ONE "
                f"newspaper — {name} — published in the LAST 24 HOURS. Write {length} "
                "covering EVERYTHING that can move stock prices: direct stock/market "
                "news, market-moving POLITICS/policy (Trump, tariffs, Iran, Fed, "
                "elections), macro (FX, rates), the semiconductor sector, and the "
                f"watchlist ({_WATCHLIST}). Flowing analytical prose — explain the price "
                f"impact, not a headline list. {note} Use ONLY the provided text; NEVER "
                f"invent quotes or numbers. Begin with the heading '### {name}'. "
                "Output EXACTLY:\n===EN===\n<english>\n===KO===\n<korean 존댓말, same depth>")
            try:
                out = chat_completion_sync(
                    system_prompt=sysd, messages=[{"role": "user", "content": corpus[:20000]}],
                    max_tokens=7000, temperature=0.45, model="groq-llama-3.3-70b") or ""
                en, ko = _split_enko(out)
                sec_en[name] = en or (f"### {name}\n" + "\n".join(f"- {a.get('title','')}" for a in arts))
                sec_ko[name] = ko or sec_en[name]
            except Exception as e:
                log.warning(f"newspaper outlet {name} failed: {str(e)[:100]}")
                sec_en[name] = f"### {name}\n" + "\n".join(f"- {a.get('title','')}" for a in arts)
                sec_ko[name] = sec_en[name]
            _t.sleep(0.5)

        news_en = "\n\n".join(sec_en[p["name"]] for p in NEWSPAPERS)
        news_ko = "\n\n".join(sec_ko[p["name"]] for p in NEWSPAPERS)

        # ---- 2) Synthesis: Overview + Company + Catalysts + Recommendations ----
        digest = "\n\n".join(f"[{p['name']}] " + (sec_en.get(p["name"], "")[:650]) for p in NEWSPAPERS)
        ssys = (
            "You are TripleH's chief market analyst. Using the per-newspaper summaries "
            "+ price data + catalyst data below, write these sections (do NOT write a "
            "'News by Newspaper' section — it is added separately; do NOT print a price "
            "table). ALL prices are KRW. Sections:\n"
            "## 1. General Overview\n## 3. Company-Specific Analysis\n"
            "## 4. Catalysts & Schedule (일정매매)\n## 5. Recommendations\n\n"
            "- Section 1: 2-3 paragraph consensus read of the last 24h across the "
            "newspapers (stock + market-moving politics + macro).\n"
            "- Section 3 (Company-Specific): a dedicated 4-6 sentence paragraph for EACH "
            f"name ({_WATCHLIST}) tying the news to its real change% + the technical read "
            "(price vs MA5/MA20/MA60, volume). You MAY use the UNATTRIBUTED data here but "
            "NEVER name its source.\n"
            f"- Section 4 (Catalysts & Schedule / 일정매매): {_cat.CATALYST_SECTION_RULE}\n"
            "- Section 5 (Recommendations): a table | Stock | Action | Reason | (BUY/HOLD/"
            "SELL); THEN '### Rationale' tying each to a catalyst + timing.\n"
            "Use ONLY provided data; never invent. Output EXACTLY:\n===EN===\n<english>\n"
            "===KO===\n<korean 존댓말>")
        suser = (f"TODAY (KST): {kst_date} · USD/KRW: {rate:,.0f}\n\n"
                 f"PRICE CONTEXT (do NOT print a table):\n{_kr._facts(rows)}\n\n"
                 f"PER-NEWSPAPER SUMMARIES:\n{digest}\n\n"
                 f"UNATTRIBUTED MARKET DATA (use, NEVER name):\n{_hidden_block(hidden)}\n\n"
                 f"CATALYST DATA:\n{_cat.catalyst_block(catalysts)}")
        syn_en = syn_ko = ""
        try:
            out = chat_completion_sync(
                system_prompt=ssys, messages=[{"role": "user", "content": suser[:22000]}],
                max_tokens=9000, temperature=0.45, model="groq-llama-3.3-70b") or ""
            syn_en, syn_ko = _split_enko(out)
        except Exception as e:
            log.warning(f"newspaper synthesis failed: {str(e)[:100]}")

        # ---- 3) Assemble: overview → News by Newspaper → rest ----
        def _assemble(syn: str, news: str) -> str:
            syn = syn or ""
            parts = re.split(r"(?=##\s*3\.)", syn, maxsplit=1)
            overview = parts[0].strip() if parts else ""
            rest = parts[1].strip() if len(parts) > 1 else ""
            return (f"# Newspaper Market Analysis\n*{kst_date} (last 24h)*\n\n"
                    f"{overview}\n\n## 2. News by Newspaper\n{news}\n\n{rest}").strip()

        if news_en.strip() and syn_en.strip():
            detail_en = _assemble(syn_en, news_en)
            detail_ko = _assemble(syn_ko or syn_en, news_ko or news_en)
    except Exception as e:
        log.warning(f"newspaper compose failed: {e}")

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
        "fetch_stats": fetch_stats,
        "source": "TripleH Newspaper Analysis (last-24h full-text: 매일경제·한국경제·머니투데이·SBS Biz; "
                  "headlines: Bloomberg·WSJ + OHLCV)",
    }
