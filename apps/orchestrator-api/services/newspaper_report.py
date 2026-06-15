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


def _gather_news_by_source(cap_kr: int = 5, cap_paid: int = 6) -> dict[str, list[dict]]:
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


# Korean display names for the outlet section headers (the NEWSPAPERS "name"
# keys carry English for search/RSS; show Korean in the report).
_DISPLAY_KO = {
    "Maeil Business (매일경제)": "매일경제",
    "Korea Economic Daily (한국경제)": "한국경제",
    "MoneyToday (머니투데이)": "머니투데이",
    "SBS Biz": "SBS Biz",
    "Bloomberg": "블룸버그",
    "The Wall Street Journal": "월스트리트저널(WSJ)",
}


def _article_section(name: str, arts: list[dict], summaries: dict[int, str]) -> str:
    """Per-article layout: clickable Korean TITLE (link) + its Korean SUMMARY.
    The LLM returns each block as '<한국어 제목> @@@ <한국어 요약>' so even foreign
    (WSJ/Bloomberg) headlines display in Korean. Falls back to the raw title."""
    lines = [f"### {_DISPLAY_KO.get(name, name)}"]
    for i, a in enumerate(arts, 1):
        url = (a.get("url") or "").strip()
        pub = a.get("pub")
        when = f" · {pub[:16].replace('T', ' ')}" if pub else ""
        raw = (summaries.get(i) or "").strip()
        ko_title, body = "", raw
        if "@@@" in raw:
            ko_title, body = [p.strip() for p in raw.split("@@@", 1)]
        title = (ko_title or a.get("title") or "").strip().replace("[", "(").replace("]", ")")[:170] or "(제목 없음)"
        body = body or (a.get("text") or "")[:400]
        head = f"#### [{title}]({url}){when}" if url else f"#### {title}{when}"
        lines.append(head)
        if body:
            lines.append(body.strip())
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


# Outlet name → homepage, for turning [출처: 매체] citations into clickable links.
_SOURCE_URLS = {
    "한국경제": "https://www.hankyung.com", "매일경제": "https://www.mk.co.kr",
    "머니투데이": "https://www.mt.co.kr", "SBS Biz": "https://biz.sbs.co.kr",
    "SBS": "https://biz.sbs.co.kr", "Bloomberg": "https://www.bloomberg.com",
    "블룸버그": "https://www.bloomberg.com", "WSJ": "https://www.wsj.com",
    "월스트리트저널": "https://www.wsj.com", "월스트리트": "https://www.wsj.com",
}


def _linkify_sources(md: str) -> str:
    """Turn plain [출처: 매체] citations into clickable [출처: 매체](url) links."""
    def repl(m):
        inner = m.group(1)
        for name, url in _SOURCE_URLS.items():
            if name in inner:
                return f"[{inner}]({url})"
        return m.group(0)
    # Match [출처: ...] that is NOT already followed by '(' (i.e. not yet a link).
    return re.sub(r"\[(출처[:：][^\]]+)\](?!\()", repl, md or "")


