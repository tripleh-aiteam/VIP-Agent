"""price_history — daily OHLCV served from OUR OWN DATA first (boss 2026-08-25:
"take the data from our server instead of Naver, I do not wanna pay").

The two servers already meet in the shared Supabase DB: the collection side writes
`raw_daily_prices` (2015→today, ~51 tickers) and this side reads it. This module makes
the chatbot's history answers read that table FIRST — free forever, 11 years deep —
with Naver used only to top up the freshest 1-2 sessions the end-of-day writer hasn't
landed yet, and as the full fallback for untracked tickers.
"""
from __future__ import annotations

from typing import Any, Optional

from services.logger import log


def rows(db, code: str, days: int) -> tuple[list[dict], str]:
    """Newest-first [{date, open, high, low, close, volume}] + a source label."""
    code = str(code).zfill(6)
    out: list[dict] = []
    try:
        if db is not None:
            from sqlalchemy import text
            rs = db.execute(text(
                "SELECT date, open, high, low, close, volume FROM raw_daily_prices "
                "WHERE ticker=:t ORDER BY date DESC LIMIT :n"),
                {"t": code, "n": int(days)}).fetchall()
            for r in rs:
                try:
                    out.append({"date": str(r[0])[:10],
                                "open": float(r[1]) if r[1] else None,
                                "high": float(r[2]) if r[2] else None,
                                "low": float(r[3]) if r[3] else None,
                                "close": float(r[4]) if r[4] else None,
                                "volume": float(r[5]) if r[5] else None})
                except Exception:
                    continue
    except Exception as e:
        log.warning(f"price_history db read failed ({code}): {str(e)[:100]}")
        out = []

    from services import naver_stock as ns
    if len(out) < 5:                       # untracked ticker / empty table → Naver fully
        try:
            return ns.daily_history(code, days=days), "네이버"
        except Exception:
            return out, "자체 DB"

    # freshness top-up: the collector writes end-of-day, so today/yesterday may be
    # missing — merge the newest Naver rows on top (dates the DB doesn't have yet).
    src = "자체 DB"
    try:
        nv = ns.daily_history(code, days=6) or []
        have = {r["date"] for r in out}
        fresh = sorted((r for r in nv if r.get("date") and r["date"] not in have
                        and r["date"] > out[0]["date"]),
                       key=lambda r: r["date"], reverse=True)
        if fresh:
            out = fresh + out
            src = "자체 DB + 네이버(최신)"
    except Exception:
        pass
    return out[:days], src
