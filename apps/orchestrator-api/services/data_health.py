"""data_health.py — Milestone 5.1: cheap data-quality guard.

Catches the case where a stock's DAILY-close series (which drives Method 1/3 + the
technicals) has drifted far from the LIVE price — a stale/unadjusted feed, a split, or
a bad row. When that happens the levels/targets are garbage (e.g. a ₩2.4M target on a
₩340k stock), so we flag it and keep it OUT of recommendations. Cached per ticker.
"""
from __future__ import annotations

import time
from typing import Any

from sqlalchemy import text

_cache: dict[str, tuple[float, dict]] = {}
_DIVERGENCE = 0.30      # >30% daily-vs-live gap = the daily feed is untrustworthy


def price_health(db, ticker: str) -> dict[str, Any]:
    """{ok, daily, live, divergence_pct, warn}. ok=False when daily vs live diverge >30%."""
    tk = str(ticker).zfill(6)
    hit = _cache.get(tk)
    if hit and (time.time() - hit[0]) < 300:
        return hit[1]
    daily = live = None
    try:
        r = db.execute(text("SELECT close FROM raw_daily_prices WHERE ticker=:t "
                            "ORDER BY date DESC LIMIT 1"), {"t": tk}).first()
        daily = float(r.close) if r and r.close else None
        from services.assistant_agent import _live_price_for_code
        from services.stock_resolver import display_name
        q = _live_price_for_code(tk, display_name(tk))
        live = float(q["price"]) if q and q.get("price") else None
    except Exception:
        pass
    div = (abs(daily - live) / live) if (daily and live) else 0.0
    ok = not (daily and live and div > _DIVERGENCE)
    out = {"ok": ok, "daily": daily, "live": live,
           "divergence_pct": round(div * 100, 1) if (daily and live) else None,
           "warn": (None if ok else
                    f"⚠️ 데이터 확인 필요 — 일봉 종가({int(daily):,})와 실시간가({int(live):,})가 크게 다름")}
    _cache[tk] = (time.time(), out)
    return out
