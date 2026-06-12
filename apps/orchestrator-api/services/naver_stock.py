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


def nxt_close(code: str) -> dict | None:
    """Regular close + NXT/after-market (시간외) price for a KR ticker."""
    try:
        b = httpx.get(f"{_BASE}/{code}/basic", headers=_H, timeout=12).json()
    except Exception as e:
        log.warning(f"naver nxt_close {code}: {str(e)[:80]}")
        return None
    om = b.get("overMarketPriceInfo") or {}
    return {
        "regular_close": _num(b.get("closePrice")),
        "nxt_price": _num(om.get("overPrice")),
        "nxt_session": om.get("tradingSessionType") or "",
        "nxt_status": om.get("overMarketStatus") or "",
    }


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


def enrich_kr(code: str) -> dict:
    """Combined NXT + latest investor flows for one KR ticker (best-effort)."""
    out: dict = {}
    nx = nxt_close(code)
    if nx:
        out.update(nx)
    fl = investor_flows(code, days=1)
    if fl:
        out["flow"] = fl[0]
    return out
