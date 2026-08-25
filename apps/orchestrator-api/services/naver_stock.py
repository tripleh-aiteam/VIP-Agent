"""
naver_stock — supplementary KR-equity data from Naver Finance's free mobile API:
  • NXT / after-market (시간외·넥스트레이드) close price
  • per-stock foreign / institutional / individual net-buy flows (투자자별 순매수)
No API key. Used to enrich the Kiwoom report (KR tickers only).
"""

from __future__ import annotations

import httpx

from services.logger import log

_BASE = "https://m.stock.naver.com/api/stock"
_H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36",
      "Referer": "https://m.stock.naver.com/"}


def _num(s) -> float | None:
    try:
        return float(str(s).replace(",", "").replace("+", "").replace("%", "").strip())
    except Exception:
        return None


def _signed(ratio, direction) -> float | None:
    """Apply +/- sign to an unsigned fluctuation ratio using the direction code."""
    v = _num(ratio)
    if v is None:
        return None
    name = ((direction or {}).get("name") or "").upper()
    if name in ("FALLING", "LOWER_LIMIT") or "하락" in ((direction or {}).get("text") or ""):
        return -abs(v)
    return abs(v)


def realtime_quote(code: str) -> dict | None:
    """REAL-TIME current price + change% for a KR ticker (Naver, ~live during
    trading) PLUS the NXT / after-market (시간외) price. The Naver `basic`
    endpoint's closePrice tracks the live price intra-session (localTradedAt is
    the actual trade time), so it's fresher than the daily candle."""
    try:
        b = httpx.get(f"{_BASE}/{code}/basic", headers=_H, timeout=12).json()
    except Exception as e:
        log.warning(f"naver realtime {code}: {str(e)[:80]}")
        return None
    om = b.get("overMarketPriceInfo") or {}
    return {
        "price": _num(b.get("closePrice")),
        "change_pct": _signed(b.get("fluctuationsRatio"), b.get("compareToPreviousPrice")),
        "open": _num(b.get("openPrice")),
        "high": _num(b.get("highPrice")),
        "low": _num(b.get("lowPrice")),
        "volume": _num(b.get("accumulatedTradingVolume")),
        "market_status": b.get("marketStatus") or "",       # OPEN / CLOSE
        "as_of": (b.get("localTradedAt") or "")[11:16],     # HH:MM
        "nxt_price": _num(om.get("overPrice")),
        "nxt_change_pct": _signed(om.get("fluctuationsRatio"), om.get("compareToPreviousPrice")),
        "nxt_status": om.get("overMarketStatus") or "",     # OPEN / CLOSE
    }


def nxt_close(code: str) -> dict | None:
    """Backwards-compatible thin wrapper around realtime_quote()."""
    q = realtime_quote(code)
    if not q:
        return None
    return {"regular_close": q.get("price"), "nxt_price": q.get("nxt_price"),
            "nxt_session": "", "nxt_status": q.get("nxt_status")}


def investor_flows(code: str, days: int = 2) -> list[dict]:
    """Latest foreign/institutional/individual net-buy quantities per day."""
    try:
        r = httpx.get(f"{_BASE}/{code}/trend", headers=_H, timeout=12).json()
    except Exception as e:
        log.warning(f"naver flows {code}: {str(e)[:80]}")
        return []
    out = []
    for d in (r or [])[:days]:
        out.append({
            "date": d.get("bizdate"),
            "foreign": _num(d.get("foreignerPureBuyQuant")),
            "organ": _num(d.get("organPureBuyQuant")),
            "individual": _num(d.get("individualPureBuyQuant")),
            "foreign_hold": d.get("foreignerHoldRatio"),
        })
    return out


def daily_history(code: str, days: int = 20) -> list[dict]:
    """Daily OHLCV history for a KR ticker from Naver Finance (free, no key).

    Returns a NEWEST-FIRST list of
    ``{date, open, high, low, close, change_pct, volume}`` (up to ``days`` rows,
    capped at 400 ≈ 18 months). ``[]`` on any failure. Works for ANY KR ticker and
    goes back months/quarters — the basis for past-date price questions + technicals."""
    try:
        n = max(1, min(int(days or 20), 400))
    except Exception:
        n = 20
    # Naver rejects pageSize > 60 (used to be 90) — page in chunks of 60 instead.
    # page=1 is the newest 60, page=2 the next 60, so concatenation stays newest-first.
    # up to 8 pages (~480 rows raw) so a specific date up to ~18 months back resolves.
    r: list = []
    try:
        page = 1
        while len(r) < n and page <= 8:      # constant pageSize keeps page offsets aligned
            chunk = httpx.get(f"{_BASE}/{code}/price?pageSize=60&page={page}",
                              headers=_H, timeout=12).json()
            if not isinstance(chunk, list) or not chunk:
                break
            r.extend(chunk)
            if len(chunk) < 60:              # no more history available
                break
            page += 1
        r = r[:n]
    except Exception as e:
        if not r:
            log.warning(f"naver daily {code}: {str(e)[:80]}")
            return []
    out: list[dict] = []
    for d in (r or []):
        close = _num(d.get("closePrice"))
        if close is None:
            continue
        out.append({
            "date": (d.get("localTradedAt") or "")[:10],
            "open": _num(d.get("openPrice")),
            "high": _num(d.get("highPrice")),
            "low": _num(d.get("lowPrice")),
            "close": close,
            "change_pct": _signed(d.get("fluctuationsRatio"), d.get("compareToPreviousPrice")),
            "volume": _num(d.get("accumulatedTradingVolume")),
        })
    return out


_FUND_CACHE: dict = {}


def fundamentals(code: str) -> dict | None:
    """PER/PBR/EPS/BPS · dividend · market cap · 52-week band · foreign rate + the real
    analyst consensus (target mean / opinion mean) for one KR ticker, from the
    m.stock.naver integration API. Cached 10 min. None on total failure."""
    import time
    c = str(code).zfill(6)
    hit = _FUND_CACHE.get(c)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    try:
        j = httpx.get(f"{_BASE}/{c}/integration", headers=_H, timeout=12).json()
        info = {t.get("code"): t.get("value") for t in (j.get("totalInfos") or [])}
        cons = j.get("consensusInfo") or {}
        out = {"name": j.get("stockName"), "info": info,
               "target_mean": cons.get("priceTargetMean"),
               "recomm_mean": cons.get("recommMean"),
               "consensus_date": cons.get("createDate"),
               "researches": [{"title": r.get("tit"), "broker": r.get("bnm"),
                               "date": r.get("wdt")} for r in (j.get("researches") or [])[:3]]}
        _FUND_CACHE[c] = (time.time(), out)
        return out
    except Exception:
        return hit[1] if hit else None


def enrich_kr(code: str) -> dict:
    """Combined real-time quote + NXT + latest investor flows for one KR ticker."""
    out: dict = {}
    q = realtime_quote(code)
    if q:
        out.update(q)
    fl = investor_flows(code, days=1)
    if fl:
        out["flow"] = fl[0]
    return out
