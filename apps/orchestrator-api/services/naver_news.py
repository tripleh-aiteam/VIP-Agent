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


def pub_age_min(pub: str) -> float | None:
    """Minutes since an article's pubDate (RFC-822, e.g. 'Thu, 04 Sep 2026
    14:20:00 +0900'), or None when unreadable. The REAL-TIME news law (boss
    2026-09-04: 'remove old days or old time news') filters on this."""
    try:
        from email.utils import parsedate_to_datetime
        from datetime import datetime, timezone
        dt = parsedate_to_datetime(str(pub))
        return (datetime.now(timezone.utc) - dt).total_seconds() / 60.0
    except Exception:
        return None


def fresh_news(query: str, display: int = 5, max_age_min: int = 1440) -> list[dict]:
    """search_news + the real-time law: only articles younger than
    max_age_min, each row gaining 'age_min'. Articles whose clock cannot be
    read are dropped — unknown age is not real-time."""
    out = []
    for a in search_news(query, display=display):
        age = pub_age_min(a.get("pub") or "")
        if age is None or age > max_age_min:
            continue
        out.append({**a, "age_min": round(age)})
    return out


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
                        # FULL RFC-822 date — the old [:22] cut chopped the
                        # time+zone off, so pub_age_min could never read it
                        # and the real-time filter dropped EVERY article
                        "pub": str(it.get("pubDate") or "")[:40],
                        "desc": re.sub(r"</?b>", "", str(it.get("description") or ""))[:120]})
        _CACHE[key] = (time.time(), out)
        return out
    except Exception:
        return []
