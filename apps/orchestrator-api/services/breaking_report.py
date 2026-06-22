"""
breaking_report — event-driven "🚨 속보" market-impact report.

When a market-moving event happens (war / defense deals, trade/export wins,
policy & tariffs, macro shocks, semiconductor demand, etc.) this builds a DETAILED
report that maps the news → affected KOREAN stocks with:
  - direction (호재 ▲ / 악재 ▼),
  - strength (강/중/약),
  - expected-move % band (clearly marked as an ESTIMATE, never a promise),
  - confidence (높음/보통/낮음),
  - the causal chain (why),
and an overall severity score (1-10).

Sources are BROAD — Korean outlets AND international ones (Financial Post,
Global News, Reuters, Bloomberg, WSJ, Nikkei) because foreign news (e.g. a
Canadian submarine/ship order) moves Korean defense/shipbuilding names.

Reuses: services.web_search, services.news_fetch, services.llm_client,
report_email + telegram for delivery. Saved as report_type='breaking_report'.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from services.logger import log

# Broad Korean sector → representative tickers, so the LLM grounds its impact
# calls in REAL names we can price (it may also name others it knows).
SECTOR_UNIVERSE: dict[str, str] = {
    "방산(Defense)": "한화에어로스페이스(012450), 한국항공우주/KAI(047810), LIG넥스1(079550), 현대로템(064350), 한화시스템(272210)",
    "조선(Shipbuilding)": "HD현대중공업(329180), HD한국조선해양(009540), 삼성중공업(010140), 한화오션(042660)",
    "반도체(Semiconductors)": "삼성전자(005930), SK하이닉스(000660), 한미반도체(042700)",
    "2차전지(Battery)": "LG에너지솔루션(373220), 삼성SDI(006400), POSCO홀딩스(005490), 에코프로비엠(247540)",
    "자동차(Auto)": "현대차(005380), 기아(000270), 현대모비스(012330)",
    "원전(Nuclear)": "두산에너빌리티(034020), 한전기술(052690), 한국전력(015760)",
    "인터넷/IT": "NAVER(035420), 카카오(035720), 삼성SDS(018260)",
    "바이오": "삼성바이오로직스(207940), 셀트리온(068270)",
    "에너지/정유": "S-OIL(010950), GS(078930), SK이노베이션(096770)",
    "금융": "KB금융(105560), 신한지주(055550), 메리츠금융(138040)",
    "항공/여행": "대한항공(003490), 진에어(272450), 하나투어(039130)",
    "방산 ETF/지수": "KODEX 200(069500), ARIRANG K방산Fn",
}

# Our current core watchlist (held/tracked) — so the report flags 'our exposure'.
OUR_WATCHLIST = "삼성전자, SK하이닉스, SK텔레콤, 삼성SDS, NAVER, KODEX 200, AMD, 마이크론, 브로드컴, 샌디스크, 필라델피아반도체(SOXX)"

# Search queries — Korean + INTERNATIONAL, tuned to events that move KR stocks.
_BASE_QUERIES = [
    "Korea stock market biggest news today defense semiconductor",
    "한국 증시 급등 급락 주요 뉴스 오늘 반도체 방산 조선",
    "Korea defense export contract Hanwha KAI Hyundai Rotem news",
    "Korea shipbuilding order HD Hyundai Samsung Heavy Hanwha Ocean deal",
    "site:reuters.com South Korea stocks OR defense OR chips",
    "site:financialpost.com Korea OR Hanwha OR submarine OR shipbuilding",
    "Fed rate decision war tariff geopolitics impact Korea market",
    "삼성전자 SK하이닉스 HBM 엔비디아 반도체 수요 뉴스",
    "site:asia.nikkei.com Korea OR semiconductor OR shipbuilding OR battery",
    "연합인포맥스 이데일리 서울경제 증시 속보 종목 급등 급락",
    "site:defensenews.com Korea OR Hanwha OR KAI OR submarine",
    "Korea won dollar FX oil price KOSPI foreign investor flow news today",
    "South Korea defense export deal Poland Romania Middle East Canada Hanwha KAI Hyundai Rotem K2 K9 FA-50 contract",
    "site:politico.com Korea OR defense OR arms OR Poland OR submarine OR Hanwha OR shipbuilding",
    "site:breakingdefense.com OR site:janes.com South Korea OR Hanwha OR KAI export",
]

# Lighter query set for the every-15-min detector (keeps search cost down).
_TRIAGE_QUERIES = [
    "South Korea stock market breaking news biggest market mover now",
    "한국 증시 속보 급등 급락 주요 뉴스 오늘",
    "site:reuters.com OR site:asia.nikkei.com OR site:politico.com South Korea market OR chips OR defense",
    "South Korea defense export arms deal Poland Canada Middle East billion contract latest",
]


def _search(q: str, n: int = 6, recency: str | None = "d") -> list[dict]:
    try:
        from services.web_search import search_web
        res = search_web(q, num_results=n, recency=recency)
        if res.get("ok"):
            return [{"title": (h.get("title") or "").strip(),
                     "url": h.get("url", ""),
                     "snippet": (h.get("snippet") or "").strip(),
                     "date": (h.get("date") or "").strip(),
                     "window": recency or "d"}   # which recency pass it came from
                    for h in res.get("results", [])]
    except Exception as e:
        log.warning(f"breaking: search '{q[:40]}' failed: {str(e)[:80]}")
    return []


def gather_breaking(focus: str | None = None, seed_urls: list[str] | None = None) -> dict:
    """Collect news (Korean + international + any seed URLs) for the impact report.
    Returns {headlines:[...], articles:[{title,url,text}], focus}."""
    queries = list(_BASE_QUERIES)
    if focus:
        queries = [f"{focus} Korea stock impact", f"{focus} 한국 증시 영향 종목",
                   f"{focus} news"] + queries

    headlines: list[dict] = []
    seen: set[str] = set()
    # Freshest first: past-HOUR results, then top up with past-day only if thin
    # (so the report reflects 'now', not 2-hours-ago news).
    for rec in ("h", "d"):
        for q in queries[:10]:
            for h in _search(q, recency=rec):
                key = (h.get("title") or h.get("url") or "")[:90].lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                headlines.append(h)
            if len(headlines) >= 40:
                break
        if len(headlines) >= 15:   # enough fresh items — skip the day-wide top-up
            break

    # Pull FULL text for the most relevant articles (seed URLs first, then a few
    # headline URLs) so the LLM reasons from substance, not just snippets.
    articles: list[dict] = []
    from services import news_fetch
    urls = list(seed_urls or [])
    urls += [h["url"] for h in headlines if h.get("url")][:8]
    fetched: set[str] = set()
    for u in urls:
        if not u or u in fetched:
            continue
        fetched.add(u)
        try:
            body = news_fetch.fetch_fulltext(u, max_chars=3500)
        except Exception:
            body = ""
        if body:
            hm = next((h for h in headlines if h.get("url") == u), None)
            articles.append({"title": (hm or {}).get("title") or u, "url": u,
                             "text": body, "date": (hm or {}).get("date", ""),
                             "window": (hm or {}).get("window", "d")})
        if len(articles) >= 8:
            break

    return {"headlines": headlines, "articles": articles, "focus": focus}


def _freshness(h: dict) -> str:
    """Always return a time/freshness string — never blank.
    Explicit publisher date if present, else the recency window it was found in."""
    if h.get("date"):
        return h["date"]
    return "최근 1시간 이내 수집" if h.get("window") == "h" else "최근 24시간 이내 수집"


def _news_block(g: dict) -> str:
    parts = []
    if g.get("articles"):
        parts.append("=== FULL ARTICLES (substance) — cite URL + published/collected time ===")
        for a in g["articles"]:
            parts.append(f"[{a['title'][:140]}] (URL: {a['url']} · 게재/수집: {_freshness(a)})\n{a['text'][:2600]}")
    parts.append("\n=== HEADLINES (title · published/collected time · URL · snippet) ===")
    for h in g.get("headlines", [])[:30]:
        s = f"- {h['title'][:150]} · {_freshness(h)} · {h.get('url', '')}"
        if h.get("snippet"):
            s += f"\n    {h['snippet'][:200]}"
        parts.append(s)
    return "\n".join(parts)


def build_breaking_report(db, trace_id: str, focus: str | None = None,
                          seed_urls: list[str] | None = None) -> dict:
    """Build the detailed event-impact report (EN compose + KO translation)."""
    from services.kst import kst_label
    kst = kst_label()
    g = gather_breaking(focus, seed_urls)
    news = _news_block(g)
    universe = "\n".join(f"  - {k}: {v}" for k, v in SECTOR_UNIVERSE.items())

    # Live Kiwoom watchlist prices (current price + change%) so the report can give
    # real 현재가 + 매수가/매도가 advice for affected names we can actually price.
    price_ctx = ""
    try:
        from services import kiwoom_report as _kr
        _rows, _te, _price_ko, _rate = _kr.gather_priced_rows()
        price_ctx = _price_ko or ""
    except Exception as e:
        log.warning(f"breaking: kiwoom price ctx failed: {str(e)[:80]}")

    sum_en = (f"Breaking market-impact scan ({len(g['headlines'])} headlines, "
              f"{len(g['articles'])} full articles)" + (f" — focus: {focus}" if focus else "") + ".")
    sum_ko = (f"속보 시장영향 스캔 (헤드라인 {len(g['headlines'])}건, 본문 {len(g['articles'])}건)"
              + (f" — 초점: {focus}" if focus else "") + ".")

    detail_en = detail_ko = ""
    try:
        from services.llm_client import chat_completion_sync
        sysmsg = (
            "You are TripleH's senior market strategist writing a CONCISE, event-driven "
            "🚨 BREAKING-NEWS IMPACT report for the boss. Use the NEWS (Korean + international) "
            "AND the live KIWOOM PRICE TABLE provided. Foreign news matters (a Canadian "
            "submarine or Polish arms deal lifts Korean defense/shipbuilding names). Ground "
            "stock calls in the SECTOR UNIVERSE (real KR tickers); you may add others you know.\n\n"
            "SECTOR UNIVERSE:\n" + universe + "\n\n"
            "⚠ LENGTH: MAXIMUM 3 PAGES (~1100-1400 words). Tight and high-signal — no padding.\n"
            "⚠ DO NOT START WITH A TABLE — start with a short PROSE summary.\n"
            "⚠ NOT limited to any event type. RANK every market-moving factor by KR-market "
            "impact (severity 1-10) and cover the TOP 5 (the biggest only). Types can be "
            "anything: earnings, M&A, export/defense deals, policy/tariffs, Fed/rates, FX, oil, "
            "war, chip demand, halts… (war/submarine are just examples).\n"
            "For each affected stock: 방향(호재▲/악재▼) · 강도(강/중/약) · 예상 변동폭(% 추정) · "
            "신뢰도(높음/보통/낮음) + a one-sentence 근거. NEVER present % as a promise — mark 추정.\n\n"
            "Produce EXACTLY this structure:\n"
            "## 1. 핵심 요약\n"
            "   - 3-5 sentences of PROSE (NO table): the single most important read right now, "
            "overall market direction, and the highest severity present.\n"
            "## 2. 상위 이벤트 & 종목 영향 (Top 5, ranked)\n"
            "   - for each of the top ~5 events: a one-line fact, then per affected stock the "
            "방향·강도·예상밴드(추정)·신뢰도 + one-sentence 근거 (compact bullets, NOT a giant table). "
            "End EACH event with '출처: [매체](URL) · 게재: <time>'.\n"
            "## 3. 가격 분석 & 매매 전략 (Kiwoom price)\n"
            "   - for the KEY affected stocks (especially watchlist names in the price table): "
            "현재가(키움 표 기준) · 추천 매수가(좋은 진입가) · 추천 매도가(목표가) + 근거. Use the REAL "
            "current price from the table where listed; for others give a level marked '추정'. "
            "ALWAYS label buy/sell as 추정/참고 — never a guarantee.\n"
            "## 4. 체크포인트 & 출처\n"
            "   - 2-4 bullets on what to watch next, then a short source list: "
            "'- [제목](URL) · 게재시각: <time>'. EVERY source MUST show a time — use the "
            "publisher time from the news block, and if a line only has a freshness window "
            "(e.g. '최근 1시간 이내 수집') use that EXACTLY. NEVER leave the time blank, and "
            "NEVER write '미상'. Never invent a URL or a time.\n"
            "Use ONLY provided facts; mark every price/% as an estimate.\n"
            "Begin output with ONE line 'TOP_SEVERITY: <N>' (highest event severity 1-10), then "
            "the English Markdown report (Sections 1-4) and nothing else."
        )
        user = (f"DATE (KST): {kst}\n" + (f"FOCUS EVENT: {focus}\n" if focus else "")
                + (f"\nKIWOOM PRICE TABLE (current prices — use for 현재가/매수가/매도가):\n{price_ctx}\n"
                   if price_ctx else "")
                + f"\nNEWS (freshest first):\n{news}")
        out = chat_completion_sync(
            system_prompt=sysmsg, messages=[{"role": "user", "content": user[:22000]}],
            max_tokens=5200, temperature=0.5, model="groq-llama-3.3-70b", prefer_paid=True) or ""
        if out.strip() and not out.lstrip().startswith(("[LLM unavailable]", "[server error]")):
            detail_en = out.strip()
            try:
                ko_sys = (
                    "You are a professional Korean financial translator. Translate the ENTIRE "
                    "English breaking-news impact report into natural professional Korean "
                    "(존댓말). Translate EVERYTHING incl. section headings; NO English prose "
                    "except ticker codes/company tickers. Keep every number, %, and '추정/예상' "
                    "labels IDENTICAL. Preserve ALL Markdown structure and tables. Keep the "
                    "강/중/약, 호재/악재, 높음/보통/낮음 labels. Keep ALL source URLs and "
                    "published times (게재시각/dates) EXACTLY as-is. Output ONLY the Korean Markdown report.")
                ko_out = chat_completion_sync(
                    system_prompt=ko_sys, messages=[{"role": "user", "content": detail_en[:16000]}],
                    max_tokens=7000, temperature=0.3, model="groq-llama-3.3-70b", prefer_paid=True) or ""
                if ko_out.strip() and not ko_out.lstrip().startswith(("[LLM unavailable]", "[server error]")) \
                        and len(ko_out.strip()) > 400:
                    detail_ko = ko_out.strip()
            except Exception as e:
                log.warning(f"breaking: KO translation failed: {str(e)[:100]}")
    except Exception as e:
        log.warning(f"breaking: LLM compose failed: {str(e)[:120]}")

    if not detail_en:
        src = "\n".join(f"- [{h['title'][:90]}]({h['url']}) · {_freshness(h)}"
                        for h in g["headlines"][:12] if h.get("url"))
        detail_en = (f"# 🚨 Breaking Market-Impact Report\n*{kst}*\n\n## 1. 핵심 요약\n{sum_en}\n\n"
                     f"## 9. 출처\n{src or '- (no sources)'}")
    if not detail_ko:
        detail_ko = detail_en

    # Guaranteed freshness banner (always present, regardless of the LLM) —
    # proves when we scanned and that items are within the monitoring window.
    n_h = sum(1 for h in g.get("headlines", []) if h.get("window") == "h")
    banner_ko = (f"🕐 **스캔 시각(KST): {kst}** · 뉴스 신선도: 최근 1시간 이내 수집 "
                 f"(15분 주기 자동 모니터링) · 1시간 이내 기사 {n_h}건\n\n---\n\n")
    banner_en = (f"🕐 **Scan time (KST): {kst}** · Freshness: collected within the last hour "
                 f"(15-min auto-monitor) · {n_h} items from the last hour\n\n---\n\n")
    detail_ko = banner_ko + detail_ko
    detail_en = banner_en + detail_en

    # severity = the model's explicit TOP_SEVERITY line; fall back to max 'N/10'.
    sev = 6
    try:
        m = re.search(r"TOP_SEVERITY:\s*(\d{1,2})", (detail_en or "") + "\n" + (detail_ko or ""))
        if m and 1 <= int(m.group(1)) <= 10:
            sev = int(m.group(1))
        else:
            nums = [int(x) for x in re.findall(r"(\d{1,2})\s*/\s*10", (detail_en or "") + (detail_ko or ""))]
            nums = [n for n in nums if 1 <= n <= 10]
            if nums:
                sev = max(nums)
    except Exception:
        pass
    # strip the marker line so it doesn't show in the report
    detail_en = re.sub(r"(?m)^\s*TOP_SEVERITY:.*\n?", "", detail_en).strip()
    detail_ko = re.sub(r"(?m)^\s*TOP_SEVERITY:.*\n?", "", detail_ko).strip()

    return {
        "agent_type": "breaking", "name": "Breaking Market-Impact Report", "emoji": "🚨",
        "status": "ok" if g["headlines"] else "partial",
        "severity": sev,
        "summary_en": sum_en, "summary_ko": sum_ko,
        "detail_en": detail_en, "detail_ko": detail_ko,
        "table_en": "", "table_ko": "",
        "focus": focus,
        "sources": [{"title": h["title"], "url": h["url"], "date": h.get("date", "")}
                    for h in g["headlines"][:30] if h.get("url")],
        "source": "TripleH Breaking News Impact (Korean + international outlets)",
    }


def triage_events(seen_keys: set | None = None, min_sev: int = 5, max_events: int = 5) -> list[dict]:
    """CHEAP detector for the 15-min monitor: a light news scan + one short LLM
    triage call → NEW market-moving events (severity ≥ min_sev) for the KR market,
    excluding anything matching `seen_keys`. Returns [{title, severity, theme, key}]."""
    seen_keys = set(seen_keys or [])
    heads: list[dict] = []
    seen_titles: set[str] = set()
    for q in _TRIAGE_QUERIES:
        for h in _search(q, n=6, recency="h"):
            k = (h.get("title") or "")[:90].lower()
            if k and k not in seen_titles:
                seen_titles.add(k)
                heads.append(h)
    if not heads:
        return []
    block = "\n".join(f"- {h['title'][:170]} — {h.get('snippet', '')[:170]}" for h in heads[:30])
    try:
        from services.llm_client import chat_completion_sync
        sysmsg = (
            "You triage breaking financial news for KOREAN-stock-market impact. From the "
            "headlines, list ONLY NEW, genuinely market-moving events (severity ≥ 7 of 10) "
            "that would move Korean stocks (any type: earnings, M&A, foreign/defense deals, "
            "policy/tariffs, Fed/rates, FX, oil, geopolitics, chip demand, halts…). For each: "
            "a short title, severity 1-10, theme, and a short stable 'key' (lowercase slug of "
            "the core event). Output STRICT JSON array only: "
            '[{"title":"","severity":7,"theme":"","key":""}]. Use [] if nothing major/new.')
        user = f"ALREADY ALERTED (exclude these keys/topics): {sorted(seen_keys)[:40]}\n\nHEADLINES:\n{block}"
        out = chat_completion_sync(
            system_prompt=sysmsg, messages=[{"role": "user", "content": user[:9000]}],
            max_tokens=700, temperature=0.2, model="groq-llama-3.3-70b", prefer_paid=True) or ""
    except Exception as e:
        log.warning(f"breaking triage LLM failed: {str(e)[:90]}")
        return []
    m = re.search(r"\[.*\]", out, re.S)
    if not m:
        return []
    try:
        import json as _json
        events = _json.loads(m.group(0))
    except Exception:
        return []
    res = []
    for e in events if isinstance(events, list) else []:
        try:
            sev = int(e.get("severity", 0))
        except Exception:
            sev = 0
        key = (str(e.get("key") or e.get("title", ""))[:50]).lower().strip()
        if sev >= min_sev and key and key not in seen_keys:
            res.append({"title": str(e.get("title", ""))[:160], "severity": sev,
                        "theme": str(e.get("theme", "")), "key": key})
    return sorted(res, key=lambda x: -x["severity"])[:max_events]
