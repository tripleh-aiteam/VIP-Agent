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

import html as _html
import os
import re
import unicodedata
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


def _won(v: float | None) -> str:
    """Format a value as Korean Won (all instruments shown in KRW)."""
    if v is None:
        return "—"
    return f"{v:,.0f}원"


def _usdkrw_rate() -> float:
    """Current USD/KRW from the live market snapshot (fallback 1,530)."""
    try:
        with httpx.Client(timeout=12) as c:
            r = c.get(f"{_BACKEND}/market/snapshots", params={"limit": 1})
        data = r.json() if r.status_code == 200 else None
        snap = None
        if isinstance(data, list) and data:
            snap = data[0]
        elif isinstance(data, dict):
            snap = data if "prices" in data else (data.get("items") or [{}])[0]
        rate = float((snap or {}).get("prices", {}).get("usdkrw") or 0)
        return rate if rate > 500 else 1530.0
    except Exception:
        return 1530.0


def _fmt_chg(c: float | None) -> str:
    if c is None:
        return "—"
    arrow = "▲" if c > 0 else ("▼" if c < 0 else "—")
    return f"{arrow}{abs(c):.2f}%"


def _fetch_daily(spec: dict) -> dict:
    """Fetch the latest FULLY-SETTLED daily candle + previous close for one ticker.
    Retries a few times — the backend's Yahoo/Kiwoom fetch is intermittently
    slow/empty, which was leaving blank rows.

    To report the FINALIZED official close (not a provisional/intraday or
    after-hours figure), we drop any candle dated today (KST) — that session may
    still be forming/unsettled — and use the most recent prior, settled session."""
    import time as _t
    from services.kst import kst_date as _kst_date, kst_now
    today = _kst_date()
    kst_hour = kst_now().hour
    row = {**spec, "open": None, "close": None, "high": None, "low": None,
           "volume": None, "change_pct": None, "ok": False}
    for attempt in range(3):
        try:
            with httpx.Client(timeout=30) as c:
                r = c.get(f"{_BACKEND}/intraday/daily-chart",
                          params={"ticker": spec["t"], "days": 20})
            candles = (r.json() or {}).get("candles") or [] if r.status_code == 200 else []
            # Use the latest candle that has a real (non-null) finalized close.
            # Today's candle is only PROVISIONAL while the KR market is still
            # trading (before ~16:00 KST) — drop it then so we don't show an
            # intraday price. After the close (or a pre-open morning run, when no
            # today candle exists yet), today's settled close IS used.
            valid = [c for c in candles if c.get("close") is not None]
            if kst_hour < 16:
                pre = [c for c in valid if (c.get("date") or "0000-00-00") < today]
                valid = pre or valid
            candles = valid or candles
            if candles:
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
                return row
        except Exception as e:
            log.warning(f"kiwoom fetch {spec['t']} attempt {attempt+1} failed: {e}")
        if attempt < 2:
            _t.sleep(1.5)
    log.warning(f"kiwoom fetch {spec['t']}: no data after retries")
    return row


def _gather() -> list[dict]:
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=5) as ex:
        return list(ex.map(_fetch_daily, KIWOOM_TICKERS))


def _build_table(rows: list[dict], ko: bool) -> str:
    """Deterministic Markdown data table with the REAL numbers — all in KRW."""
    if ko:
        head = ("| 종목 | 시가 | 종가 | 대비·등락 | 거래량 |\n"
                "|---|---|---|---|---|")
    else:
        head = ("| Stock | Open (KRW) | Close (KRW) | Change | Volume |\n"
                "|---|---|---|---|---|")
    lines = [head]
    for r in rows:
        name = r["ko"] if ko else r["en"]
        vol = f"{int(r['volume']):,}" if r.get("volume") is not None else "—"
        lines.append(
            f"| {name} | {_won(r.get('open_krw'))} | {_won(r.get('close_krw'))} | "
            f"{_fmt_chg(r.get('change_pct'))} | {vol} |"
        )
    return "\n".join(lines)


