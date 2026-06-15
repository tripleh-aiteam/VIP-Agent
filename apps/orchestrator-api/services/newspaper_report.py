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
    {"name": "Maeil Business (매일경제)", "site": "mk.co.kr", "region": "KR"},
    {"name": "Korea Economic Daily (한국경제)", "site": "hankyung.com", "region": "KR"},
    {"name": "MoneyToday (머니투데이)", "site": "mt.co.kr", "region": "KR"},
    {"name": "SBS Biz", "site": "biz.sbs.co.kr", "region": "KR"},
    {"name": "Bloomberg", "site": "bloomberg.com", "region": "US"},
    {"name": "The Wall Street Journal", "site": "wsj.com", "region": "US"},
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
    """Recent (past-week) site-scoped search → [{title,url,snippet}]. Past-week
    so we cover news from ALL days/times, not only this morning."""
    try:
        from services.web_search import search_web
    except Exception:
        return []
    kw = (_KR_KEYWORDS + " OR 정책 OR 트럼프 OR 금리 OR 환율") if kr else \
         (_US_KEYWORDS + " OR Fed OR tariff OR Trump OR Iran OR policy")
    out: list[dict] = []
    seen: set[str] = set()
    try:
        res = search_web(f"site:{site} ({kw})", num_results=per_query, recency="w")
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


# Keep only stock/market/economy articles (drop sports/society/entertainment that
# leak in from general RSS feeds, esp. with the wider time window).
_MARKET_KW = (
    "증시", "주식", "주가", "코스피", "코스닥", "나스닥", "다우", "s&p", "반도체", "삼성",
    "하이닉스", "네이버", "텔레콤", "sds", "경제", "금리", "환율", "실적", "엔비디아", "증권",
    "투자", "달러", "무역", "관세", "수출", "연준", "fed", "기업", "상장", "배당", "인수",
    "합병", "공시", "매출", "영업이익", "메모리", "hbm", "트럼프", "유가", "원유", "채권",
    "펀드", "ipo", "비트코인", "전기차", "배터리", "바이오", "코스피", "외국인", "기관", "마감",
    "stock", "market", "nasdaq", "fed", "semiconductor", "earnings", "tariff", "chip",
)
_OFFTOPIC_PATHS = ("/sports", "/society", "/entertain", "/culture", "/life", "/people",
                   "/travel", "/health/", "/opinion")


def _is_market_relevant(it: dict) -> bool:
    """True only for stock/market/economy articles (filters out sports/society)."""
    url = (it.get("url") or "").lower()
    if any(p in url for p in _OFFTOPIC_PATHS):
        return False
    blob = ((it.get("title") or "") + " " + (it.get("summary") or "")
            + " " + (it.get("text") or "")).lower()
    return any(k in blob for k in _MARKET_KW)


def _domain_ok(url: str, site: str) -> bool:
    """True only if the URL belongs to the outlet's own domain — so every
    reference link is from that newspaper, never an external site."""
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lower().lstrip("www.")
        base = (site or "").lower().split("/")[0].lstrip("www.")
        return bool(base) and (host == base or host.endswith("." + base) or host.endswith(base))
    except Exception:
        return False


def _gather_news_by_source(cap_kr: int = 7, cap_paid: int = 8) -> dict[str, list[dict]]:
    """Collect recent (last ~72h, all times of day) articles per outlet.
      - Korean (free) outlets: RSS list (timestamped) + FULL article text
        (trafilatura); paywalled premium → its RSS summary.
      - Paid (WSJ/Bloomberg): recent headlines + free snippets only (no body).
    Returns {name: [{title,url,text,full,paid}]}."""
    from services import news_fetch
    grouped: dict[str, list[dict]] = {}
    for paper in NEWSPAPERS:
        name, site, kr = paper["name"], paper["site"], paper["region"] == "KR"
        paid = name in ("Bloomberg", "The Wall Street Journal")
        arts: list[dict] = []
        if paid:
            for h in _recency_search(site, kr, per_query=cap_paid + 5):
                if not _domain_ok(h.get("url", ""), site):
                    continue  # only this outlet's own domain
                arts.append({"title": h["title"], "url": h["url"], "text": h["snippet"],
                             "full": False, "paid": True, "pub": None})
                if len(arts) >= cap_paid:
                    break
        else:
            # Korean free outlet — RSS list (last ~72h = all days/times), search fallback.
            items = news_fetch.rss_items(name, hours=72, cap=cap_kr + 8)
            if len(items) < 5:
                items += _recency_search(site, kr, per_query=10)
            picked: list[dict] = []
            seen: set[str] = set()
            for it in items:
                u = it.get("url", "")
                if not u or u in seen or not _domain_ok(u, site):
                    continue  # only this outlet's own domain
                if not _is_market_relevant(it):
                    continue  # drop sports/society/etc. — stock & market news only
                seen.add(u)
                picked.append(it)
                if len(picked) >= cap_kr:
                    break
            for it in picked:
                body = news_fetch.fetch_fulltext(it["url"], max_chars=3000)
                arts.append({"title": it.get("title", ""), "url": it.get("url", ""),
                             "text": body or it.get("summary", ""),
                             "full": bool(body), "paid": False, "pub": it.get("pub")})
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


