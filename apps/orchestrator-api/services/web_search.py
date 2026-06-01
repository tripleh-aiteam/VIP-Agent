"""
web_search — a single live web-search helper used by the assistant's
`web_search` tool and by the self-improvement loop (to research questions the
agent couldn't answer from its knowledge base).

Provider precedence (first configured wins):
  1. Serper.dev      — env SERPER_API_KEY        (simplest, generous free tier)
  2. Google PSE      — env GOOGLE_CSE_KEY + GOOGLE_CSE_CX (Programmable Search)
  3. Tavily          — env TAVILY_API_KEY        (LLM-oriented results)

If none are configured the helper returns ok:false with a clear message, so
the caller can fall back to LLM knowledge rather than crashing. Never raises.
"""

from __future__ import annotations

import os
from typing import Any

from services.logger import log


def _serper(query: str, n: int) -> list[dict[str, Any]]:
    import httpx
    key = os.environ.get("SERPER_API_KEY")
    if not key:
        return []
    with httpx.Client(timeout=12.0) as c:
        r = c.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
            json={"q": query, "num": n},
        )
        r.raise_for_status()
        data = r.json()
    out = []
    for item in (data.get("organic") or [])[:n]:
        out.append({
            "title": item.get("title", ""),
            "url": item.get("link", ""),
            "snippet": item.get("snippet", ""),
        })
    # Answer box / knowledge graph give a quick direct answer when present.
    if data.get("answerBox"):
        ab = data["answerBox"]
        out.insert(0, {
            "title": ab.get("title", "Answer"),
            "url": ab.get("link", ""),
            "snippet": ab.get("answer") or ab.get("snippet") or "",
        })
    return out


def _google_pse(query: str, n: int) -> list[dict[str, Any]]:
    import httpx
    key = os.environ.get("GOOGLE_CSE_KEY")
    cx = os.environ.get("GOOGLE_CSE_CX")
    if not (key and cx):
        return []
    with httpx.Client(timeout=12.0) as c:
        r = c.get(
            "https://www.googleapis.com/customsearch/v1",
            params={"key": key, "cx": cx, "q": query, "num": min(n, 10)},
        )
        r.raise_for_status()
        data = r.json()
    return [
        {"title": it.get("title", ""), "url": it.get("link", ""), "snippet": it.get("snippet", "")}
        for it in (data.get("items") or [])[:n]
    ]


def _tavily(query: str, n: int) -> list[dict[str, Any]]:
    import httpx
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        return []
    with httpx.Client(timeout=15.0) as c:
        r = c.post(
            "https://api.tavily.com/search",
            json={"api_key": key, "query": query, "max_results": n},
        )
        r.raise_for_status()
        data = r.json()
    return [
        {"title": it.get("title", ""), "url": it.get("url", ""), "snippet": it.get("content", "")}
        for it in (data.get("results") or [])[:n]
    ]


def search_web(query: str, num_results: int = 5) -> dict[str, Any]:
    """Run a live web search. Returns {ok, provider, results:[{title,url,snippet}]}.
    Never raises — returns ok:false with a reason if no provider is configured
    or the call fails."""
    query = (query or "").strip()
    if not query:
        return {"ok": False, "error": "empty query", "results": []}
    n = max(1, min(num_results, 10))
    for name, fn in (("serper", _serper), ("google_pse", _google_pse), ("tavily", _tavily)):
        try:
            hits = fn(query, n)
        except Exception as e:
            log.warning(f"web_search {name} failed: {str(e)[:120]}")
            continue
        if hits:
            return {"ok": True, "provider": name, "results": hits}
    return {
        "ok": False,
        "error": (
            "No web-search provider configured. Set SERPER_API_KEY (or "
            "GOOGLE_CSE_KEY+GOOGLE_CSE_CX, or TAVILY_API_KEY) on the orchestrator "
            "to enable live web search."
        ),
        "results": [],
    }