def _facts(rows: list[dict]) -> str:
    """Rich per-ticker facts (KRW prices + intraday range + MA trend structure)
    so the LLM can write a deep, name-by-name Detailed Analysis."""
    out = []
    for r in rows:
        if not r.get("ok"):
            out.append(f"- {r['en']} ({r['ko']}, {r['t']}, {r['mkt']}): NO DATA this session.")
            continue

        def pa(v):  # close vs a moving average, in %
            try:
                return f"{(r['close'] - v) / v * 100:+.1f}%" if (v and r.get('close')) else "n/a"
            except Exception:
                return "n/a"

        rng = "n/a"
        try:
            if r.get("high") and r.get("low") and r.get("open"):
                rng = f"{(r['high'] - r['low']) / r['open'] * 100:.1f}%"
        except Exception:
            pass
        chg = r.get("change_pct")
        chg_s = f"{chg:+.2f}%" if chg is not None else "n/a"

        # Trend structure from the moving-average stack.
        ma5, ma20, ma60, cl = r.get("ma5"), r.get("ma20"), r.get("ma60"), r.get("close")
        trend = "n/a"
        if cl and ma5 and ma20 and ma60:
            if cl > ma5 > ma20 > ma60:
                trend = "full uptrend (close>MA5>MA20>MA60, bullish stack)"
            elif cl < ma5 < ma20 < ma60:
                trend = "full downtrend (close<MA5<MA20<MA60, bearish stack)"
            elif cl > ma60 > ma20 and cl < ma5:
                trend = "pullback within an uptrend (above MA60, below MA5)"
            elif cl < ma60 and cl > ma5 > ma20:
                trend = "early bottoming (reclaiming MA5/MA20, still under MA60)"
            elif cl > ma60:
                trend = "above long-term MA60 (longer-term bullish, short-term mixed)"
            else:
                trend = "below long-term MA60 (longer-term bearish, short-term mixed)"

        vol = f"{int(r['volume']):,}" if r.get("volume") is not None else "n/a"
        out.append(
            f"- {r['en']} ({r['ko']}, {r['t']}, {r['mkt']}, ETF:{r['etf']}): "
            f"open={_won(r.get('open_krw'))}, close={_won(r.get('close_krw'))}, "
            f"high={_won(r.get('high_krw'))}, low={_won(r.get('low_krw'))}, "
            f"intraday_range={rng}, change_vs_prev_close={chg_s}, volume={vol}; "
            f"close_vs_MA5={pa(ma5)}, close_vs_MA20={pa(ma20)}, close_vs_MA60={pa(ma60)}; "
            f"trend={trend}."
        )
    return "\n".join(out)


def gather_priced_rows() -> tuple[list[dict], str, str, float]:
    """Shared price layer for any report: fetch the 11 tickers, convert all
    prices to KRW, and build the EN/KO Markdown tables. Returns
    (rows, table_en, table_ko, usdkrw_rate)."""
    rows = _gather()
    rate = _usdkrw_rate()
    for r in rows:
        mult = rate if r["mkt"] == "US" else 1.0
        for k in ("open", "close", "high", "low"):
            r[f"{k}_krw"] = (r[k] * mult) if r.get(k) is not None else None
    return rows, _build_table(rows, ko=False), _build_table(rows, ko=True), rate