def _merge_hourly_news(db, grouped: dict[str, list[dict]], cap_per_outlet: int = 6) -> dict[str, list[dict]]:
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
        sec_ko: dict[str, str] = {}
        for paper in NEWSPAPERS:
            name = paper["name"]
            arts = grouped.get(name, [])
            paid = name in ("Bloomberg", "The Wall Street Journal")
            if not arts:
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
                "당신은 TripleH의 시장 뉴스 애널리스트입니다. 아래는 "
                f"{name}의 최근 뉴스 기사(번호 매김)입니다. 모든 번호의 기사에 대해 "
                f"한국어 제목과 상세한 한국어 요약(존댓말)을 작성하세요 — {per}. 주식·시장에 "
                f"왜 중요한지 설명하세요(관심종목: {_WATCHLIST}; 반도체; 거시 환율/금리; 시장을 "
                "움직이는 정치 — 트럼프/관세/연준/이란). 제공된 텍스트만 사용하고 수치를 "
                "지어내지 마세요. URL 금지. 영어로 된 원문 기사(Bloomberg/WSJ 등)도 제목과 "
                "본문을 모두 한국어로 번역하세요. 회사·기관명도 한국어로(삼성전자, SK하이닉스, "
                "엔비디아, 마이크론, 브로드컴, 연준 등). 출력 어디에도 영어 문장이 있으면 안 됩니다. "
                "모든 번호의 기사를 순서대로 빠짐없이 작성하세요. 각 기사는 '한국어 제목 @@@ "
                "한국어 요약' 형식으로.\n"
                "출력 형식(정확히):\n[1] <한국어 제목> @@@ <한국어 요약>\n"
                "[2] <한국어 제목> @@@ <한국어 요약>\n…")
            ko_sum: dict[int, str] = {}
            try:
                out = chat_completion_sync(
                    system_prompt=sysd, messages=[{"role": "user", "content": corpus[:18000]}],
                    max_tokens=10000, temperature=0.45, model="groq-llama-3.3-70b", prefer_paid=True) or ""
                if out and not out.lstrip().startswith(("[LLM unavailable]", "[server error]")):
                    ko_sum = _parse_numbered(out)
            except Exception as e:
                log.warning(f"newspaper outlet {name} failed: {str(e)[:100]}")
            # Lay out per-article: title (clickable) + its Korean summary.
            sec_ko[name] = _article_section(name, arts, ko_sum)
            _t.sleep(1.5)   # space outlet calls so we stay under Groq's TPM limit

        news_ko = "\n\n".join(sec_ko[p["name"]] for p in NEWSPAPERS)

        # ---- 2) Synthesis: Overview + Company + Catalysts + Recommendations (Korean) ----
        digest = "\n\n".join(f"[{p['name']}]\n" + (sec_ko.get(p["name"], "")[:1100]) for p in NEWSPAPERS)
        stats = _stats_block(rows)
        ssys = (
            "당신은 TripleH의 수석 시장 애널리스트입니다. 아래의 신문별 요약 + 종목 통계 + "
            "촉매 데이터를 사용해 다음 섹션을 모두 한국어(존댓말)로 작성하세요. 출력에 영어 "
            "문장이 있으면 절대 안 됩니다('General Overview' 같은 영어 제목도 금지). "
            "'신문별 뉴스' 섹션은 따로 추가되니 쓰지 말고, 시세 표도 출력하지 마세요. "
            "모든 가격은 원(KRW). 섹션 제목은 정확히 이렇게:\n"
            "## 1. 총평\n## 3. 종목별 분석\n## 4. 일정·촉매 (일정매매)\n## 5. 추천\n\n"
            "- ## 1. 총평: 최근 뉴스에 대한 풍부한 3-4단락 종합(종목 움직임 + 시장을 움직이는 "
            "정치 + 거시 + 섹터).\n"
            f"- ## 3. 종목별 분석: 각 종목({_WATCHLIST})마다 5-7문장 단락으로, 뉴스를 일일·주간 "
            "등락%와 거래량 추세 + 기술적 분석(MA5/MA20/MA60 대비)에 연결하세요. STOCK STATS를 "
            "사용하고, UNATTRIBUTED 데이터는 출처를 밝히지 말고 활용만 하세요. 종목명은 한국어로.\n"
            f"- ## 4. 일정·촉매 (일정매매): {_cat.CATALYST_SECTION_RULE}\n"
            "- ## 5. 추천: 정확히 이 컬럼의 표: | 종목 | 의견 | 일일 등락 | 일일 거래량 | "
            "주간 등락 | 주간 거래량 | 핵심 근거 | — 종목명은 STOCK STATS의 한국어 이름 그대로"
            "(삼성전자, SK하이닉스, 네이버, 마이크론, 브로드컴, 샌디스크 …), 영어 금지. "
            "등락/거래량 컬럼은 STOCK STATS 값을 그대로(지어내지 말 것). 핵심 근거는 구체적 "
            "사유 + 출처를 대괄호로(예: '주간 거래량 급증 + HBM 수요 [출처: 한국경제]'). "
            "'기술적 분석' 같은 막연한 표현 금지. 의견 = 매수/보유/매도. 그 다음 "
            "'### 근거 상세' 소제목: 각 종목마다 5-7문장의 상세 단락(① 구체적 뉴스+출처, "
            "② 일일·주간 거래량·가격 추세를 실제 숫자로, ③ 외국인/기관 수급, ④ 촉매+시점). "
            "각 종목 단락은 서로 다른 문체로(템플릿 복붙 금지), 끝에 [출처: <매체>]를 붙이세요.\n"
            "제공된 데이터만 사용하고 숫자를 지어내지 마세요. 한국어 마크다운만 출력하세요.")
        suser = (f"오늘(KST): {kst_date} · USD/KRW: {rate:,.0f}\n\n"
                 f"STOCK STATS (일일·주간 거래량·등락% — 5번 표에 이 숫자 그대로 사용):\n{stats}\n\n"
                 f"가격/기술적 컨텍스트:\n{_kr._facts(rows)}\n\n"
                 f"신문별 요약:\n{digest}\n\n"
                 f"UNATTRIBUTED 시장 데이터 (활용하되 출처 언급 금지):\n{_hidden_block(hidden)}\n\n"
                 f"촉매 데이터:\n{_cat.catalyst_block(catalysts)}")
        syn_ko = ""
        try:
            out = chat_completion_sync(
                system_prompt=ssys, messages=[{"role": "user", "content": suser[:24000]}],
                max_tokens=10000, temperature=0.45, model="groq-llama-3.3-70b", prefer_paid=True) or ""
            if out and not out.lstrip().startswith(("[LLM unavailable]", "[server error]")):
                syn_ko = out.replace("===KO===", "").replace("===EN===", "").strip()
        except Exception as e:
            log.warning(f"newspaper synthesis failed: {str(e)[:100]}")

        # ---- 3) Assemble (Korean only): overview → 신문별 뉴스 → rest ----
        def _assemble(syn: str, news: str) -> str:
            syn = syn or ""
            parts = re.split(r"(?=##\s*3\.)", syn, maxsplit=1)
            overview = parts[0].strip() if parts else ""
            rest = parts[1].strip() if len(parts) > 1 else ""
            return (f"# 신문 시장 분석 리포트\n*{kst_date} (최근 수일)*\n\n"
                    f"{overview}\n\n## 2. 신문별 뉴스\n{news}\n\n{rest}").strip()

        if news_ko.strip() and syn_ko.strip():
            detail_ko = _linkify_sources(_assemble(syn_ko, news_ko))
            detail_en = detail_ko   # Korean-only report; keep field populated for master
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
