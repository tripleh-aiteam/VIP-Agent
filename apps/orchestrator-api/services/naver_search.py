"""naver_search — search NAVER (네이버) for the assistant's `naver_search` tool.

Two jobs:
  • General Naver lookup (web / news / blog / local).
  • Real-estate check: is one of OUR properties (land/house/building) advertised on
    NAVER 부동산? Pass the address (e.g. '낙하리 301-7') with realestate=True.

Provider precedence (first that returns results wins):
  1. Official Naver Open Search API — env NAVER_CLIENT_ID + NAVER_CLIENT_SECRET
     (developers.naver.com). Most authoritative; covers web/news/blog/local.
  2. Web search scoped to naver.com / 네이버 부동산 — reuses the existing web_search
     providers (Serper / Google PSE / Tavily / Gemini grounding), so it works even
     without Naver API keys.

Never raises — returns {ok, provider, results:[{title,url,snippet}], ...}.
"""

from __future__ import annotations

import os
import re
from typing import Any

from services.logger import log

_TAG_RE = re.compile(r"<[^>]+>")
# Naver Open Search API path per search type.
_TYPE_MAP = {
    "web": "webkr", "webkr": "webkr",
    "news": "news",
    "blog": "blog",
    "local": "local",
    "cafe": "cafearticle", "cafearticle": "cafearticle",
    "shop": "shop", "shopping": "shop",
}


def _strip(s: str) -> str:
    return _TAG_RE.sub("", s or "").replace("&quot;", '"').replace("&amp;", "&").strip()


def _naver_api(query: str, kind: str, n: int, cid: str, csec: str) -> dict[str, Any]:
    import httpx
    typ = _TYPE_MAP.get((kind or "web").lower(), "webkr")
    try:
        with httpx.Client(timeout=12.0) as c:
            r = c.get(
                f"https://openapi.naver.com/v1/search/{typ}.json",
                params={"query": query, "display": min(max(n, 1), 10), "sort": "sim"},
                headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": csec},
            )
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return {"ok": False, "error": f"naver_api: {str(e)[:160]}", "results": []}
    out: list[dict[str, Any]] = []
    for it in (data.get("items") or [])[:n]:
        out.append({
            "title": _strip(it.get("title", "")),
            "url": it.get("link") or it.get("originallink") or "",
            "snippet": _strip(it.get("description") or it.get("roadAddress") or it.get("address") or ""),
        })
    return {"ok": bool(out), "provider": f"naver_api:{typ}", "results": out}


def naver_search(query: str, *, kind: str = "web", num_results: int = 5,
                 realestate: bool = False) -> dict[str, Any]:
    """Search NAVER. `kind`: web|news|blog|local. `realestate=True` checks NAVER 부동산
    listings for the given property/address. Returns {ok, provider, results, query}."""
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "empty query", "results": []}
    n = max(1, min(int(num_results or 5), 10))

    cid = os.environ.get("NAVER_CLIENT_ID")
    csec = os.environ.get("NAVER_CLIENT_SECRET")

    # IMPORTANT: 부동산 매물 listings live on land.naver.com, which ONLY Serper (Google
    # scoped to that domain) can return. Naver's official Open API searches web/news/
    # blog — NOT 부동산 listings — so for real-estate it must NOT be used to decide
    # "is this property listed?" (it would return news articles and we'd falsely say
    # "not listed"). Provider order therefore differs by intent:
    #   • real-estate  → Serper first (real listings); Naver API/web only as fallback
    #   • general      → Naver API first (free, great for web/news/blog)

    # 1) Real-estate: Serper scoped to land.naver.com FIRST.
    if realestate:
        # Only 2 variants (was 5) to conserve Serper credits — each property check
        # already fans out over several addresses, so 5×N calls drained the quota.
        # `site:land.naver.com` already matches the m.land / new.land subdomains in
        # Google, so one scoped query covers them; one broader query is the fallback.
        for scoped in (f"{q} 매물 site:land.naver.com", f"{q} 네이버 부동산 매물"):
            hits = _serper(scoped, n)
            if hits:
                return {"ok": True, "provider": "serper:naver", "results": hits,
                        "query": scoped, "realestate": realestate}

    # 2) Official Naver API — authoritative for GENERAL search (web/news/blog/local).
    if cid and csec:
        api_q = f"{q} 매물 네이버부동산" if realestate else q
        res = _naver_api(api_q, kind, n, cid, csec)
        if res.get("ok"):
            res["query"] = api_q
            res["realestate"] = realestate
            return res

    # 3) Serper for general (non-real-estate) queries.
    if not realestate:
        for scoped in (f"{q} site:naver.com", f"{q} 네이버"):
            hits = _serper(scoped, n)
            if hits:
                return {"ok": True, "provider": "serper:naver", "results": hits,
                        "query": scoped, "realestate": realestate}

    # 3) Last resort: the generic web-search chain (may include other providers).
    from services.web_search import search_web
    scoped = (f"{q} 네이버 부동산 매물" if realestate else f"{q} site:naver.com")
    res = search_web(scoped, num_results=n)
    res["provider"] = f"naver(web:{res.get('provider')})"
    res["query"] = scoped
    res["realestate"] = realestate
    return res


def _serper(query: str, n: int) -> list[dict[str, Any]]:
    """Direct Serper (Google) call, biased to Korea (gl=kr, hl=ko). [] if no key /
    no results. Returns real source links — no grounding-redirect URLs."""
    import httpx
    key = os.environ.get("SERPER_API_KEY")
    if not key:
        return []
    try:
        with httpx.Client(timeout=12.0) as c:
            r = c.post("https://google.serper.dev/search",
                       headers={"X-API-KEY": key, "Content-Type": "application/json"},
                       json={"q": query, "num": n, "gl": "kr", "hl": "ko"})
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        log.warning("naver_search serper failed: %s", str(e)[:120])
        return []
    out: list[dict[str, Any]] = []
    for it in (data.get("organic") or [])[:n]:
        out.append({"title": it.get("title", ""), "url": it.get("link", ""),
                    "snippet": it.get("snippet", "")})
    return out


__all__ = ["naver_search"]
