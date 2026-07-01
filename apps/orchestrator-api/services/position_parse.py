"""position_parse.py — Milestone 2.1: understand the user's holding from plain words.

Extracts what the user tells the chatbot about a position they already hold — the
stock, share count, entry price and/or current P&L %, and roughly when they bought —
from natural Korean/English ("지난주 SK하이닉스 200주 샀는데 -4%야", "bought 100 Samsung
at 70,000, now +3%"). Regex-based (fast, no LLM); the ticker uses stock_resolver.
"""
from __future__ import annotations

import re
from typing import Any, Optional


# "position" markers → this is about a holding the user already has (not a fresh query)
_POSITION_KW = (
    "샀는데", "샀어", "샀다", "매수했", "보유", "가지고", "들고", "물렸", "평단", "평균단가",
    "손실", "수익", "물타기", "손절할까", "팔까", "팔아야", "익절", "지금 팔", "계속 들고",
    "bought", "i hold", "holding", "i own", "i'm holding", "im holding", "average price",
    "avg price", "my position", "down ", "up ", "underwater", "in the red", "in the green",
)


def is_position_question(text: str) -> bool:
    """True when the user is describing a stock they already hold + asking what to do."""
    t = (text or "").lower()
    if not any(k in t for k in _POSITION_KW):
        return False
    try:
        from services.stock_resolver import find_all
        return bool(find_all(text))
    except Exception:
        return False


def _num(s: str) -> Optional[float]:
    try:
        return float(s.replace(",", ""))
    except Exception:
        return None


def parse(text: str) -> dict[str, Any]:
    """Return {ticker, name, shares, entry_price, pnl_pct, when} — any field may be None."""
    t = (text or "").strip()
    low = t.lower()
    out: dict[str, Any] = {"ticker": None, "name": None, "shares": None,
                           "entry_price": None, "pnl_pct": None, "when": None}
    try:
        from services.stock_resolver import resolve_one
        c, n = resolve_one(t)
        out["ticker"], out["name"] = c, n
    except Exception:
        pass

    # shares — "200주", "100 shares", "200개"
    m = re.search(r"([\d,]+)\s*(?:주|shares?|share|개)", low)
    if m:
        out["shares"] = int(_num(m.group(1)) or 0) or None

    # P&L % — "-4%", "+3%", "4% 손실", "down 4%", "4% 물렸"
    pnl = None
    m = re.search(r"([+\-]?\d+(?:\.\d+)?)\s*%", low)
    if m:
        pnl = _num(m.group(1))
        # words that flip an unsigned % to negative
        neg = any(w in low for w in ("손실", "물렸", "마이너스", "down", "underwater", "in the red", "-"))
        pos = any(w in low for w in ("수익", "플러스", "up ", "in the green", "+"))
        if pnl is not None and pnl > 0 and neg and not pos:
            pnl = -pnl
    out["pnl_pct"] = pnl

    # entry price — "70,000에", "at 70000", "평단 70000"
    m = re.search(r"(?:평단가?|평균단가|at|에)\s*([\d,]{4,})", low) or re.search(r"([\d,]{4,})\s*(?:원|krw)?\s*(?:에 샀|에 매수|에)", low)
    if m:
        out["entry_price"] = _num(m.group(1))

    # when — coarse
    for kw, label in (("지난주", "지난주"), ("last week", "last week"), ("어제", "어제"),
                      ("yesterday", "yesterday"), ("오늘", "오늘"), ("today", "today"),
                      ("이번주", "이번주"), ("this week", "this week"), ("지난달", "지난달"),
                      ("last month", "last month"), ("아까", "아까")):
        if kw in low:
            out["when"] = label
            break
    return out
