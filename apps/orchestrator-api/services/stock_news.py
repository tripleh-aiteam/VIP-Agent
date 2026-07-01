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


def _fetch_items(name: str, limit: int, days: int) -> list[dict]:
    """A LIST of recent real Korean headlines for a stock. Naver News (clean Korean list)
    first, then top up with Serper Google-News. Never returns a single 'Answer' blob."""
    from services.news_impact import classify
    items: list[dict] = []
    seen: set[str] = set()

    def _add(hits):
        for h in hits or []:
            title = (h.get("title") or "").strip()
            if not title or title.lower() == "answer" or title in seen:
                continue
            seen.add(title)
            snip = (h.get("snippet") or "").strip()
            ntype, _imp, ddir = classify(title, snip)
            items.append({"title": title, "snippet": snip[:220], "url": h.get("url") or "",
                          "source": _domain(h.get("url") or ""), "type": ntype,
                          "direction": "▲" if ddir > 0 else "▼" if ddir < 0 else "•"})

    # 1) Naver News API — authoritative Korean news list (real titles + links + dates).
    try:
        from services.naver_search import naver_search
        _add((naver_search(f"{name} 주가", kind="news", num_results=max(limit, 10)) or {}).get("results"))
    except Exception as e:
        log.warning(f"stock_news naver: {str(e)[:100]}")
    # 2) Top up with Serper Google-News (a LIST) — skip the gemini single-answer fallback.
    if len(items) < limit:
        try:
            from services.web_search import search_web
            ws = search_web(f"{name} 주가 뉴스", num_results=max(limit, 10),
                            recency="d" if days <= 1 else "w")
            if ws.get("provider") == "serper":     # only serper returns a real list
                _add(ws.get("results"))
        except Exception as e:
            log.warning(f"stock_news serper: {str(e)[:100]}")
    return items[:limit]


def stock_news(db, ticker: str, days: int = 7, limit: int = 15) -> dict[str, Any]:
    """A LIST of recent Korean news for one stock (real headlines, clickable). No summary
    here — the summary is an on-demand button (news_summary). {ticker, name, count, items[]}."""
    from services.stock_resolver import display_name
    tk = str(ticker).zfill(6)
    name = display_name(tk)
    items = _fetch_items(name, limit, days)
    return {"ticker": tk, "name": name, "count": len(items), "items": items,
            "configured": bool(items), "days": days}


def news_summary(db, ticker: str, days: int = 7, limit: int = 15) -> dict[str, Any]:
    """On-demand AI summary of the stock's recent news (the '요약 보기' button)."""
    from services.stock_resolver import display_name
    tk = str(ticker).zfill(6)
    name = display_name(tk)
    items = _fetch_items(name, limit, days)
    return {"ticker": tk, "name": name, "summary_ko": _summary(name, items), "count": len(items)}
