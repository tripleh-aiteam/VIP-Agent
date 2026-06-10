"""
master_report — the 4th daily report. Reads the day's three source reports
(Kiwoom price/technical, Newspaper news, YouTube video) and synthesises ONE
smart consolidated summary: a cross-source signal table per stock, where the
sources agree/disagree, the merged catalyst schedule (일정매매), and a final
consensus BUY/HOLD/SELL.

Runs after the other three (≈6:50 AM KST). Saved as report_type='master_report'
(period='cross'); also Telegram + Word email.
"""

from __future__ import annotations

import re
from datetime import datetime

from services.logger import log
from services import kiwoom_report as _kr


def _latest_report(db, report_type: str) -> dict:
    """Fetch the most recent stored report of a type → its `report` dict."""
    try:
        from db.models import OrchReport
        r = (db.query(OrchReport)
             .filter(OrchReport.report_type == report_type)
             .order_by(OrchReport.created_at.desc()).first())
        if not r:
            return {}
        return (r.content_json or {}).get("report", {}) or {}
    except Exception as e:
        log.warning(f"master: fetch {report_type} failed: {e}")
        return {}


def _section(md: str, *keywords: str) -> str:
    """Extract a Markdown section whose heading contains any keyword."""
    md = md or ""
    for kw in keywords:
        m = re.search(rf"\n#{{1,3}}\s*[^\n]*{re.escape(kw)}[^\n]*\n(.*?)(\n#{{1,3}}\s|\Z)",
                      "\n" + md, re.S | re.I)
        if m:
            return m.group(1).strip()
    return ""


def _digest(rep: dict, label: str) -> str:
    """Compact digest of one source report: summary + recommendations + catalysts."""
    if not rep:
        return f"### {label}\n(no report available today)"
    de = rep.get("detail_en", "") or ""
    recs = _section(de, "Recommendation", "Recommended Actions")
    cats = _section(de, "Catalyst", "Schedule")
    parts = [f"### {label}", f"Summary: {rep.get('summary_en', '')}"]
    if recs:
        parts.append(f"RECOMMENDATIONS:\n{recs[:1600]}")
    if cats:
        parts.append(f"CATALYSTS:\n{cats[:1400]}")
    if not recs and not cats:
        parts.append(de[:1500])
    return "\n".join(parts)


