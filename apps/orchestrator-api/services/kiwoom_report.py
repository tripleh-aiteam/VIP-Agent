"""
kiwoom_report — daily Kiwoom market-analysis report, generated after the US
market close (~6:30 AM KST). Pulls real daily OHLCV from the Stock Advisor
backend (Kiwoom for KR tickers, Yahoo for US) for a fixed watchlist, builds a
data table, and has the LLM compose a structured bilingual (EN/KO) report:
  1. General Overview
  2. Market Data (real table)
  3. Detailed Analysis
  4. Risks & Watch-items
  5. Opportunities
  6. Recommended Actions (buy/sell table with reasons)

Saved as report_type='kiwoom_report' (period='daily'); also sent to Telegram.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import httpx

from services.logger import log

_BACKEND = (os.environ.get("STOCK_BACKEND_URL")
            or "https://stock-advisor-agent-9qwi.onrender.com").rstrip("/")

# (ticker, KO name, EN name, market, unit, etf-tracking note)
KIWOOM_TICKERS: list[dict[str, Any]] = [
    {"t": "000660", "ko": "SK하이닉스", "en": "SK Hynix", "mkt": "KR", "etf": "KODEX 200"},
    {"t": "005930", "ko": "삼성전자", "en": "Samsung Electronics", "mkt": "KR", "etf": "KODEX 200"},
    {"t": "AMD", "ko": "AMD", "en": "AMD", "mkt": "US", "etf": "SOXX / SMH"},
    {"t": "MU", "ko": "마이크론", "en": "Micron Technology", "mkt": "US", "etf": "SOXX / SMH"},
    {"t": "SOXX", "ko": "필라델피아 반도체(SOX)", "en": "Philadelphia Semi (SOX→SOXX)", "mkt": "US", "etf": "(ETF)"},
    {"t": "SNDK", "ko": "샌디스크", "en": "SanDisk", "mkt": "US", "etf": "SOXX / SMH"},
    {"t": "AVGO", "ko": "브로드컴", "en": "Broadcom", "mkt": "US", "etf": "SOXX / SMH"},
    {"t": "017670", "ko": "SK텔레콤", "en": "SK Telecom", "mkt": "KR", "etf": "KODEX 200"},
    {"t": "018260", "ko": "삼성SDS", "en": "Samsung SDS", "mkt": "KR", "etf": "KODEX 200"},
    {"t": "035420", "ko": "NAVER", "en": "Naver", "mkt": "KR", "etf": "KODEX 200"},
    {"t": "069500", "ko": "KODEX 200", "en": "Kodex 200 ETF", "mkt": "KR", "etf": "(ETF)"},
]


def _fmt_price(v: float | None, mkt: str) -> str:
    if v is None:
        return "—"
    return f"${v:,.2f}" if mkt == "US" else f"{v:,.0f}원"


def _fmt_chg(c: float | None) -> str:
    if c is None:
        return "—"
    arrow = "▲" if c > 0 else ("▼" if c < 0 else "—")
    return f"{arrow}{abs(c):.2f}%"


def _fetch_daily(spec: dict) -> dict:
    """Fetch the latest daily candle + previous close for one ticker."""
    row = {**spec, "open": None, "close": None, "high": None, "low": None,
           "volume": None, "change_pct": None, "ok": False}
    try:
        with httpx.Client(timeout=15) as c:
            r = c.get(f"{_BACKEND}/intraday/daily-chart", params={"ticker": spec["t"], "days": 20})
        if r.status_code != 200:
            return row
        candles = (r.json() or {}).get("candles") or []
        if not candles:
            return row
        last = candles[-1]
        prev_close = candles[-2].get("close") if len(candles) >= 2 else None
        close = last.get("close")
        chg = ((close - prev_close) / prev_close * 100) if (prev_close and close) else None
        row.update({
            "open": last.get("open"), "close": close, "high": last.get("high"),
            "low": last.get("low"), "volume": last.get("volume"),
            "prev_close": prev_close, "change_pct": chg, "date": last.get("date"),
            "ma5": last.get("ma5"), "ma20": last.get("ma20"), "ma60": last.get("ma60"),
            "ok": True,
        })
    except Exception as e:
        log.warning(f"kiwoom fetch {spec['t']} failed: {e}")
    return row


def _gather() -> list[dict]:
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as ex:
        return list(ex.map(_fetch_daily, KIWOOM_TICKERS))


def _build_table(rows: list[dict], ko: bool) -> str:
    """Deterministic Markdown data table with the REAL numbers."""
    if ko:
        head = ("| 종목 | 시가 | 종가 | 대비·등락 | 거래량 | 공매도 | ETF 추적 |\n"
                "|---|---|---|---|---|---|---|")
    else:
        head = ("| Stock | Open | Close | Change | Volume | Short(공매도) | ETF |\n"
                "|---|---|---|---|---|---|---|")
    lines = [head]
    for r in rows:
        name = r["ko"] if ko else r["en"]
        vol = f"{int(r['volume']):,}" if r.get("volume") is not None else "—"
        lines.append(
            f"| {name} | {_fmt_price(r.get('open'), r['mkt'])} | "
            f"{_fmt_price(r.get('close'), r['mkt'])} | {_fmt_chg(r.get('change_pct'))} | "
            f"{vol} | N/A | {r['etf']} |"
        )
    return "\n".join(lines)


def _facts(rows: list[dict]) -> str:
    out = []
    for r in rows:
        out.append(
            f"{r['en']} ({r['ko']}, {r['t']}, {r['mkt']}): open={r.get('open')}, "
            f"close={r.get('close')}, change_pct={r.get('change_pct')}, "
            f"volume={r.get('volume')}, ma5={r.get('ma5')}, ma20={r.get('ma20')}, ma60={r.get('ma60')}"
        )
    return "\n".join(out)


def build_kiwoom_report(db, trace_id: str) -> dict:
    """Build the daily Kiwoom report (real data table + LLM narrative, EN+KO)."""
    rows = _gather()
    ok_rows = [r for r in rows if r.get("ok")]
    kst_date = datetime.utcnow().strftime("%Y-%m-%d")
    table_en, table_ko = _build_table(rows, ko=False), _build_table(rows, ko=True)

    # One-line summary (deterministic fallback)
    movers = sorted([r for r in ok_rows if r.get("change_pct") is not None],
                    key=lambda r: r["change_pct"])
    sum_en = (f"Kiwoom daily ({len(ok_rows)}/{len(rows)} tickers): "
              + (f"weakest {movers[0]['en']} {_fmt_chg(movers[0]['change_pct'])}, "
                 f"strongest {movers[-1]['en']} {_fmt_chg(movers[-1]['change_pct'])}."
                 if movers else "data limited."))
    sum_ko = (f"키움 일일 ({len(ok_rows)}/{len(rows)} 종목): "
              + (f"최약 {movers[0]['ko']} {_fmt_chg(movers[0]['change_pct'])}, "
                 f"최강 {movers[-1]['ko']} {_fmt_chg(movers[-1]['change_pct'])}."
                 if movers else "데이터 제한."))

    detail_en = detail_ko = ""
    try:
        from services.llm_client import chat_completion_sync
        sysmsg = (
            "You are Kiwoom's market analyst writing the DAILY report after the US "
            "market close (~6:30 AM KST). Use ONLY the data provided — NEVER invent "
            "numbers. Note: 공매도 (short-selling) data is not available (mark N/A). "
            "Produce the report in this EXACT section structure:\n"
            "## 1. General Overview\n## 2. Market Data\n## 3. Detailed Analysis\n"
            "## 4. Risks & Watch-items\n## 5. Opportunities\n## 6. Recommended Actions\n\n"
            "Rules: In section 2, insert the provided data table VERBATIM. In section "
            "3, interpret by sector (KR memory/semis, US semis, telecom/IT, ETFs) using "
            "the real change%/volume. In section 6, give a Markdown table with columns "
            "| Stock | Action | Reason | where Action is BUY / SELL / HOLD with a concrete "
            "reason from the data. Write the SAME report TWICE: first English (use the "
            "ENGLISH table), then natural Korean (use the KOREAN table). "
            "Output EXACTLY:\n===EN===\n<english md>\n===KO===\n<korean md>"
        )
        user = (f"Date (KST): {kst_date}\n\nENGLISH TABLE:\n{table_en}\n\n"
                f"KOREAN TABLE:\n{table_ko}\n\nRAW DATA:\n{_facts(rows)}")
        out = chat_completion_sync(
            system_prompt=sysmsg, messages=[{"role": "user", "content": user[:4000]}],
            max_tokens=2600, temperature=0.5, model="groq-llama-3.3-70b") or ""
        bad = (not out.strip()) or out.lstrip().startswith(("[LLM unavailable]", "[server error]"))
        if not bad:
            if "===KO===" in out:
                a, b = out.split("===KO===", 1)
                detail_en = a.replace("===EN===", "").strip()
                detail_ko = b.strip()
            else:
                detail_en = out.replace("===EN===", "").strip()
    except Exception as e:
        log.warning(f"kiwoom LLM compose failed: {e}")

    # Fallback: assemble a structured report from the table if the LLM is down.
    if not detail_en:
        detail_en = (f"# Kiwoom Daily Report\n*{kst_date} (after US close)*\n\n"
                     f"## 1. General Overview\n{sum_en}\n\n## 2. Market Data\n{table_en}\n\n"
                     f"## 3. Detailed Analysis\nSee the table above for open/close/change/volume.\n\n"
                     f"## 4. Risks & Watch-items\n- 공매도 data not available (N/A).\n\n"
                     f"## 5. Opportunities\n- Review the strongest/weakest movers above.\n\n"
                     f"## 6. Recommended Actions\n| Stock | Action | Reason |\n|---|---|---|\n"
                     f"| — | HOLD | LLM unavailable — manual review recommended |")
    if not detail_ko:
        detail_ko = detail_en

    return {
        "agent_type": "kiwoom", "name": "Kiwoom Market Analysis", "emoji": "📈",
        "status": "ok" if ok_rows else "partial",
        "summary_en": sum_en, "summary_ko": sum_ko,
        "detail_en": detail_en, "detail_ko": detail_ko,
        "table_en": table_en, "table_ko": table_ko,
        "rows": rows, "source": "Kiwoom / Stock Advisor (live daily OHLCV)",
    }
