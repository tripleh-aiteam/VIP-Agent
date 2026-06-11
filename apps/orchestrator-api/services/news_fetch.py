"""
news_fetch — last-24h article collection + full-text reading for the deep
newspaper report.

RSS gives a timestamped list of each outlet's recent articles (so we filter
strictly to the last `hours`). We then fetch each FREE article's full body
(trafilatura). Paywalled / premium articles (한경 프리미엄, 매경 등) can't be read —
those fall back to their RSS summary. WSJ/Bloomberg are paywalled, so they are
handled by search (headline + snippet) elsewhere, not here.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx

from services.logger import log

_UA = {"User-Agent": "Mozilla/5.0 (compatible; TripleHReports/1.0)"}

# RSS feeds per Korean outlet (multiple for coverage). Outlets whose RSS is
# blocked/unknown (매일경제 403, SBS Biz) fall back to recency search in the caller.
RSS_FEEDS: dict[str, list[str]] = {
    "Korea Economic Daily (한국경제)": [
        "https://www.hankyung.com/feed/finance",
        "https://www.hankyung.com/feed/economy",
        "https://www.hankyung.com/feed/stock",
    ],
    "MoneyToday (머니투데이)": [
        "https://rss.mt.co.kr/mt_news.xml",
        "https://rss.mt.co.kr/stock_news.xml",
        "https://rss.mt.co.kr/economy_news.xml",
    ],
    "Maeil Business (매일경제)": [
        "https://www.mk.co.kr/rss/30000001/",
        "https://www.mk.co.kr/rss/50200011/",
    ],
    "SBS Biz": [
        "https://news.sbs.co.kr/news/rss.do?plink=RSSREADER",
    ],
}


def _pubdate(s: str | None):
    try:
        return parsedate_to_datetime(s) if s else None
    except Exception:
        return None


def rss_items(outlet: str, hours: int = 24, cap: int = 25) -> list[dict]:
    """Recent articles for an outlet via its RSS feeds, filtered to the last
    `hours`. Returns [{title, url, summary, pub}]."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out: list[dict] = []
    seen: set[str] = set()
    for url in RSS_FEEDS.get(outlet, []):
        if len(out) >= cap:
            break
        try:
            import defusedxml.ElementTree as ET
            r = httpx.get(url, headers=_UA, timeout=15, follow_redirects=True)
            if r.status_code != 200:
                continue
            root = ET.fromstring(r.text)
            for it in root.findall(".//item"):
                title = (it.findtext("title") or "").strip()
                link = (it.findtext("link") or "").strip()
                desc = (it.findtext("description") or "").strip()
                desc = re.sub(r"(?is)<[^>]+>", " ", desc)
                desc = re.sub(r"\s+", " ", desc).strip()
                pub = _pubdate(it.findtext("pubDate"))
                if not link or link in seen:
                    continue
                if pub and pub.tzinfo and pub < cutoff:
                    continue  # older than the window
                seen.add(link)
                out.append({"title": title, "url": link, "summary": desc[:400],
                            "pub": pub.isoformat() if pub else None})
                if len(out) >= cap:
                    break
        except Exception as e:
            log.warning(f"news_fetch: rss {url} failed: {str(e)[:90]}")
    return out


_PAYWALL = ("프리미엄", "구독하", "구독하고", "구독하시", "Subscribe to", "subscription",
            "sign in to read", "회원 전용", "로그인 후 이용")


def fetch_fulltext(url: str, max_chars: int = 4000) -> str:
    """Full article body via trafilatura. Returns '' for paywalled/empty pages so
    the caller can fall back to the RSS summary."""
    if not url:
        return ""
    try:
        import trafilatura
        r = httpx.get(url, headers=_UA, timeout=20, follow_redirects=True)
        if r.status_code != 200:
            return ""
        txt = trafilatura.extract(r.text, include_comments=False, include_tables=False,
                                  favor_precision=True) or ""
        txt = re.sub(r"\s+", " ", txt).strip()
        if len(txt) < 250:
            return ""  # too short → stub/paywall
        # If it's mostly a subscription notice, treat as paywalled.
        head = txt[:400]
        if any(k in head for k in _PAYWALL) and len(txt) < 600:
            return ""
        return txt[:max_chars]
    except Exception as e:
        log.warning(f"news_fetch: fulltext {url} failed: {str(e)[:90]}")
        return ""
