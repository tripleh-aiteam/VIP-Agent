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
                     "date": (h.get("date") or "").strip()}
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
    for q in queries[:10]:
        for h in _search(q):
            key = (h.get("title") or h.get("url") or "")[:90].lower()
            if not key or key in seen:
                continue
            seen.add(key)
            headlines.append(h)
        if len(headlines) >= 40:
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
                             "text": body, "date": (hm or {}).get("date", "")})
        if len(articles) >= 8:
            break

    return {"headlines": headlines, "articles": articles, "focus": focus}


def _news_block(g: dict) -> str:
    parts = []
    if g.get("articles"):
        parts.append("=== FULL ARTICLES (substance) — cite URL + published time ===")
        for a in g["articles"]:
            when = f" · 게재: {a['date']}" if a.get("date") else ""
            parts.append(f"[{a['title'][:140]}] (URL: {a['url']}{when})\n{a['text'][:2600]}")
    parts.append("\n=== HEADLINES (title · published time · URL · snippet) ===")
    for h in g.get("headlines", [])[:30]:
        when = f" · {h['date']}" if h.get("date") else ""
        s = f"- {h['title'][:150]}{when} · {h.get('url', '')}"
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

    sum_en = (f"Breaking market-impact scan ({len(g['headlines'])} headlines, "
              f"{len(g['articles'])} full articles)" + (f" — focus: {focus}" if focus else "") + ".")
    sum_ko = (f"속보 시장영향 스캔 (헤드라인 {len(g['headlines'])}건, 본문 {len(g['articles'])}건)"
              + (f" — 초점: {focus}" if focus else "") + ".")

    detail_en = detail_ko = ""
    try:
        from services.llm_client import chat_completion_sync
        sysmsg = (
            "You are TripleH's senior market strategist writing a DETAILED, event-driven "
            "BREAKING-NEWS IMPACT report for the boss. From the NEWS provided (Korean AND "
            "international outlets), identify the market-moving EVENTS and analyse how each "
            "affects KOREAN stocks. Foreign news matters (e.g. a Canadian submarine/ship "
            "order lifts Korean defense & shipbuilding names). Ground every stock call in "
            "the SECTOR UNIVERSE below (real KR tickers) — you may add other real KR names "
            "you know.\n\nSECTOR UNIVERSE:\n" + universe + "\n\n"
            "⚠ NOT limited to any fixed event type. RANK EVERY market-moving factor in the "
            "news by its impact on the KOREAN market (severity 1-10) and cover the TOP ~10 "
            "(fewer only if there truly aren't that many). They can be ANYTHING moving "
            "prices: earnings surprises, M&A, foreign export/defense deals, policy & "
            "tariffs, Fed/rates, FX(원/달러), oil, geopolitics/war, chip demand(HBM/엔비디아), "
            "trading halts, index rebalances, regulation, etc. (war/submarine deals are just "
            "examples, NOT the scope). Order events strongest-impact first.\n\n"
            "For EACH affected stock give: 방향(호재 ▲ / 악재 ▼), 강도(강/중/약), "
            "예상 변동폭(% 밴드 — 반드시 '추정치'로 표기, 확정 아님), 신뢰도(높음/보통/낮음), "
            "그리고 한두 문장의 인과 사슬(왜 오르고/내리는지). NEVER present the % as a promise — "
            "always label it 추정/예상.\n\n"
            "Produce this EXACT structure (substantial, ~2-3 pages):\n"
            "## 1. 핵심 요약 (Executive Summary)\n"
            "   - a RANKED top-events table | 순위 | 이벤트 | 심각도(1-10) | 핵심 영향 종목 | 방향 | — "
            "the most market-moving events first (aim for ~10), then a 2-3 sentence overall read.\n"
            "## 2. 이벤트별 영향 분석 (Event-by-event, ranked)\n"
            "   - for EACH ranked event (top ~10): 무슨 일인지(사실), 출처 국가/매체, 영향받는 종목 표:\n"
            "     | 종목 | 방향 | 강도 | 예상 변동폭(추정) | 신뢰도 | 근거(인과) |\n"
            "   - then 2-3 sentences of deeper reasoning per event.\n"
            "## 3. 호재 종목 (Bullish ▲, ranked by impact)\n"
            "   - a ranked list/table of the BUY-side names with 강도·예상밴드·이유.\n"
            "## 4. 악재 종목 (Bearish ▼, ranked)\n"
            "   - the SELL/avoid-side names (write '해당 없음' if none).\n"
            "## 5. 테마별 분류 (By theme)\n"
            "   - group impacts by 방산/조선/반도체/2차전지/매크로 etc.\n"
            "## 6. 해외 뉴스 → 한국 증시 (Foreign news read-through)\n"
            "   - explicitly connect the INTERNATIONAL articles to Korean tickers.\n"
            "## 7. 우리 보유·관심 종목 영향 (Our watchlist)\n"
            f"   - impact on: {OUR_WATCHLIST}. If none material, say so honestly.\n"
            "## 8. 체크포인트 & 일정 (What to watch next)\n"
            "   - upcoming catalysts/decisions tied to these events.\n"
            "## 9. 출처 (Sources)\n"
            "   - a list of the source articles used, EACH as a clickable Markdown link "
            "WITH its published time: '- [제목](URL) · 게재시각: <date>'. Use the exact URL "
            "and the published time given in the news block (write '시각 미상' only if truly "
            "absent). NEVER invent a URL or a time.\n"
            "ALSO: in Section 2 (event-by-event), end each event with its source(s) as "
            "'출처: [매체/제목](URL) · 게재: <date>' so every event is traceable.\n"
            "Use ONLY the provided news for facts; do not fabricate a deal that isn't there. "
            "Be decisive but mark every number as an estimate.\n"
            "Begin your output with ONE line exactly: 'TOP_SEVERITY: <N>' where <N> is the "
            "highest single event severity (1-10) in this report. Then output the finished "
            "English Markdown report (Sections 1-9) and nothing else."
        )
        user = (f"DATE (KST): {kst}\n" + (f"FOCUS EVENT: {focus}\n" if focus else "") + "\n"
                f"NEWS:\n{news}")
        out = chat_completion_sync(
            system_prompt=sysmsg, messages=[{"role": "user", "content": user[:24000]}],
            max_tokens=9000, temperature=0.5, model="groq-llama-3.3-70b", prefer_paid=True) or ""
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
                    system_prompt=ko_sys, messages=[{"role": "user", "content": detail_en[:24000]}],
                    max_tokens=13000, temperature=0.3, model="groq-llama-3.3-70b", prefer_paid=True) or ""
                if ko_out.strip() and not ko_out.lstrip().startswith(("[LLM unavailable]", "[server error]")) \
                        and len(ko_out.strip()) > 400:
                    detail_ko = ko_out.strip()
            except Exception as e:
                log.warning(f"breaking: KO translation failed: {str(e)[:100]}")
    except Exception as e:
        log.warning(f"breaking: LLM compose failed: {str(e)[:120]}")

    if not detail_en:
        src = "\n".join(f"- [{h['title'][:90]}]({h['url']})" + (f" · 게재: {h['date']}" if h.get("date") else "")
                        for h in g["headlines"][:12] if h.get("url"))
        detail_en = (f"# 🚨 Breaking Market-Impact Report\n*{kst}*\n\n## 1. 핵심 요약\n{sum_en}\n\n"
                     f"## 9. 출처\n{src or '- (no sources)'}")
    if not detail_ko:
        detail_ko = detail_en

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


def triage_events(seen_keys: set | None = None, min_sev: int = 7, max_events: int = 5) -> list[dict]:
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
