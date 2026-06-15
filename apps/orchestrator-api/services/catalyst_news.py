"""
catalyst_news — shared catalyst / event research for the daily reports, built
for 일정매매 (schedule-based / event-driven trading): surface upcoming scheduled
events AND what influential people are saying/doing, so the user can position
EARLY — before the crowd.

Pulls live results via services.web_search (Serper etc.) for:
  - upcoming earnings / product launches / conferences (GTC, CES)
  - macro schedule (FOMC, CPI, rate decisions)
  - policy & politics (Trump tariffs, chip policy, export rules)
  - influential figures' statements & actions (Jensen Huang/Nvidia visits &
    partnerships, Fed, Samsung/SK executives)
  - corporate events (ADR/US listing, M&A, index rebalance, buybacks)
Used by both the newspaper and youtube reports.
"""

from __future__ import annotations

from services.logger import log

# Catalyst-hunting queries (global, watchlist-aware). Each is 1 search credit.
CATALYST_QUERIES = [
    "Samsung SK Hynix Micron Nvidia AMD Broadcom next earnings date upcoming 2026",
    "Nvidia GTC CES semiconductor AI chip upcoming product launch event date 2026",
    "FOMC meeting CPI jobs report economic calendar upcoming next week 2026",
    "Trump tariff semiconductor chips Korea export policy upcoming decision 2026",
    "Jensen Huang Nvidia Korea Samsung SK Hynix Naver partnership announcement latest",
    "SK Hynix ADR US listing schedule MSCI index rebalance upcoming date 2026",
    "한국 증시 반도체 실적 발표 예정 일정 향후 이벤트 삼성전자 SK하이닉스 네이버 2026",
    "upcoming stock market catalysts this week semiconductor AI events calendar",
]


def gather_catalysts(per_query: int = 6, cap: int = 30) -> list[dict]:
    """Run the catalyst queries and return deduped {title,url,snippet}. Empty if
    no search provider is configured."""
    try:
        from services.web_search import search_web
    except Exception as e:
        log.warning(f"catalyst: web_search import failed: {e}")
        return []
    seen: set[str] = set()
    out: list[dict] = []
    for q in CATALYST_QUERIES:
        if len(out) >= cap:
            break
        try:
            res = search_web(q, num_results=per_query)
        except Exception as e:
            log.warning(f"catalyst: search failed: {str(e)[:100]}")
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
            out.append({"title": title, "url": h.get("url", ""), "snippet": snippet})
            if len(out) >= cap:
                break
    return out


def catalyst_block(items: list[dict]) -> str:
    """Text block of catalyst headlines for the LLM."""
    if not items:
        return "(no live catalyst data — infer near-term catalysts from the news above + general knowledge, mark dates as approx/expected)"
    return "\n".join(
        (f"- {n['title'][:150]} — {n['snippet'][:240]}" if n.get("snippet") else f"- {n['title'][:150]}")
        for n in items
    )


# Shared LLM instruction for the Catalysts & Schedule section — used by both
# the newspaper and youtube reports so the wording stays consistent.
CATALYST_SECTION_RULE = (
    "This section is the CORE of 일정매매 (schedule-based / event-driven trading): "
    "identify catalysts EARLY so the reader can position BEFORE the crowd. "
    "CRITICAL — FUTURE ONLY: include ONLY events dated AFTER today's date (given at "
    "the top of the prompt). NEVER list an event with a date earlier than or equal "
    "to today — discard all past events. If you don't know the exact future date, "
    "write 'upcoming (date TBC)' or a quarter (e.g. 'late June', 'Q3 2026') — NEVER "
    "invent a specific past date. Every row's timing must be in the future.\n"
    "Provide THREE parts. ALL output text, table headers and labels MUST be in "
    "KOREAN (no English words anywhere):\n"
    "(a) a '### 다가오는 촉매 일정' table — Markdown with EXACTLY these Korean column "
    "headers: | 시점 | 촉매/이벤트 | 해당 종목 | 예상 영향 | 선제 포지션 | — listing real "
    "scheduled or expected events (earnings, product launches, GTC/CES, FOMC/CPI/"
    "rate decisions, policy/tariff rulings, executive visits, ADR/US listings, M&A, "
    "index rebalances). Korean dates/timing; mark uncertain ones '예상'.\n"
    "(b) '### 영향력 있는 발언·행동' — Korean bullets on what key decision-makers SAID "
    "or DID that moves these stocks (트럼프 관세/반도체 정책 발언; 젠슨 황/엔비디아 방문·"
    "파트너십·투자; 연준 신호; 삼성/SK 임원) and the likely price effect of each.\n"
    "(c) '### 선제 포지셔닝 아이디어' — for the 2-4 nearest catalysts, a concrete Korean "
    "'<날짜/이벤트> 전 매수 → 대중 관심 도달 시 매도' note tied to the affected stock. "
    "Use ONLY real events from the data; never invent a date. Write everything in Korean."
)
