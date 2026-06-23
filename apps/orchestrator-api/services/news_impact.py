"""news_impact — score WHICH news actually matters for short-term moves.

Collecting news + sentiment isn't enough: most headlines are noise. A day-trader
only cares about news that *moves price*. This engine does two things:

  1. classify(title) -> news TYPE (실적/수주/유증/자사주/M&A/정책/증설/리포트/...)
  2. score_impact(type, sentiment) -> 0..1 impact + signed direction

Impact starts from a per-type prior (earnings surprises, large contracts and
capacity expansions move stocks; analyst target tweaks barely do), and is then
refined per-stock by the *realized* price reaction we observe in raw_news ⨝ prices
as history accumulates (learn_priors). So the same pipeline that's a sensible
rule today becomes data-driven over time — no rebuild needed.

Used by trading_brief to surface "effective news only" and hide the noise.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from sqlalchemy import text

# news TYPE -> (regex keywords, base impact 0..1, default direction sign)
# direction: +1 bullish bias, -1 bearish bias, 0 depends on sentiment
NEWS_TYPES: list[tuple[str, str, float, int]] = [
    ("실적",     r"실적|어닝|영업이익|순이익|매출|컨센서스|서프라이즈|earnings|guidance", 0.85, 0),
    ("수주/계약", r"수주|계약|공급|납품|수출|체결|order|contract|deal|supply", 0.80, +1),
    ("증설/투자", r"증설|설비|투자|공장|capacity|증산|라인|fab|착공", 0.65, +1),
    ("M&A",      r"인수|합병|매각|지분|M&A|acquisition|merger|stake", 0.70, 0),
    ("유상증자",  r"유상증자|전환사채|CB|BW|신주|희석|dilution|rights\s*issue", 0.75, -1),
    ("자사주",    r"자사주|자기주식|소각|buyback|repurchase", 0.60, +1),
    ("배당",     r"배당|dividend|주주환원|payout", 0.45, +1),
    ("정책/규제", r"규제|정책|관세|제재|보조금|법안|tariff|sanction|subsidy|policy", 0.65, 0),
    ("리포트",    r"목표가|투자의견|매수의견|상향|하향|리포트|analyst|target\s*price|upgrade|downgrade", 0.40, 0),
    ("외국인",    r"외국인|기관|순매수|순매도|수급|foreign|institution", 0.50, 0),
    ("소송/사고", r"소송|리콜|화재|사고|결함|lawsuit|recall|defect|probe", 0.55, -1),
]
_COMPILED = [(name, re.compile(pat, re.I), imp, d) for name, pat, imp, d in NEWS_TYPES]
DEFAULT = ("일반", 0.20, 0)


def classify(title: str, snippet: str = "") -> tuple[str, float, int]:
    """-> (type_name, base_impact, default_dir). First matching type wins."""
    txt = f"{title or ''} {snippet or ''}"
    for name, rx, imp, d in _COMPILED:
        if rx.search(txt):
            return name, imp, d
    return DEFAULT


def score(title: str, snippet: str, sentiment: Optional[float],
          priors: Optional[dict[str, float]] = None) -> dict[str, Any]:
    """Full impact score for one news item.
    sentiment: -1..+1 (from the collector). priors: learned per-type impact override."""
    name, base, ddir = classify(title, snippet)
    if priors and name in priors:
        base = priors[name]
    s = float(sentiment) if sentiment is not None else 0.0   # DB returns Decimal
    # direction: type bias if it has one, else follow sentiment
    direction = ddir if ddir != 0 else (1 if s > 0.15 else -1 if s < -0.15 else 0)
    # impact grows with conviction (|sentiment|) but never below the type's floor
    impact = round(min(1.0, base * (0.6 + 0.4 * min(abs(s) * 2, 1.0))), 3)
    return {"type": name, "impact": impact, "direction": direction,
            "sentiment": round(s, 3)}


def learn_priors(db, lookback_days: int = 120) -> dict[str, float]:
    """Refine per-type impact from REALIZED moves: for past news, how big was the
    |next-day price move| of that ticker? Avg |move| per type -> data-driven impact.
    Returns {} if not enough history (falls back to rule priors)."""
    rows = db.execute(text(
        "SELECT n.title, n.snippet, n.ticker, n.ts::date AS d "
        "FROM raw_news n WHERE n.ticker IS NOT NULL "
        "AND n.ts > now() - (:days || ' days')::interval"), {"days": lookback_days}).fetchall()
    if len(rows) < 60:
        return {}
    from collections import defaultdict
    moves: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        nm, _, _ = classify(r.title or "", r.snippet or "")
        mv = db.execute(text(
            "SELECT abs((lead_close/close)-1) AS m FROM ("
            "  SELECT close, lead(close,1) OVER (ORDER BY date) AS lead_close "
            "  FROM raw_daily_prices WHERE ticker=:t AND date>=:d ORDER BY date LIMIT 2) q "
            "WHERE lead_close IS NOT NULL LIMIT 1"), {"t": r.ticker, "d": r.d}).first()
        if mv and mv[0] is not None:
            moves[nm].append(float(mv[0]))
    if not moves:
        return {}
    # normalize avg |move| to a 0..1 impact (5% move -> ~1.0)
    return {nm: round(min(1.0, (sum(v) / len(v)) / 0.05), 3)
            for nm, v in moves.items() if len(v) >= 3}


def effective_news(db, ticker: Optional[str] = None, limit: int = 8,
                   min_impact: float = 0.35, lookback_days: int = 5) -> list[dict]:
    """Recent, HIGH-impact news only (noise hidden). Market-wide if ticker is None."""
    priors = learn_priors(db)
    q = ("SELECT ticker, ts, source, url, title, snippet, sentiment FROM raw_news "
         "WHERE ts > now() - (:days || ' days')::interval ")
    params: dict[str, Any] = {"days": lookback_days}
    if ticker:
        q += "AND ticker=:t "
        params["t"] = ticker
    q += "ORDER BY ts DESC LIMIT 200"
    out = []
    for r in db.execute(text(q), params):
        sc = score(r.title or "", r.snippet or "", r.sentiment, priors)
        if sc["impact"] < min_impact:
            continue
        out.append({
            "ticker": r.ticker, "ts": str(r.ts), "source": r.source, "url": r.url,
            "title": r.title, **sc,
        })
    # rank by impact, then recency (already DESC)
    out.sort(key=lambda x: x["impact"], reverse=True)
    return out[:limit]
