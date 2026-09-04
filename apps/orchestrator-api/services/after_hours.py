# -*- coding: utf-8 -*-
"""after_hours — the price the market pays AFTER the bell, so an overnight gap
is measured from the last real price instead of a stale 15:30 close.

Boss 2026-09-03 evening: "in order to know there is a 갭상승 we have to compare
with the 9am price and one day before 20:00 price - so how can we get this
data?"

Where it comes from, and why not from Kiwoom. KRX trades on after the bell -
15:40-16:00 시간외 종가, then 16:00-18:00 시간외 단일가 - but Kiwoom's feed
gives us none of it: tested live at 17:06 during an open 시간외 session, every
execution tape stopped at 15:30:2x and the quote endpoint returned exactly the
15:30 close. Naver's stock API does carry it, in overMarketPriceInfo.overPrice,
and that is the source used here (the same house we already fall back to for
price history).

There is no 20:00 session on KRX. 18:00 is where trading actually stops, so the
18:00 print is the last real price of the day and is what this records.
"""
from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime
from pathlib import Path

_FILE = Path(__file__).resolve().parent.parent / "data" / "after_hours.json"
_URL = "https://m.stock.naver.com/api/stock/{code}/basic"
_HDR = {"User-Agent": "Mozilla/5.0"}


def _num(v) -> float | None:
    try:
        return float(re.sub(r"[^0-9.\-]", "", str(v))) or None
    except Exception:
        return None


def fetch(code: str) -> dict | None:
    """One stock's after-market print, or None when the session never traded it."""
    try:
        req = urllib.request.Request(_URL.format(code=code), headers=_HDR)
        d = json.loads(urllib.request.urlopen(req, timeout=20).read())
    except Exception:
        return None
    info = d.get("overMarketPriceInfo") or {}
    close = _num(d.get("closePrice"))
    # THE LAST PRICE OF THE DAY IS THE NXT CLOSE, NOT THE 18:00 KRX PRINT
    # (boss 2026-09-04, checking HD현대중공업 against Kiwoom: "yesterday's
    # closing price at 19:59 was 429,500" while we had stored 432,500).
    # Korea has TWO venues now: KRX's 시간외 단일가 ends at 18:00, but NXT
    # keeps trading to 20:00 - and his 19:59 quote is the NXT one. We were
    # capturing the earlier venue and calling it the day's last price, which
    # made every gap we measured slightly too small.
    px = None
    try:
        from services.naver_stock import realtime_quote as _rq
        q = _rq(code) or {}
        px = _num(q.get("nxt_price"))
    except Exception:
        px = None
    if not px:
        px = _num(info.get("overPrice"))      # KRX 시간외, if NXT is silent
    if not px:
        return None
    return {"px": px, "close": close,
            "status": str(info.get("overMarketStatus") or ""),
            "session": str(info.get("tradingSessionType") or "")}


def _load() -> dict:
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def record(day8: str, codes: list) -> dict:
    """Store today's after-market close for each code. Only real prints are
    written - a stock that never traded after hours simply has no entry, and
    the gap then falls back to its official close."""
    all_ = _load()
    day = all_.setdefault(day8, {})
    got = {}
    for c, n in codes:
        r = fetch(c)
        if r and r.get("px"):
            day[str(c)] = {"px": r["px"], "close": r.get("close"), "name": n}
            got[str(c)] = r["px"]
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    _FILE.write_text(json.dumps(all_, ensure_ascii=False, indent=1), encoding="utf-8")
    return got


def price(day8: str, code: str) -> float | None:
    """The recorded after-market price for one stock on one day."""
    try:
        return float((_load().get(day8) or {}).get(str(code), {}).get("px") or 0) or None
    except Exception:
        return None
