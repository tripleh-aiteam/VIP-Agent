"""
price_validate — independent cross-check of report prices against Google.

The report's prices come from the Stock Advisor backend (Kiwoom/Yahoo). To make
sure we never silently ship a wrong number, we ALSO look up each ticker's price
on Google (via the Serper API — the same SERPER_API_KEY already used for news)
and compare. Any large discrepancy is flagged on the row so the report can show
a ✓ / ⚠ verification mark instead of asking a human to eyeball it.

This runs server-side, so it works for BOTH the 06:50 auto-report and the
on-demand dashboard report — there is no human in the loop, the code self-checks.
"""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

from services.logger import log

# Flag a row when backend and Google disagree by more than this (fraction).
DISCREPANCY_THRESHOLD = 0.03  # 3%


def _serper_raw(query: str) -> dict[str, Any]:
    key = os.environ.get("SERPER_API_KEY")
    if not key:
        return {}
    try:
        with httpx.Client(timeout=12.0) as c:
            r = c.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": key, "Content-Type": "application/json"},
                json={"q": query, "num": 3},
            )
            r.raise_for_status()
            return r.json() or {}
    except Exception as e:  # network/api hiccup → "couldn't verify", never crash the report
        log.warning(f"price_validate serper '{query}': {e}")
        return {}


# Numbers that look like a price: 1,234.56 / 78400 / 145.20  (>= 1 to skip ratios)
_PRICE_RE = re.compile(r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\d{2,})")


def _extract_price(data: dict, currency_hint: str) -> float | None:
    """Pull the most price-looking number out of Google's answer box / KG.
    Prefers text sitting next to a currency marker (₩, $, KRW, USD)."""
    candidates: list[str] = []
    ab = data.get("answerBox") or {}
    for k in ("answer", "snippet", "title"):
        if ab.get(k):
            candidates.append(str(ab[k]))
    kg = data.get("knowledgeGraph") or {}
    # knowledgeGraph sometimes exposes a price-ish attribute
    for k, v in (kg.get("attributes") or {}).items():
        if any(w in k.lower() for w in ("price", "value", "주가", "시세")):
            candidates.append(str(v))
    if kg.get("description"):
        candidates.append(str(kg["description"]))
    for org in (data.get("organic") or [])[:2]:
        if org.get("snippet"):
            candidates.append(str(org["snippet"]))

    marker = "₩" if currency_hint == "KRW" else "$"
    # Pass 1: a number adjacent to the right currency marker.
    for text in candidates:
        for m in re.finditer(re.escape(marker) + r"\s*(\d[\d,]*\.?\d*)", text):
            val = _to_float(m.group(1))
            if val and val > 1:
                return val
    # Pass 2: a number adjacent to the currency CODE.
    for text in candidates:
        for m in re.finditer(r"(\d[\d,]*\.?\d*)\s*" + currency_hint, text):
            val = _to_float(m.group(1))
            if val and val > 1:
                return val
    # Pass 3: first plausible price-shaped number in the answer box.
    for text in candidates[:2]:
        m = _PRICE_RE.search(text)
        if m:
            val = _to_float(m.group(1))
            if val and val > 1:
                return val
    return None


def _to_float(s: str) -> float | None:
    try:
        return float(s.replace(",", ""))
    except Exception:
        return None


def _extract_labeled(data: dict, labels: tuple[str, ...]) -> float | None:
    """Best-effort: pull a number that sits right after one of `labels`
    (e.g. 'Open 452.40', 'Open: 452.40') in Google's answer box / snippets."""
    texts: list[str] = []
    ab = data.get("answerBox") or {}
    for k in ("answer", "snippet"):
        if ab.get(k):
            texts.append(str(ab[k]))
    kg = data.get("knowledgeGraph") or {}
    for k, v in (kg.get("attributes") or {}).items():
        texts.append(f"{k} {v}")
    for org in (data.get("organic") or [])[:2]:
        if org.get("snippet"):
            texts.append(str(org["snippet"]))
    pat = re.compile(r"(?:" + "|".join(labels) + r")[:\s]*[$₩]?\s*(\d[\d,]*\.?\d*)", re.IGNORECASE)
    for text in texts:
        m = pat.search(text)
        if m:
            val = _to_float(m.group(1))
            if val and val > 1:
                return val
    return None


