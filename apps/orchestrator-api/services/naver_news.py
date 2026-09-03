# -*- coding: utf-8 -*-
"""naver_news — the official Naver News Search API (boss 2026-09-03: his own
'tripleh' app keys from developers.naver.com, tested live before wiring).

A SECOND news source beside the AI news intern: the API answers in seconds
with articles minutes old, so the market-move note and any caller that needs
"what is happening RIGHT NOW" reads from here. Keys are read at call time
(repo convention — .env edits apply on restart without code changes).
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request

_CACHE: dict = {}
_TTL = 300.0                     # 5 min per query — polite to the quota (25k/day)


def _env(k: str) -> str:
    return (os.environ.get(k) or "").strip()


def search_news(query: str, display: int = 5) -> list[dict]:
    """Freshest articles for a query: [{'title','link','pub','desc'}...] or []."""
    key = (query, display)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    cid, csec = _env("NAVER_CLIENT_ID"), _env("NAVER_CLIENT_SECRET")
    if not cid or not csec:
        return []
    try:
        url = (f"https://openapi.naver.com/v1/search/news.json?"
               f"query={urllib.parse.quote(query)}&display={int(display)}&sort=date")
        req = urllib.request.Request(url, headers={
            "X-Naver-Client-Id": cid, "X-Naver-Client-Secret": csec})
        r = json.load(urllib.request.urlopen(req, timeout=10))
        out = []
        for it in r.get("items") or []:
            t = re.sub(r"</?b>|&quot;|&amp;", lambda m: {"&quot;": '"', "&amp;": "&"}.get(m.group(0), ""),
                       str(it.get("title") or ""))
            out.append({"title": t, "link": it.get("link"),
                        "pub": str(it.get("pubDate") or "")[:22],
                        "desc": re.sub(r"</?b>", "", str(it.get("description") or ""))[:120]})
        _CACHE[key] = (time.time(), out)
        return out
    except Exception:
        return []
