"""position_size.py — "몇 주 사?" answered with risk discipline.

Parses the user's budget from the message ("500만원으로", "1억", "5 million won"),
remembers it per user, and sizes the position two ways — budget cap (can't spend more
than you have) and risk cap (a stopped-out trade may cost at most ~1% of capital) —
taking the SAFER of the two. For scalping the risk cap is what keeps one oversized
loser from erasing twenty +1% winners.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from sqlalchemy import text

from services.logger import log

_DDL = """
CREATE TABLE IF NOT EXISTS user_trade_budget (
    user_key  TEXT PRIMARY KEY,
    budget    BIGINT NOT NULL,
    updated   TIMESTAMPTZ DEFAULT now()
)
"""

RISK_PCT_PER_TRADE = 1.0          # max % of capital lost if the stop is hit


def _ensure(db) -> None:
    try:
        db.execute(text(_DDL))
        db.commit()
    except Exception as e:
        db.rollback(); log.warning(f"position_size ensure: {str(e)[:120]}")


def parse_budget(msg: str) -> Optional[int]:
    """Extract a KRW budget from KO/EN text. Returns won, or None if not stated."""
    t = (msg or "").replace(",", "").replace(" ", "")
    # 1억 / 1억5000만 / 2억3천만
    m = re.search(r"(\d+(?:\.\d+)?)억(?:(\d+)(?:천만|만))?", t)
    if m:
        won = float(m.group(1)) * 100_000_000
        if m.group(2):
            unit = 10_000_000 if "천만" in m.group(0) else 10_000
            won += int(m.group(2)) * unit
        return int(won)
    # 5000만원 / 500만 / 3천만원 / 30만원
    m = re.search(r"(\d+(?:\.\d+)?)천만", t)
    if m:
        return int(float(m.group(1)) * 10_000_000)
    m = re.search(r"(\d+(?:\.\d+)?)백만", t)
    if m:
        return int(float(m.group(1)) * 1_000_000)
    m = re.search(r"(\d+(?:\.\d+)?)만(?:원)?", t)
    if m:
        return int(float(m.group(1)) * 10_000)
    # 5 million won / 5m krw / ₩5000000 / 500000원
    tl = (msg or "").lower()
    m = re.search(r"([\d,.]+)\s*(?:million|mil|m)\s*(?:won|krw)", tl)
    if m:
        return int(float(m.group(1).replace(",", "")) * 1_000_000)
    m = re.search(r"([\d,.]+)\s*k\s*(?:won|krw)", tl)
    if m:
        return int(float(m.group(1).replace(",", "")) * 1_000)
    m = re.search(r"(?:₩|krw)\s*([\d,]{4,})", msg or "", re.I) or re.search(r"([\d,]{6,})\s*원", msg or "")
    if m:
        return int(m.group(1).replace(",", ""))
    return None


_BUDGET_CUES = ("자금", "예산", "투자금", "가지고", "보유금", "budget", "capital", "i have")
_BUY_CTX = ("사", "살", "매수", "단타", "투자", "담", "들어가", "buy", "scalp", "trade", "invest")


def stated_budget(msg: str) -> Optional[int]:
    """A budget the user actually STATED — not just any price mentioned. A bare amount
    ("10만원 가면 팔까?" = a price target) must not overwrite the saved budget, so an
    amount counts only with a budget cue (자금/예산/budget…) or the "<amount>으로 +
    buy-context" pattern ("500만원으로 살까")."""
    amt = parse_budget(msg)
    if not amt:
        return None
    t = (msg or "").lower()
    if any(c in t for c in _BUDGET_CUES):
        return amt
    if "으로" in t and any(c in t for c in _BUY_CTX):
        return amt
    return None


def remember_budget(db, user_key: str, budget: int) -> None:
    try:
        _ensure(db)
        db.execute(text(
            "INSERT INTO user_trade_budget (user_key, budget) VALUES (:k,:b) "
            "ON CONFLICT (user_key) DO UPDATE SET budget=EXCLUDED.budget, updated=now()"),
            {"k": user_key, "b": int(budget)})
        db.commit()
    except Exception as e:
        db.rollback(); log.warning(f"position_size remember: {str(e)[:120]}")


