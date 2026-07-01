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
    # ONLY real "I already hold this" markers. Pure decision verbs (팔까/팔아야/사야/살까) are
    # NOT here — 'X 사야 할까 팔까?' is a fresh buy/sell DECISION (→ decide), not a position.
    # A held position with those verbs is still caught by the implicit shares/P&L path below.
    "샀는데", "샀어", "샀다", "매수했", "매수 했", "보유", "가지고", "들고", "물렸", "물려",
    "평단", "평균단가", "물타기", "지금 팔", "계속 들고", "계속 보유",
    "bought", "i hold", "holding", "i own", "i'm holding", "im holding", "average price",
    "avg price", "my position", "underwater", "in the red", "in the green",
)
# advice cues that, together with a stock + a P&L, mean "I hold this — what do I do?"
_POS_ADVICE_CUE = (
    "어떡해", "어떡하지", "어떻게 해", "어떻게해", "어떻할", "어쩌지", "어쩔", "어때",
    "팔", "손절", "익절", "물타", "버텨", "버틸", "정리",
    "what should i do", "should i sell", "should i hold", "hold or sell", "sell or hold",
    "should i cut", "cut or hold", "take profit", "what do i do",
)
_PNL_RE = __import__("re").compile(r"[+\-]?\d+(?:\.\d+)?\s*%")


def is_position_question(text: str) -> bool:
    """True when the user describes a stock they HOLD + asks what to do. Detected either
    by an explicit holding word, OR by (stock + a P&L%/shares + an advice cue) — so
    '지난주 SK하이닉스 200주 -4%에 어떡해?' is caught even without the word '샀다'."""
    t = (text or "").lower()
    try:
        from services.stock_resolver import find_all
        if not find_all(text):
            return False
    except Exception:
        return False
    if any(k in t for k in _POSITION_KW):
        return True
    # holding described implicitly: shares/P&L present AND an advice cue
    has_pnl = bool(_PNL_RE.search(t))
    has_shares = ("주" in t) or ("share" in t)
    has_cue = any(c in t for c in _POS_ADVICE_CUE)
    return (has_pnl or has_shares) and has_cue


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
