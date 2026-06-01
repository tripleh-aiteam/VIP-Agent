"""
web_search — a single live web-search helper used by the assistant's
`web_search` tool and by the self-improvement loop (to research questions the
agent couldn't answer from its knowledge base).

Provider precedence (first configured wins):
  1. Serper.dev      — env SERPER_API_KEY        (simplest, generous free tier)
  2. Google PSE      — env GOOGLE_CSE_KEY + GOOGLE_CSE_CX (Programmable Search)
  3. Tavily          — env TAVILY_API_KEY        (LLM-oriented results)
  4. Gemini grounding— env GEMINI_API_KEY / GOOGLE_API_KEY / GOOGLE_GENERATIVE_AI_API_KEY
                       (reuses the key already configured for Gemini models —
                       uses Google Search "grounding" so NO new key is needed)

If none are configured the helper returns ok:false with a clear message, so
the caller can fall back to LLM knowledge rather than crashing. Never raises.
"""

from __future__ import annotations

import os
from typing import Any

from services.logger import log


def _gemini_key() -> str | None:
    return (
        os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
        or os.environ.get("GOOGLE_GENERATIVE_AI_API_KEY")
    )


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


def _gemini_grounded(query: str, n: int) -> list[dict[str, Any]]:
    """Use Gemini's built-in Google Search grounding. Reuses the Gemini API key
    already configured for the chatbot — no extra signup. Returns the grounding
    web sources (title/url) plus a synthesized snippet as the first result."""
    import httpx
    key = _gemini_key()
    if not key:
        return []
    # gemini-2.0-flash supports the google_search grounding tool.
    model = os.environ.get("GEMINI_SEARCH_MODEL", "gemini-2.0-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": query}]}],
        "tools": [{"google_search": {}}],
    }
    with httpx.Client(timeout=20.0) as c:
        r = c.post(url, json=payload, headers={"Content-Type": "application/json"})
        r.raise_for_status()
        data = r.json()

    cand = (data.get("candidates") or [{}])[0]
    # Synthesized answer text
    answer = ""
    for part in (cand.get("content", {}).get("parts") or []):
        if part.get("text"):
            answer += part["text"]
    out: list[dict[str, Any]] = []
    if answer.strip():
        out.append({"title": "Answer (Gemini + Google Search)", "url": "", "snippet": answer.strip()[:1200]})
    # Grounding source links
    gm = cand.get("groundingMetadata") or {}
    for chunk in (gm.get("groundingChunks") or [])[:n]:
        web = chunk.get("web") or {}
        if web.get("uri"):
            out.append({"title": web.get("title", ""), "url": web.get("uri", ""), "snippet": ""})
    return out


def search_web(query: str, num_results: int = 5) -> dict[str, Any]:
    """Run a live web search. Returns {ok, provider, results:[{title,url,snippet}]}.
    Never raises — returns ok:false with a reason if no provider is configured
    or the call fails."""
    query = (query or "").strip()
    if not query:
        return {"ok": False, "error": "empty query", "results": []}
    n = max(1, min(num_results, 10))
    for name, fn in (
        ("serper", _serper),
        ("google_pse", _google_pse),
        ("tavily", _tavily),
        ("gemini_grounded", _gemini_grounded),
    ):
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
            "No web-search provider available. Set SERPER_API_KEY (or "
            "GOOGLE_CSE_KEY+GOOGLE_CSE_CX, or TAVILY_API_KEY, or GEMINI_API_KEY "
            "for Google-Search grounding) on the orchestrator."
        ),
        "results": [],
    }