def build_master_report(db, trace_id: str) -> dict:
    """Synthesise the 3 source reports into one consolidated smart summary."""
    kiwoom = _latest_report(db, "kiwoom_report")
    news = _latest_report(db, "newspaper_report")
    youtube = _latest_report(db, "youtube_report")

    # Use the freshest price rows/table available (prefer kiwoom, else refetch).
    rows = kiwoom.get("rows") or news.get("rows") or youtube.get("rows")
    table_en = kiwoom.get("table_en") or news.get("table_en") or youtube.get("table_en")
    table_ko = kiwoom.get("table_ko") or news.get("table_ko") or youtube.get("table_ko")
    if not rows or not table_en:
        rows, table_en, table_ko, _ = _kr.gather_priced_rows()

    have = [name for name, r in (("Kiwoom", kiwoom), ("Newspaper", news), ("YouTube", youtube)) if r]
    kst_date = datetime.utcnow().strftime("%Y-%m-%d")
    sum_en = f"Master summary — consolidated from {len(have)}/3 sources ({', '.join(have) or 'none'})."
    sum_ko = f"통합 요약 — {len(have)}/3개 소스 종합 ({', '.join(have) or '없음'})."

    detail_en = detail_ko = ""
    try:
        from services.llm_client import chat_completion_sync
        sysmsg = (
            "You are TripleH's chief strategist writing the MASTER daily report — "
            "ONE consolidated view built from THREE source reports: Kiwoom "
            "(price/technical), Newspaper (news), and YouTube (video). Use ONLY the "
            "provided material — NEVER invent. ALL prices are Korean Won (KRW). "
            "Produce this EXACT structure:\n"
            "## 1. Executive Summary\n## 2. Smart Signal Table\n"
            "## 3. Where the Sources Agree & Disagree\n"
            "## 4. Consolidated Catalysts & Schedule (일정매매)\n"
            "## 5. Final Consensus Recommendations\n\n"
            "Rules:\n"
            "- Section 1: a sharp 1-2 paragraph consensus read of the day.\n"
            "- Section 2 (Smart Signal Table) — the CENTREPIECE. A Markdown table with "
            "columns | Stock | Close (KRW) | Change | Kiwoom | News | YouTube | "
            "Consensus | Key Catalyst | — ONE ROW per watchlist stock (SK Hynix, "
            "Samsung, AMD, Micron, SOXX, SanDisk, Broadcom, SK Telecom, Samsung SDS, "
            "Naver, KODEX 200). Fill Kiwoom/News/YouTube with that source's stance "
            "(BUY / HOLD / SELL / —) inferred from its recommendations; Consensus = the "
            "combined call (note agreement strength); Key Catalyst = the nearest "
            "relevant event. Use the real Close/Change from the data table.\n"
            "- Section 3: where the 3 sources AGREE (high-confidence) vs DISAGREE "
            "(watch) — name the stocks.\n"
            "- Section 4: merge the catalysts from News + YouTube into one schedule "
            "(date/timing, event, stock, early-position note) for 일정매매.\n"
            "- Section 5: final BUY/HOLD/SELL table | Stock | Action | Confidence | "
            "Reason | where Confidence reflects cross-source agreement; THEN a short "
            "'### Top Conviction Ideas' paragraph on the 2-3 strongest setups.\n"
            "Be decisive and specific; ~1200-1700 words. Output ONLY the English "
            "Markdown report — no preamble."
        )
        user = (f"Date (KST): {kst_date}\n\n"
                f"PRICE TABLE (use for Close/Change in Section 2):\n{table_en}\n\n"
                f"{_digest(kiwoom, 'KIWOOM (price/technical) report')}\n\n"
                f"{_digest(news, 'NEWSPAPER report')}\n\n"
                f"{_digest(youtube, 'YOUTUBE report')}")
        out = chat_completion_sync(
            system_prompt=sysmsg, messages=[{"role": "user", "content": user[:24000]}],
            max_tokens=9000, temperature=0.4, model="groq-llama-3.3-70b") or ""
        bad = (not out.strip()) or out.lstrip().startswith(("[LLM unavailable]", "[server error]"))
        if not bad:
            detail_en = out.strip()
            try:
                ko_sys = (
                    "You are a professional Korean financial translator. Translate the "
                    "ENTIRE English master report into natural, professional Korean "
                    "(존댓말). Translate EVERYTHING — never summarise or stub. Preserve "
                    "ALL Markdown, headings and tables; keep every number, %, 원, ticker "
                    "and BUY/HOLD/SELL IDENTICAL. Replace the price columns' table with "
                    f"values consistent with this Korean table:\n{table_ko}\n"
                    "Output ONLY the Korean Markdown report.")
                ko_out = chat_completion_sync(
                    system_prompt=ko_sys,
                    messages=[{"role": "user", "content": detail_en[:20000]}],
                    max_tokens=9000, temperature=0.3, model="groq-llama-3.3-70b") or ""
                ko_bad = ((not ko_out.strip())
                          or ko_out.lstrip().startswith(("[LLM unavailable]", "[server error]"))
                          or len(ko_out.strip()) < 400)
                if not ko_bad:
                    detail_ko = ko_out.strip()
            except Exception as e:
                log.warning(f"master KO translation failed: {e}")
    except Exception as e:
        log.warning(f"master LLM compose failed: {e}")

    if not detail_en:
        detail_en = (f"# Master Daily Summary\n*{kst_date}*\n\n## 1. Executive Summary\n{sum_en}\n\n"
                     f"## 2. Smart Signal Table\n{table_en}\n\n"
                     f"## 3. Where the Sources Agree & Disagree\n- See the three source reports.\n\n"
                     f"## 4. Consolidated Catalysts & Schedule (일정매매)\n- See News/YouTube catalysts.\n\n"
                     f"## 5. Final Consensus Recommendations\n| Stock | Action | Confidence | Reason |\n"
                     f"|---|---|---|---|\n| — | HOLD | low | LLM unavailable — manual review |")
    if not detail_ko:
        detail_ko = detail_en

    return {
        "agent_type": "master", "name": "Master Daily Summary", "emoji": "🧠",
        "status": "ok" if have else "partial",
        "summary_en": sum_en, "summary_ko": sum_ko,
        "detail_en": detail_en, "detail_ko": detail_ko,
        "table_en": table_en, "table_ko": table_ko,
        "rows": rows,
        "sources_used": have,
        "source": "TripleH Master Synthesis (Kiwoom + Newspaper + YouTube)",
    }
