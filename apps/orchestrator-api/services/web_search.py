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


def _gemini_list_models(key: str) -> tuple[list[str], str | None]:
    """Ask the key which models it can actually use for generateContent. Returns
    (model_names, error). Different keys expose different models, so we discover
    rather than hardcode."""
    import httpx
    try:
        with httpx.Client(timeout=12.0) as c:
            r = c.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}")
        if r.status_code >= 400:
            return [], f"ListModels HTTP {r.status_code} {r.text[:120]}"
        names = []
        for m in (r.json().get("models") or []):
            if "generateContent" in (m.get("supportedGenerationMethods") or []):
                names.append((m.get("name") or "").replace("models/", ""))
        return names, None
    except Exception as e:
        return [], f"ListModels {str(e)[:120]}"


def gemini_search_models(key: str | None = None) -> list[str]:
    """Public helper for diagnostics — the discovered generateContent models."""
    key = key or _gemini_key()
    if not key:
        return []
    return _gemini_list_models(key)[0]


def _gemini_grounded(query: str, n: int) -> list[dict[str, Any]]:
    """Use Gemini's built-in Google Search grounding. Reuses the Gemini API key
    already configured for the chatbot — no extra signup. Returns the grounding
    web sources (title/url) plus a synthesized snippet as the first result.

    Tries several model + grounding-tool variants because the right combo differs
    by API version: newer models want `google_search`, older (1.5) want
    `google_search_retrieval`. Raises an informative error if all variants fail
    so the caller can surface WHY (e.g. key not enabled for the API)."""
    import httpx
    key = _gemini_key()
    if not key:
        return []
    forced = os.environ.get("GEMINI_SEARCH_MODEL")
    if forced:
        models = [forced]
    else:
        discovered, derr = _gemini_list_models(key)
        if derr and not discovered:
            raise RuntimeError(derr)  # surface "API not enabled / key invalid"
        # Prefer flash models (cheap + support search grounding), newest first.
        flash = [m for m in discovered if "flash" in m and "lite" not in m and "8b" not in m]
        flash.sort(reverse=True)  # 2.5 > 2.0 > 1.5 lexically
        models = (flash + [m for m in discovered if "flash" in m]
                  + ["gemini-2.0-flash"])[:4]
    last_err = None
    with httpx.Client(timeout=20.0) as c:
        for model in models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            # Newer models: google_search; legacy 1.5: google_search_retrieval.
            tools = [{"google_search": {}}] if not model.startswith("gemini-1.5") \
                else [{"google_search_retrieval": {}}]
            try:
                r = c.post(url, json={
                    "contents": [{"role": "user", "parts": [{"text": query}]}],
                    "tools": tools,
                }, headers={"Content-Type": "application/json"})
                if r.status_code >= 400:
                    last_err = f"{model}: HTTP {r.status_code} {r.text[:140]}"
                    continue
                data = r.json()
            except Exception as e:
                last_err = f"{model}: {str(e)[:140]}"
                continue

            cand = (data.get("candidates") or [{}])[0]
            answer = "".join(p.get("text", "") for p in (cand.get("content", {}).get("parts") or []))
            out: list[dict[str, Any]] = []
            if answer.strip():
                out.append({"title": "Answer (Gemini + Google Search)", "url": "",
                            "snippet": answer.strip()[:1200]})
            gm = cand.get("groundingMetadata") or {}
            for chunk in (gm.get("groundingChunks") or [])[:n]:
                web = chunk.get("web") or {}
                if web.get("uri"):
                    out.append({"title": web.get("title", ""), "url": web.get("uri", ""), "snippet": ""})
            if out:
                return out
            last_err = f"{model}: empty grounding response"
    if last_err:
        raise RuntimeError(last_err)
    return []


def search_web(query: str, num_results: int = 5) -> dict[str, Any]:
    """Run a live web search. Returns {ok, provider, results:[{title,url,snippet}]}.
    Never raises — returns ok:false with a reason if no provider is configured
    or the call fails."""
    query = (query or "").strip()
    if not query:
        return {"ok": False, "error": "empty query", "results": []}
    n = max(1, min(num_results, 10))
    errors: list[str] = []
    for name, fn in (
        ("serper", _serper),
        ("google_pse", _google_pse),
        ("tavily", _tavily),
        ("gemini_grounded", _gemini_grounded),
    ):
        try:
            hits = fn(query, n)
        except Exception as e:
            msg = f"{name}: {str(e)[:160]}"
            errors.append(msg)
            log.warning(f"web_search {msg}")
            continue
        if hits:
            return {"ok": True, "provider": name, "results": hits}
    return {
        "ok": False,
        "error": ("; ".join(errors) if errors else
                  "No web-search provider available. Set SERPER_API_KEY (or "
                  "GOOGLE_CSE_KEY+GOOGLE_CSE_CX, or TAVILY_API_KEY, or GEMINI_API_KEY "
                  "for Google-Search grounding) on the orchestrator."),
        "results": [],
    }