def _sources_md(arts: list[dict]) -> str:
    """Clickable source links (with publish date when known) so the reader can
    verify each article is real and published within the last day."""
    lines = []
    for a in arts:
        u = (a.get("url") or "").strip()
        if not u:
            continue
        t = (a.get("title") or u).strip().replace("[", "(").replace("]", ")")[:120]
        pub = a.get("pub")
        when = f" — {pub[:16].replace('T', ' ')}" if pub else ""
        lines.append(f"- [{t}]({u}){when}")
    return "\n".join(lines) if lines else "- (no source links available)"


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


def _parse_numbered(text: str) -> dict[int, str]:
    """Parse an LLM block of '[1] summary\\n[2] summary …' → {n: summary}."""
    out: dict[int, str] = {}
    if not text:
        return out
    for m in re.finditer(r"\[(\d+)\]\s*(.*?)(?=\n\s*\[\d+\]|\Z)", text, re.S):
        out[int(m.group(1))] = m.group(2).strip()
    return out


def _article_section(name: str, arts: list[dict], summaries: dict[int, str]) -> str:
    """Per-article layout: each article = clickable TITLE (link) + its SUMMARY.
    Falls back to the raw snippet when the LLM gave no summary for that article."""
    lines = [f"### {name}"]
    for i, a in enumerate(arts, 1):
        title = (a.get("title") or "").strip().replace("[", "(").replace("]", ")")[:170] or "(제목 없음)"
        url = (a.get("url") or "").strip()
        pub = a.get("pub")
        when = f" · {pub[:16].replace('T', ' ')}" if pub else ""
        s = summaries.get(i) or (a.get("text") or "")[:400]
        head = f"#### [{title}]({url}){when}" if url else f"#### {title}{when}"
        lines.append(head)
        if s:
            lines.append(s.strip())
        lines.append("")
    return "\n".join(lines).rstrip()


def _stats_block(rows: list[dict]) -> str:
    """Per-ticker daily + weekly volume & price-change — feeds the richer
    recommendation table's rationale."""
    lines = []
    for r in rows:
        if not r.get("ok"):
            continue
        dvol = f"{int(r['volume']):,}" if r.get("volume") is not None else "—"
        wvol = f"{int(r['weekly_volume']):,}" if r.get("weekly_volume") is not None else "—"
        dchg = _kr._fmt_chg(r.get("change_pct"))
        wchg = _kr._fmt_chg(r.get("weekly_change_pct"))
        lines.append(f"- {r['ko']} ({r['t']}): 일일 등락 {dchg} · 일일 거래량 {dvol} · "
                     f"주간 등락 {wchg} · 주간 거래량 {wvol}")
    return "\n".join(lines) if lines else "(no stats)"


def _merge_hourly_news(db, grouped: dict[str, list[dict]], cap_per_outlet: int = 8) -> dict[str, list[dict]]:
    """Fold the day's hourly newspaper snapshots into the fresh fetch (dedupe by
    URL, cap per outlet). Fresh full-text articles are kept first; accumulated
    snapshot headlines fill the rest so the day's full coverage is represented."""
    try:
        from services import hourly_capture
        acc = hourly_capture.accumulated(db, "newspaper", hours=26)
    except Exception as e:
        log.warning(f"newspaper hourly merge skipped: {str(e)[:80]}")
        return grouped
    by_outlet: dict[str, list[dict]] = {}
    for it in acc:
        by_outlet.setdefault(it.get("outlet", ""), []).append(it)
    for paper in NEWSPAPERS:
        name = paper["name"]
        cur = grouped.get(name, [])
        urls = {a.get("url") for a in cur}
        for it in by_outlet.get(name, []):
            if len(cur) >= cap_per_outlet:
                break
            if it.get("url") and it["url"] not in urls:
                urls.add(it["url"])
                cur.append({"title": it.get("title", ""), "url": it.get("url", ""),
                            "text": it.get("snippet", ""), "full": False,
                            "paid": False, "pub": it.get("pub")})
        grouped[name] = cur[:cap_per_outlet]
    return grouped