def build_kiwoom_report(db, trace_id: str) -> dict:
    """Build the daily Kiwoom report (real data table + LLM narrative, EN+KO)."""
    rows = _gather()
    # Convert ALL prices to Korean Won (US tickers × USD/KRW; KR as-is).
    rate = _usdkrw_rate()
    for r in rows:
        mult = rate if r["mkt"] == "US" else 1.0
        r["open_krw"] = (r["open"] * mult) if r.get("open") is not None else None
        r["close_krw"] = (r["close"] * mult) if r.get("close") is not None else None
        r["high_krw"] = (r["high"] * mult) if r.get("high") is not None else None
        r["low_krw"] = (r["low"] * mult) if r.get("low") is not None else None
    ok_rows = [r for r in rows if r.get("ok")]
    from services.kst import kst_date as _kst_date
    kst_date = _kst_date()
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
            "You are Kiwoom's senior market analyst writing the DAILY report after "
            "the US market close (~6:30 AM KST). Use ONLY the data provided — NEVER "
            "invent numbers. ALL prices are in Korean Won (KRW). Produce the report "
            "in this EXACT section structure:\n"
            "## 1. General Overview\n## 2. Market Data\n## 3. Detailed Analysis\n"
            "## 4. Risks & Watch-items\n## 5. Opportunities\n## 6. Recommended Actions\n\n"
            "Rules:\n"
            "- Section 2: insert the provided data table VERBATIM.\n"
            "- Section 3 (DETAILED ANALYSIS — this is the CORE of the report and MUST "
            "be long and substantive: AT LEAST ~750 words. Structure it as:\n"
            "  (a) a sub-heading '### Memory & KR Semiconductors' with a DEDICATED "
            "paragraph for SK Hynix AND for Samsung Electronics;\n"
            "  (b) '### US Semiconductors' with a dedicated paragraph for AMD, Micron, "
            "Broadcom, SanDisk, and the SOXX (Philadelphia Semi) index;\n"
            "  (c) '### Telecom & IT' with a dedicated paragraph for SK Telecom, "
            "Samsung SDS, and Naver;\n"
            "  (d) '### Broad Market — KODEX 200' on the ETF;\n"
            "  (e) '### Cross-Market Read' tying US semis → KR memory read-through and "
            "the overall risk tone.\n"
            "For EVERY name discuss ALL of: change vs prior close, the intraday range "
            "(high/low — wide=volatile/contested, narrow=quiet), the VOLUME (heavy = "
            "conviction/institutional, light = low participation), and the TREND "
            "STRUCTURE from the MA stack (close vs MA5/MA20/MA60 — above=uptrend, "
            "below=downtrend; explicitly name pullbacks-in-uptrend, bottoming, bullish/"
            "bearish stacks, and golden/dead-cross setups). Quote the REAL KRW prices "
            "and percentages throughout — never be vague, never invent.\n"
            "- Section 6: FIRST a Markdown table | Stock | Action | Reason | where "
            "Action is BUY / SELL / HOLD; THEN, AFTER the table, add a '### Rationale' "
            "subsection with a paragraph per recommendation explaining IN DETAIL why "
            "(the technical + momentum reasoning behind each BUY / SELL / HOLD).\n"
            "Output ONLY the finished English Markdown report — no preamble, no "
            "placeholders, no notes about a translation."
        )
        user = (f"Date (KST): {kst_date} · USD/KRW used for US tickers: {rate:,.0f}\n\n"
                f"DATA TABLE (insert verbatim in Section 2):\n{table_en}\n\n"
                f"DATA + TECHNICALS:\n{_facts(rows)}")
        out = chat_completion_sync(
            system_prompt=sysmsg, messages=[{"role": "user", "content": user[:9000]}],
            max_tokens=7000, temperature=0.5, model="groq-llama-3.3-70b") or ""
        bad = (not out.strip()) or out.lstrip().startswith(("[LLM unavailable]", "[server error]"))
        if not bad:
            detail_en = out.replace("===EN===", "").strip()
            # Korean: a DEDICATED translation call (the single-shot "write it twice"
            # approach made the model stub the KO half). Translate the FULL English
            # report and swap in the ready-made Korean data table.
            try:
                ko_sys = (
                    "You are a professional Korean financial translator. Translate the "
                    "ENTIRE English market report below into natural, professional "
                    "Korean (존댓말) for an investor audience. Rules:\n"
                    "- Translate EVERYTHING — every section and paragraph. NEVER "
                    "abbreviate, summarise, or write a placeholder like '(same as "
                    "English)'. The Korean must be as long as the English.\n"
                    "- Preserve ALL Markdown structure, heading levels, and tables.\n"
                    "- Keep every number, %, 원 amount, ticker code and MA value "
                    "IDENTICAL — translate only the words.\n"
                    "- Replace the Section 2 data table with this EXACT Korean table:\n"
                    f"{table_ko}\n"
                    "Output ONLY the Korean Markdown report."
                )
                ko_out = chat_completion_sync(
                    system_prompt=ko_sys,
                    messages=[{"role": "user", "content": detail_en[:14000]}],
                    max_tokens=7000, temperature=0.3, model="groq-llama-3.3-70b") or ""
                ko_bad = ((not ko_out.strip())
                          or ko_out.lstrip().startswith(("[LLM unavailable]", "[server error]"))
                          or len(ko_out.strip()) < 400)
                if not ko_bad:
                    detail_ko = ko_out.strip()
            except Exception as e:
                log.warning(f"kiwoom KO translation failed: {e}")
    except Exception as e:
        log.warning(f"kiwoom LLM compose failed: {e}")

    # Fallback: assemble a structured report from the table if the LLM is down.
    if not detail_en:
        detail_en = (f"# Kiwoom Daily Report\n*{kst_date} (after US close)*\n\n"
                     f"## 1. General Overview\n{sum_en}\n\n## 2. Market Data\n{table_en}\n\n"
                     f"## 3. Detailed Analysis\nSee the table above for open/close/change/volume.\n\n"
                     f"## 4. Risks & Watch-items\n- Review the weakest movers above.\n\n"
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


# ---------------------------------------------------------------------------
# Telegram rendering — send the SAME full report (incl. table) as the dashboard
# ---------------------------------------------------------------------------

def _dw(s: str) -> int:
    """Display width: CJK glyphs occupy ~2 monospace cells."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in s)


def _pad(s: str, w: int) -> str:
    return s + " " * max(0, w - _dw(s))


def _inline_tg(s: str) -> str:
    """Escape HTML, then convert inline **bold** / `code` to Telegram tags."""
    s = _html.escape(s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return s


def _table_to_pre(block: list[str]) -> str:
    """Render a Markdown table as an aligned monospace <pre> block (CJK-aware)."""
    rows: list[list[str]] = []
    for ln in block:
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if cells and all(set(c) <= set("-: ") for c in cells):
            continue  # separator row |---|---|
        rows.append(cells)
    if not rows:
        return ""
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]
    widths = [max(_dw(r[c]) for r in rows) for c in range(ncol)]
    out = [" │ ".join(_pad(r[c], widths[c]) for c in range(ncol)) for r in rows]
    return "<pre>" + _html.escape("\n".join(out)) + "</pre>"


def _md_to_tg_blocks(md: str) -> list[str]:
    """Convert report Markdown into a list of atomic Telegram-HTML blocks
    (headings → bold, tables → <pre>, bullets → •). Blocks are never split."""
    lines = md.split("\n")
    blocks: list[str] = []
    buf: list[str] = []

    def flush():
        if buf:
            blocks.append("\n".join(buf).strip())
            buf.clear()

    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        is_tbl = (line.lstrip().startswith("|") and i + 1 < n
                  and set(lines[i + 1].replace("|", "").replace(" ", "")) <= set("-:"))
        if is_tbl:
            flush()
            tbl = []
            while i < n and lines[i].lstrip().startswith("|"):
                tbl.append(lines[i]); i += 1
            blocks.append(_table_to_pre(tbl))
            continue
        m = re.match(r"^(#{1,6})\s*(.*)$", line)
        if m:
            flush()
            blocks.append(f"<b>{_inline_tg(m.group(2).strip())}</b>")
            i += 1
            continue
        if re.match(r"^\s*[-*]\s+", line):
            buf.append("• " + _inline_tg(re.sub(r"^\s*[-*]\s+", "", line)))
            i += 1
            continue
        if line.strip() == "":
            flush()
        else:
            buf.append(_inline_tg(line))
        i += 1
    flush()
    return [b for b in blocks if b]


def format_report_telegram(rep: dict, kst: str, lang: str = "ko", limit: int = 3900,
                           title: str = "Kiwoom Daily Report", emoji: str = "📈") -> list[str]:
    """Build the FULL report (same content + table as the dashboard) as a list of
    Telegram-HTML messages, each under the 4096-char limit. Generic across report
    types — pass title/emoji to brand the header."""
    body = rep.get("detail_ko") if lang == "ko" else rep.get("detail_en")
    if not body or (lang == "ko" and (len(body.strip()) < 200
                                      or "same report in korean" in body.lower())):
        body = rep.get("detail_en") or ""
    # Drop a leading "# <title>" H1 (we add our own header).
    body = re.sub(r"^\s*#\s+.*\n", "", body, count=1)

    header = f"{emoji} <b>{_html.escape(title)}</b>\n<i>{_html.escape(kst)}</i>"
    blocks = _md_to_tg_blocks(body)

    chunks: list[str] = []
    cur = header
    for blk in blocks:
        if len(blk) > limit:  # oversized single block — hard-split on newlines
            for part in blk.split("\n"):
                if len(cur) + len(part) + 1 > limit:
                    chunks.append(cur); cur = ""
                cur += ("\n" if cur else "") + part
            continue
        if len(cur) + len(blk) + 2 > limit:
            chunks.append(cur); cur = ""
        cur += ("\n\n" if cur else "") + blk
    if cur.strip():
        chunks.append(cur)

    total = len(chunks)
    if total > 1:
        chunks = [f"{c}\n\n<i>({i + 1}/{total})</i>" for i, c in enumerate(chunks)]
    return chunks


def format_kiwoom_telegram(rep: dict, kst: str, lang: str = "ko", limit: int = 3900) -> list[str]:
    """Back-compat wrapper — Kiwoom-branded Telegram chunks."""
    return format_report_telegram(rep, kst, lang, limit, "Kiwoom Daily Report", "📈")