def google_price(ko_name: str, en_name: str, ticker: str, market: str) -> dict[str, Any]:
    """Look up the current price on Google. Returns {price, open, currency, query}.
    KR tickers → KRW search ('<한글명> 주가'); US → USD ('<TICKER> stock price').
    `open` is best-effort (Google sometimes lists the day's open) — may be None."""
    if market == "KR":
        query, currency = f"{ko_name} 주가", "KRW"
    else:
        query, currency = f"{ticker} {en_name} stock price", "USD"
    data = _serper_raw(query)
    price = _extract_price(data, currency) if data else None
    open_ = _extract_labeled(data, ("open", "시가")) if data else None
    return {"price": price, "open": open_, "currency": currency, "query": query}


def backfill_stale_us(rows: list[dict], expected_latest_us: str) -> int:
    """When the backend's US (Yahoo) data is EMPTY or older than the most recent
    completed US session, replace that row's price with Google's latest. Fixes the
    backend's day-behind ingestion lag (e.g. serving 06-09 when 06-10 has closed).
    Returns the number of rows backfilled. KR rows are untouched."""
    n = 0
    for r in rows:
        if r.get("mkt") != "US":
            continue
        dd = r.get("data_date") or ""
        stale = (not r.get("ok")) or (dd < expected_latest_us)
        if not stale:
            continue
        g = google_price(r.get("ko", ""), r.get("en", ""), r.get("t", ""), "US")
        gp = g.get("price")
        if gp:
            r["close"] = gp
            # Google gives the latest quote; the day's open is best-effort. Blank
            # high/low (not reliably available) rather than show stale figures.
            r["open"] = g.get("open")
            r["high"] = r["low"] = None
            r["change_pct"] = None
            r["price_kind"] = "google"
            # Label with the session it represents (latest completed US session).
            r["data_date"] = expected_latest_us
            r["source"] = "google"
            r["ok"] = True
            n += 1
    if n:
        log.info(f"price_validate: backfilled {n} stale US rows from Google",
                 extra={"action": "price.backfill"})
    return n


def cross_check_rows(rows: list[dict]) -> dict[str, Any]:
    """Compare each row's NATIVE-currency price against Google. Mutates rows,
    adding g_price / g_diff_pct / g_flag, and returns a summary dict.

    Compares native `close` (KRW for KR, USD for US) so it lines up with what
    Google shows in that market's own currency."""
    checked = matched = flagged = unverified = 0
    for r in rows:
        if not r.get("ok") or r.get("close") is None:
            r["g_flag"] = "—"
            continue
        if r.get("source") == "google":
            # Price already came FROM Google (backend was stale) — verifying it
            # against Google is circular; just mark its provenance.
            r["g_flag"] = "Google"
            r["g_diff_pct"] = None
            continue
        checked += 1
        g = google_price(r.get("ko", ""), r.get("en", ""), r.get("t", ""), r.get("mkt", "US"))
        gp = g.get("price")
        r["g_price"] = gp
        r["g_query"] = g.get("query")
        if gp is None:
            r["g_flag"] = "확인불가"  # couldn't read a price off Google
            r["g_diff_pct"] = None
            unverified += 1
            continue
        diff = (r["close"] - gp) / gp if gp else None
        r["g_diff_pct"] = (diff * 100) if diff is not None else None
        if diff is not None and abs(diff) <= DISCREPANCY_THRESHOLD:
            r["g_flag"] = "✓"
            matched += 1
        else:
            r["g_flag"] = "⚠"
            flagged += 1
    summary = {"checked": checked, "matched": matched, "flagged": flagged,
               "unverified": unverified}
    log.info(f"price_validate: {summary}", extra={"action": "price.validate"})
    return summary