def build_newspaper_report(db, trace_id: str) -> dict:
    """Build the daily per-newspaper news report — same price table as Kiwoom +
    per-outlet news analysis + a BUY/HOLD/SELL recommendation, bilingual EN/KO."""
    rows, table_en, table_ko, rate = _kr.gather_priced_rows()
    ok_rows = [r for r in rows if r.get("ok")]
    grouped = _gather_news_by_source()
    # Merge in the day's hourly snapshots (the '24 parts') so the morning report
    # synthesises the WHOLE day/night of news, not just this fetch.
    grouped = _merge_hourly_news(db, grouped)
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

        # ---- 1) Per-outlet sections: a SUMMARY PER ARTICLE under its title+link.
        #         One LLM call per outlet returns a numbered summary for every
        #         article; we then lay out  #### [title](link)  +  summary.  ----
        sec_en: dict[str, str] = {}
        sec_ko: dict[str, str] = {}
        for paper in NEWSPAPERS:
            name = paper["name"]
            arts = grouped.get(name, [])
            paid = name in ("Bloomberg", "The Wall Street Journal")
            if not arts:
                sec_en[name] = f"### {name}\nNo fresh items in the recent window."
                sec_ko[name] = f"### {name}\n최근 신규 기사가 없습니다."
                continue
            has_full = any(a.get("full") for a in arts)
            # Number each article so the model's summaries map back 1:1.
            corpus = "\n\n".join(
                f"[{i}] ({'FULL ARTICLE' if a.get('full') else 'HEADLINE/SNIPPET'}) "
                f"{a.get('title','')}\n{(a.get('text') or '')[:3200]}"
                for i, a in enumerate(arts, 1))
            if paid or not has_full:
                per = ("a solid 8-10 sentence summary — work only with the headline/snippet, "
                       "do NOT fabricate detail you don't have")
            else:
                per = ("AT LEAST HALF A PAGE (250-380 words, ~14-20 sentences) — a thorough, "
                       "in-depth summary: what happened, ALL the key facts/figures/quotes from "
                       "the body, the companies/sectors affected, the cause, and the market "
                       "impact & outlook. Be substantial, never one short paragraph")
            sysd = (
                "You are TripleH's market-news analyst. Below are NUMBERED recent news "
                f"articles from {name}. For EVERY numbered article, write a DETAILED "
                f"summary — {per}. Explain WHY it matters for stocks/markets "
                f"(watchlist: {_WATCHLIST}; semiconductors; macro FX/rates; market-moving "
                "politics — Trump/tariffs/Fed/Iran). Use ONLY the provided text; NEVER "
                "invent quotes or numbers; do NOT include any URLs. Write a block for "
                "EVERY article number, in order, never skipping one, and never truncate. "
                "Output EXACTLY:\n"
                "===EN===\n[1] <summary>\n[2] <summary>\n…\n===KO===\n"
                "[1] <한국어 요약 존댓말>\n[2] <한국어 요약>\n…")
            en_sum: dict[int, str] = {}
            ko_sum: dict[int, str] = {}
            try:
                out = chat_completion_sync(
                    system_prompt=sysd, messages=[{"role": "user", "content": corpus[:22000]}],
                    max_tokens=16000, temperature=0.45, model="groq-llama-3.3-70b") or ""
                en_txt, ko_txt = _split_enko(out)
                en_sum = _parse_numbered(en_txt)
                ko_sum = _parse_numbered(ko_txt)
            except Exception as e:
                log.warning(f"newspaper outlet {name} failed: {str(e)[:100]}")
            # Lay out per-article: title (clickable) + its summary.
            sec_en[name] = _article_section(name, arts, en_sum)
            sec_ko[name] = _article_section(name, arts, ko_sum or en_sum)
            _t.sleep(0.5)

        news_en = "\n\n".join(sec_en[p["name"]] for p in NEWSPAPERS)
        news_ko = "\n\n".join(sec_ko[p["name"]] for p in NEWSPAPERS)

        # ---- 2) Synthesis: Overview + Company + Catalysts + Recommendations ----
        digest = "\n\n".join(f"[{p['name']}]\n" + (sec_en.get(p["name"], "")[:1100]) for p in NEWSPAPERS)
        stats = _stats_block(rows)
        ssys = (
            "You are TripleH's chief market analyst. Using the per-newspaper summaries "
            "+ stock stats + catalyst data below, write these sections (do NOT write a "
            "'News by Newspaper' section — it is added separately; do NOT print a price "
            "table). ALL prices are KRW. Sections:\n"
            "## 1. General Overview\n## 3. Company-Specific Analysis\n"
            "## 4. Catalysts & Schedule (일정매매)\n## 5. Recommendations\n\n"
            "- Section 1: a RICH 3-4 paragraph consensus read of the recent news across "
            "the newspapers (stock moves + market-moving politics + macro + sector).\n"
            "- Section 3 (Company-Specific): a dedicated 5-7 sentence paragraph for EACH "
            f"name ({_WATCHLIST}) tying the news to its daily & weekly change% and "
            "volume trend + the technical read (price vs MA5/MA20/MA60). Use the STOCK "
            "STATS provided. You MAY use the UNATTRIBUTED data but NEVER name its source.\n"
            f"- Section 4 (Catalysts & Schedule / 일정매매): {_cat.CATALYST_SECTION_RULE}\n"
            "- Section 5 (Recommendations): a DETAILED table with EXACTLY these columns: "
            "| 종목 | 의견 | 일일 등락 | 일일 거래량 | 주간 등락 | 주간 거래량 | 핵심 근거 | — "
            "use the KOREAN stock names EXACTLY as written in STOCK STATS (삼성전자, "
            "SK하이닉스, 네이버, 마이크론, 브로드컴, 샌디스크 …), never English. "
            "fill the volume/change columns from the STOCK STATS (NEVER invent them). "
            "핵심 근거 MUST be a SPECIFIC concrete reason WITH A SOURCE CITATION — name the "
            "newspaper/article it came from in brackets, e.g. '주간 거래량 급증 + HBM 수요 "
            "[출처: 한국경제]' or '외국인 순매수 전환 [출처: 매일경제]'. NEVER write a generic "
            "placeholder like '기술적 분석' or '근거'. 의견 = 매수/보유/매도. THEN a "
            "'### 근거 상세' subsection: for EACH stock, 3-4 sentences explaining the call "
            "from (a) the news (WITH the source outlet named), (b) the daily vs weekly "
            "volume & price trend, (c) a catalyst + timing. Every '매도/매수' claim MUST "
            "state WHY and from WHICH source.\n"
            "Write Korean section HEADINGS in the ===KO=== version: '## 1. 총평', "
            "'## 3. 종목별 분석', '## 4. 일정·촉매 (일정매매)', '## 5. 추천'. NO English prose "
            "anywhere in the Korean version.\n"
            "Use ONLY provided data; never invent a number. Output EXACTLY:\n===EN===\n"
            "<english>\n===KO===\n<korean 존댓말>")
        suser = (f"TODAY (KST): {kst_date} · USD/KRW: {rate:,.0f}\n\n"
                 f"STOCK STATS (daily & weekly volume + change% — use these EXACT numbers "
                 f"in the Section 5 table):\n{stats}\n\n"
                 f"PRICE/TECHNICAL CONTEXT:\n{_kr._facts(rows)}\n\n"
                 f"PER-NEWSPAPER SUMMARIES:\n{digest}\n\n"
                 f"UNATTRIBUTED MARKET DATA (use, NEVER name):\n{_hidden_block(hidden)}\n\n"
                 f"CATALYST DATA:\n{_cat.catalyst_block(catalysts)}")
        syn_en = syn_ko = ""
        try:
            out = chat_completion_sync(
                system_prompt=ssys, messages=[{"role": "user", "content": suser[:24000]}],
                max_tokens=10000, temperature=0.45, model="groq-llama-3.3-70b") or ""
            syn_en, syn_ko = _split_enko(out)
        except Exception as e:
            log.warning(f"newspaper synthesis failed: {str(e)[:100]}")

        # ---- 3) Assemble: overview → News by Newspaper → rest ----
        def _assemble(syn: str, news: str) -> str:
            syn = syn or ""
            parts = re.split(r"(?=##\s*3\.)", syn, maxsplit=1)
            overview = parts[0].strip() if parts else ""
            rest = parts[1].strip() if len(parts) > 1 else ""
            return (f"# 신문 시장 분석 리포트\n*{kst_date} (최근 수일)*\n\n"
                    f"{overview}\n\n## 2. 신문별 뉴스\n{news}\n\n{rest}").strip()

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
