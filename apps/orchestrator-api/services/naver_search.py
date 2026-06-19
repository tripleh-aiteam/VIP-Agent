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

    # 1) Official Naver API. For real-estate we bias the query toward 매물 listings.
    if cid and csec:
        api_q = f"{q} 매물 네이버부동산" if realestate else q
        res = _naver_api(api_q, kind, n, cid, csec)
        if res.get("ok"):
            res["query"] = api_q
            res["realestate"] = realestate
            return res

    # 2) Fallback: web search scoped to Naver (no Naver key needed).
    from services.web_search import search_web
    if realestate:
        scoped = f'"{q}" 매물 (site:land.naver.com OR site:new.land.naver.com OR 네이버부동산)'
    else:
        scoped = f"{q} site:naver.com"
    res = search_web(scoped, num_results=n)
    res["provider"] = f"naver(web-scoped:{res.get('provider')})"
    res["query"] = scoped
    res["realestate"] = realestate
    if not res.get("ok"):
        log.info("naver_search fallback empty for %r", scoped[:80])
    return res


__all__ = ["naver_search"]
