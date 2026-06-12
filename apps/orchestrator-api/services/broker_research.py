"""
broker_research — Korean securities-firm analyst calls for the Recommendation
report. Pulls the brokerage CONSENSUS (목표주가 target price + 투자의견 rating)
and the most recent broker research reports (firm + title + date) for each KR
watchlist ticker, straight from Naver's public mobile JSON API (free, no key).

This is REAL published analyst data — target prices and report titles from
Kiwoom / Samsung / Mirae Asset / Yuanta / Hanwha / Korea Investment & others —
so the Recommendation report carries professional calls, not just LLM opinion.
"""

from __future__ import annotations

import httpx

from services.logger import log

_BASE = "https://m.stock.naver.com/api/stock"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Referer": "https://m.stock.naver.com/",
}

# The six firms the report should highlight when they appear in recent reports.
HIGHLIGHT_FIRMS = ["키움", "삼성증권", "미래에셋", "유안타", "한화", "한국투자"]


def _opinion_label(recomm_mean: float | None) -> str:
    """Naver/FnGuide 투자의견 scale: 5=강력매수 … 1=강력매도 (higher = more bullish)."""
    if recomm_mean is None:
        return "—"
    if recomm_mean >= 4.5:
        return "강력매수 (Strong Buy)"
    if recomm_mean >= 3.5:
        return "매수 (Buy)"
    if recomm_mean >= 2.5:
        return "중립 (Hold)"
    if recomm_mean >= 1.5:
        return "매도 (Sell)"
    return "강력매도 (Strong Sell)"


def _num(s) -> float | None:
    try:
        return float(str(s).replace(",", "").strip())
    except Exception:
        return None


def fetch_consensus(code: str, name: str = "", current_price: float | None = None) -> dict | None:
    """Consensus + recent broker reports for one KR ticker. None on failure.
    `current_price` (KRW) lets us compute upside vs the consensus target."""
    try:
        r = httpx.get(f"{_BASE}/{code}/integration", headers=_HEADERS, timeout=15)
        if r.status_code != 200:
            return None
        d = r.json() or {}
    except Exception as e:
        log.warning(f"broker_research {code}: {str(e)[:100]}")
        return None

    ci = d.get("consensusInfo") or {}
    target = _num(ci.get("priceTargetMean"))
    recomm = _num(ci.get("recommMean"))
    upside = ((target - current_price) / current_price * 100) if (target and current_price) else None

    reports = []
    for x in (d.get("researches") or [])[:6]:
        wdt = x.get("wdt") or ""
        date = f"{wdt[:4]}-{wdt[4:6]}-{wdt[6:8]}" if len(wdt) == 8 else wdt
        reports.append({"broker": x.get("bnm") or "", "title": x.get("tit") or "", "date": date})

    return {
        "code": code, "name": name or (d.get("stockName") or ""),
        "target": target, "recomm_mean": recomm, "opinion": _opinion_label(recomm),
        "upside_pct": upside, "reports": reports,
    }


def gather_kr_consensus(tickers: list[dict]) -> list[dict]:
    """tickers: rows with t/ko/mkt/close (close in native KRW for KR). Returns
    consensus dicts for KR tickers only (Korean brokers cover KR equities)."""
    out = []
    for t in tickers:
        if t.get("mkt") != "KR":
            continue
        c = fetch_consensus(t["t"], t.get("ko", ""), t.get("close"))
        if c:
            out.append(c)
    return out


def consensus_facts(rows: list[dict], ko: bool = True) -> str:
    """Markdown block of real analyst consensus for the LLM to ground the
    '증권사 추천' paragraph. Empty string if nothing usable."""
    rows = [r for r in rows if r.get("target") or r.get("reports")]
    if not rows:
        return ""
    lines = ["## 증권사 컨센서스 (Korean Securities Firms — analyst consensus)" if ko
             else "## Korean Securities Firms — analyst consensus"]
    for r in rows:
        tgt = f"{r['target']:,.0f}원" if r.get("target") else "—"
        up = f" (상승여력 {r['upside_pct']:+.0f}%)" if r.get("upside_pct") is not None else ""
        lines.append(f"\n### {r['name']} ({r['code']})")
        lines.append(f"- 컨센서스 목표주가: {tgt}{up} · 투자의견: {r['opinion']}")
        if r.get("reports"):
            lines.append("- 최근 증권사 리포트:")
            for rep in r["reports"][:5]:
                if rep["title"]:
                    lines.append(f"  - [{rep['date']}] {rep['broker']}: {rep['title']}")
    return "\n".join(lines)
