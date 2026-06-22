"""news_sentiment_collector — daily per-stock news + sentiment → raw_news.

Runs ON Render (light: Serper search + one cheap LLM call per stock, no ML libs).
Accumulates the news/sentiment training data we currently lack, so that — after a
few weeks — build_features can join it and we can retrain WITH the news edge.

Writes to the same Supabase `raw_news` table the ML pipeline reads. Point-in-time:
each row carries the article's published time.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone, timedelta

from sqlalchemy import text

from services.logger import log
from services.prediction_service import NAMES  # the 37-stock universe + Korean names

KST = timezone(timedelta(hours=9))


def _score_sentiment(name: str, headlines: list[str]) -> list[float]:
    """One cheap LLM call → sentiment in [-1,1] per headline (호재 +, 악재 -)."""
    if not headlines:
        return []
    from services.llm_client import chat_completion_sync
    numbered = "\n".join(f"{i+1}. {h[:160]}" for i, h in enumerate(headlines))
    sysmsg = (
        f"You score Korean-stock news sentiment for '{name}'. For EACH numbered "
        "headline, give a sentiment score from -1.0 (very bearish/악재) to +1.0 "
        "(very bullish/호재), 0 = neutral, from the perspective of THIS stock's price. "
        "Return STRICT JSON only: a list of numbers, same length/order as the input. "
        "Example: [0.6, -0.3, 0.0]")
    try:
        out = chat_completion_sync(
            system_prompt=sysmsg, messages=[{"role": "user", "content": numbered}],
            max_tokens=200, temperature=0.0, model="groq-llama-3.3-70b") or ""
        m = re.search(r"\[[^\]]*\]", out)
        arr = json.loads(m.group(0)) if m else []
        out_scores = []
        for i in range(len(headlines)):
            try:
                v = float(arr[i])
                out_scores.append(max(-1.0, min(1.0, v)))
            except Exception:
                out_scores.append(0.0)
        return out_scores
    except Exception as e:
        log.warning(f"news-sentiment: scoring failed for {name}: {str(e)[:70]}")
        return [0.0] * len(headlines)


def collect_for_ticker(db, ticker: str, name: str) -> int:
    from services.web_search import search_web
    res = search_web(f"{name} 주가 실적 뉴스 호재 악재", num_results=5, recency="d")
    arts = res.get("results", []) if res.get("ok") else []
    if not arts:
        return 0
    scores = _score_sentiment(name, [a.get("title", "") for a in arts])
    now = datetime.now(KST)
    n = 0
    for a, s in zip(arts, scores):
        url = (a.get("url") or "").strip()
        title = (a.get("title") or "").strip()
        if not url or not title:
            continue
        try:
            db.execute(text(
                "INSERT INTO raw_news (ts, ticker, source, url, title, snippet, sentiment) "
                "VALUES (:ts,:tk,:src,:url,:title,:snip,:sent) "
                "ON CONFLICT (url, ticker) DO UPDATE SET sentiment=EXCLUDED.sentiment"),
                {"ts": now, "tk": ticker, "src": "serper", "url": url[:1000],
                 "title": title[:500], "snip": (a.get("snippet") or "")[:1000], "sent": s})
            n += 1
        except Exception as e:
            log.warning(f"news-sentiment: insert failed {ticker}: {str(e)[:60]}")
            db.rollback()
    db.commit()
    return n


def collect_all(db, limit: int | None = None) -> dict:
    items = list(NAMES.items())
    if limit:
        items = items[:limit]
    total = 0; stocks = 0
    for ticker, name in items:
        try:
            c = collect_for_ticker(db, ticker, name)
            total += c
            stocks += 1 if c else 0
        except Exception as e:
            log.warning(f"news-sentiment: {ticker} failed: {str(e)[:70]}")
    log.info(f"news-sentiment: collected {total} articles across {stocks} stocks",
             extra={"action": "news_sentiment.collect"})
    return {"articles": total, "stocks": stocks}
