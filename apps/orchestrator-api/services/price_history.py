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


# The DATA PC's read-only HTTP API (boss 2026-08-25) — Tailscale, local, free.
# 2,815 KR daily codes · 115 KR minute codes · 517 US tickers · crypto · live ticks.
DATA_PC = "http://100.96.115.29:8010"


_PC_DOWN_UNTIL = [0.0]     # circuit breaker: after a failure, skip the data PC 120s


def _from_data_pc(code: str, days: int) -> list[dict]:
    """Daily OHLCV from the data PC (oldest-first there → newest-first here)."""
    import httpx
    # cap at 36 months — deeper asks go to Supabase; a months=240 sweep during setup
    # hung the data PC's API (2026-08-25), so we stay gentle with the small server
    months = max(1, min((days // 21) + 1, 36))
    # trust_env=False: a proxy env var must never reroute a Tailscale-local call
    with httpx.Client(trust_env=False, timeout=6) as _c:
        j = _c.get(f"{DATA_PC}/kr/daily/{code}", params={"months": months}).json()
    rows_ = j.get("data") or []
    out = [{"date": r.get("date"), "open": r.get("open"), "high": r.get("high"),
            "low": r.get("low"), "close": r.get("close"), "volume": r.get("volume"),
            "change_pct": (float(r["change"]) * 100 if r.get("change") is not None else None)}
           for r in rows_ if r.get("date") and r.get("close")]
    out.sort(key=lambda r: r["date"], reverse=True)
    return out[:days]


def rows(db, code: str, days: int) -> tuple[list[dict], str]:
    r, s, _m = rows_traced(db, code, days)
    return r, s


def rows_traced(db, code: str, days: int) -> tuple[list[dict], str, dict]:
    """Newest-first [{date, open, high, low, close, volume}] + source label + TRACE
    (which servers were tried, timing, row counts — drives the chat's animated
    'getting data from OUR server' view, boss 2026-08-25).
    Priority: DATA PC (2,815 codes, local, free) → Supabase (our 51) → Naver."""
    import time as _t
    code = str(code).zfill(6)
    trace: dict = {"steps": [], "source": None}

    def _step(tier, ok, t0, n=0, note=""):
        trace["steps"].append({"tier": tier, "ok": ok, "ms": int((_t.time() - t0) * 1000),
                               "rows": n, "note": note})
    out: list[dict] = []
    # tier 1 — the data PC (unless the ask outreaches its store: then the deeper
    # Supabase archive below wins — e.g. NAVER 10년 needs 2015→, data PC holds ~2y)
    _t0 = _t.time()
    try:
        if _t.time() < _PC_DOWN_UNTIL[0]:
            raise RuntimeError("data-pc cooling down after a failure")
        out = _from_data_pc(code, days)
        if len(out) >= 5 and len(out) >= int(days * 0.8):
            _step("data_pc", True, _t0, len(out))
            src = "데이터 PC"
            try:                        # freshest session top-up (today's live day)
                from services import naver_stock as ns
                nv = ns.daily_history(code, days=4) or []
                have = {r["date"] for r in out}
                fresh = sorted((r for r in nv if r.get("date") and r["date"] not in have
                                and r["date"] > out[0]["date"]),
                               key=lambda r: r["date"], reverse=True)
                if fresh:
                    out = fresh + out
                    src = "데이터 PC + 네이버(오늘)"
            except Exception:
                pass
            trace["source"] = src
            return out[:days], src, trace
        _step("data_pc", False, _t0, len(out), "depth")
    except Exception as e:
        _step("data_pc", False, _t0, 0, "timeout")
        if "cooling down" not in str(e):
            _PC_DOWN_UNTIL[0] = _t.time() + 120
        log.warning(f"price_history data-pc failed ({code}): {str(e)[:80]}")
    out = []
    _t0 = _t.time()
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
    _step("supabase", len(out) >= 5, _t0, len(out))

    from services import naver_stock as ns
    if len(out) < 5:                       # untracked ticker / empty table → Naver fully
        _t0 = _t.time()
        try:
            nv_all = ns.daily_history(code, days=days)
            _step("naver", True, _t0, len(nv_all))
            trace["source"] = "네이버"
            return nv_all, "네이버", trace
        except Exception:
            _step("naver", False, _t0)
            trace["source"] = "자체 DB"
            return out, "자체 DB", trace

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
    trace["source"] = src
    return out[:days], src, trace
