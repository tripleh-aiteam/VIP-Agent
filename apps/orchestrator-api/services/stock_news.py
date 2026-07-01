"""stock_news.py — per-stock recent Korean news + an LLM summary (Report → News panel).

Reads the shared `raw_news` table (populated by the daily per-stock news collector + the
newspaper collector — Naver + Korean outlets), tags each headline with a type/direction
via news_impact.classify, and returns a short Korean summary of the batch. Powers the
Report-menu dropdown: pick a stock → recent news + summary.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

from services.logger import log


def universe() -> list[dict[str, str]]:
    """Stocks available in the dropdown (tracked names + Wave-extra), sorted by name."""
    out: dict[str, str] = {}
    try:
        from services.prediction_service import NAMES
        out.update({c: n for c, n in NAMES.items() if c != "069500"})
    except Exception:
        pass
    try:
        from routers.predictions import WAVE_EXTRA_NAMES
        out.update(WAVE_EXTRA_NAMES)
    except Exception:
        pass
    return sorted(({"code": c, "name": n} for c, n in out.items()), key=lambda x: x["name"])


def _summary(name: str, items: list[dict]) -> str:
    if not items:
        return ""
    heads = "\n".join(f"- {it['title']}" + (f" — {it['snippet'][:110]}" if it.get("snippet") else "")
                      for it in items[:12])
    try:
        from services.llm_client import chat_completion_sync
        out = chat_completion_sync(
            system_prompt=(f"아래는 '{name}' 관련 최근 뉴스 제목 목록입니다. 핵심 내용을 "
                           f"3~4문장으로 한국어로 요약하고, 마지막 줄에 전반적 논조를 "
                           f"'논조: 호재/악재/중립' 형식으로 한 줄 덧붙이세요. 과장 없이 사실 위주로, "
                           f"제목에 없는 내용은 지어내지 마세요."),
            messages=[{"role": "user", "content": heads}],
            max_tokens=450, temperature=0.3, model="groq-llama-3.3-70b",
        )
        return (out or "").strip()
    except Exception as e:
        log.warning(f"stock_news summary failed: {str(e)[:120]}")
        return ""


def _domain(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return (urlparse(url).netloc or "").replace("www.", "")
    except Exception:
        return ""


def stock_news(db, ticker: str, days: int = 7, limit: int = 15) -> dict[str, Any]:
    """Recent Korean news for one stock + a Korean summary. Fetches LIVE via web search
    (real headlines/snippets/links) because the stored raw_news has domain-only titles.
    {ticker, name, count, items[], summary_ko, provider}."""
    from services.web_search import search_web
    from services.news_impact import classify
    from services.stock_resolver import display_name
    tk = str(ticker).zfill(6)
    name = display_name(tk)
    recency = "d" if days <= 1 else "w" if days <= 8 else None
    # Korean news query — Serper Google-News returns Korean outlets for a Korean name.
    res = search_web(f"{name} 주가 뉴스", num_results=max(limit, 10), recency=recency)
    hits = res.get("results") or []
    seen: set[str] = set()
    items = []
    for h in hits:
        title = (h.get("title") or "").strip()
        if not title or title in seen:
            continue
        seen.add(title)
        snip = (h.get("snippet") or "").strip()
        ntype, _imp, ddir = classify(title, snip)
        direction = "▲" if ddir > 0 else "▼" if ddir < 0 else "•"
        items.append({"title": title, "snippet": snip[:220], "url": h.get("url"),
                      "source": _domain(h.get("url") or ""), "type": ntype, "direction": direction})
        if len(items) >= limit:
            break
    return {"ticker": tk, "name": name, "count": len(items), "items": items,
            "summary_ko": _summary(name, items), "provider": res.get("provider"),
            "configured": bool(res.get("ok")), "days": days}