def recall_budget(db, user_key: str) -> Optional[int]:
    try:
        _ensure(db)
        row = db.execute(text(
            "SELECT budget FROM user_trade_budget WHERE user_key=:k"), {"k": user_key}).fetchone()
        return int(row.budget) if row else None
    except Exception:
        return None


def size_position(budget: int, entry: float, stop: Optional[float],
                  risk_pct: float = RISK_PCT_PER_TRADE) -> Optional[dict[str, Any]]:
    """Share count = min(budget cap, risk cap). None if the budget can't buy 1 share."""
    if not budget or not entry or entry <= 0:
        return None
    by_budget = int(budget // entry)
    if by_budget < 1:
        return None
    shares = by_budget
    capped_by = "budget"
    if stop and 0 < stop < entry:
        risk_per_share = entry - stop
        by_risk = int((budget * risk_pct / 100.0) // risk_per_share)
        if 1 <= by_risk < shares:
            shares, capped_by = by_risk, "risk"
        elif by_risk < 1:
            shares, capped_by = 1, "risk"          # even 1 share exceeds the risk rule — say so
    cost = int(shares * entry)
    risk_won = int(shares * (entry - stop)) if stop and stop < entry else None
    return {"shares": shares, "cost": cost, "budget": int(budget), "capped_by": capped_by,
            "budget_use_pct": round(cost / budget * 100, 1),
            "risk_won": risk_won,
            "risk_pct_of_budget": round(risk_won / budget * 100, 2) if risk_won else None}


def sizing_line(db, *, transcript: str, user_key: Optional[str], lang: Optional[str],
                entry: Optional[float], stop: Optional[float]) -> Optional[str]:
    """One appendable answer line: budget from the message (remembered) or from memory.
    Returns a hint line when no budget is known, so the user learns to state it."""
    en = str(lang or "").lower().startswith("en")
    stated = stated_budget(transcript)
    # No real user identity → use only what THIS message states; never remember/recall a
    # shared row (users on the same agent must not inherit each other's capital).
    if user_key:
        if stated:
            remember_budget(db, user_key, stated)
        budget = stated or recall_budget(db, user_key)
    else:
        budget = stated
    if not budget:
        return ("\n\n💰 Tell me your budget (e.g. \"with 5 million won\") and I'll size the position too."
                if en else
                "\n\n💰 자금 규모를 알려주시면 수량까지 계산해 드립니다 (예: \"500만원으로\").")
    if not entry:
        return None
    s = size_position(budget, entry, stop)
    if not s:
        return (f"\n\n💰 Budget ₩{budget:,} can't buy 1 share at ₩{entry:,.0f}."
                if en else f"\n\n💰 자금 {budget:,}원으로는 1주({entry:,.0f}원)를 살 수 없습니다.")
    if en:
        line = (f"\n\n💰 Sizing (budget ₩{s['budget']:,}): **{s['shares']:,} shares** ≈ ₩{s['cost']:,} "
                f"({s['budget_use_pct']}% of budget)")
        if s["risk_won"] is not None:
            line += (f" · if stopped out ≈ −₩{s['risk_won']:,} ({s['risk_pct_of_budget']}% of capital"
                     f"{', 1%-risk rule' if s['capped_by'] == 'risk' else ''})")
        return line + "."
    line = (f"\n\n💰 수량 (자금 {s['budget']:,}원 기준): **{s['shares']:,}주** ≈ {s['cost']:,}원 "
            f"(자금의 {s['budget_use_pct']}%)")
    if s["risk_won"] is not None:
        line += (f" · 손절 시 손실 ≈ −{s['risk_won']:,}원 (자금의 {s['risk_pct_of_budget']}%"
                 f"{', 1회 리스크 1% 룰 적용' if s['capped_by'] == 'risk' else ''})")
    return line + "."
