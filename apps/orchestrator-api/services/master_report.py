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


_NO_BROKER = "## Korean Securities Firms — analyst consensus\n(No broker consensus available today.)"


def build_master_report(db, trace_id: str) -> dict:
    """Synthesise the 3 source reports + Korean broker consensus into one summary."""
    kiwoom = _latest_report(db, "kiwoom_report")
    news = _latest_report(db, "newspaper_report")
    youtube = _latest_report(db, "youtube_report")

    # Use the freshest price rows/table available (prefer kiwoom, else refetch).
    rows = kiwoom.get("rows") or news.get("rows") or youtube.get("rows")
    table_en = kiwoom.get("table_en") or news.get("table_en") or youtube.get("table_en")
    table_ko = kiwoom.get("table_ko") or news.get("table_ko") or youtube.get("table_ko")
    if not rows or not table_en:
        rows, table_en, table_ko, _ = _kr.gather_priced_rows()

    # 4th input: Korean securities-firm analyst consensus (목표주가/투자의견 + recent
    # broker reports) for the KR tickers — real published calls, not LLM opinion.
    broker_facts = ""
    try:
        from services import broker_research
        broker_rows = broker_research.gather_kr_consensus(rows or [])
        broker_facts = broker_research.consensus_facts(broker_rows, ko=False)
    except Exception as e:
        log.warning(f"master broker consensus failed: {e}")

    have = [name for name, r in (("Kiwoom", kiwoom), ("Newspaper", news), ("YouTube", youtube)) if r]
    from services.kst import kst_date as _kst_date
    kst_date = _kst_date()
    sum_en = f"Daily recommendation — consolidated from {len(have)}/3 sources ({', '.join(have) or 'none'})."
    sum_ko = f"일일 투자 추천 — {len(have)}/3개 소스 종합 ({', '.join(have) or '없음'})."

    detail_en = detail_ko = ""
    try:
        from services.llm_client import chat_completion_sync
        sysmsg = (
            "You are TripleH's chief strategist writing the MASTER daily report — "
            "ONE consolidated view built from THREE source reports: Kiwoom "
            "(price/technical), Newspaper (news), and YouTube (video) — PLUS the "
            "Korean securities firms' analyst consensus (목표주가/투자의견 + recent "
            "broker reports) when provided. The reader's "
            "strategy is 일정매매 (event-driven): KNOW FUTURE EVENTS → POSITION EARLY "
            "→ SELL WHEN PUBLIC ATTENTION ARRIVES. Frame the whole report around that. "
            "Use ONLY the provided material — NEVER invent. ALL prices are KRW. "
            "TODAY'S DATE is given at the top of the user message: treat every "
            "'catalyst' as FUTURE-ONLY (after today) — DISCARD any past-dated event; "
            "if a date is unknown write 'upcoming (TBC)' or a quarter, NEVER a past "
            "date. Produce this EXACT structure (~4-PAGE report; do NOT include a "
            "price/market-data grid — the price table lives only in the Kiwoom "
            "report):\n"
            "## 1. Executive Summary\n## 2. Signal Explanations (per stock)\n"
            "## 3. Where the Sources Agree & Disagree\n"
            "## 4. Upcoming Catalysts & Schedule (일정매매 · FUTURE ONLY)\n"
            "## 5. 증권사 추천 (Korean Securities Firms' Analyst Calls)\n"
            "## 6. Final Consensus Recommendations\n\n"
            "Rules:\n"
            "- Section 1: a sharp 2-3 paragraph consensus read of the day + the key "
            "future setups to watch.\n"
            "- Section 2 (Signal Explanations) — IMPORTANT, the deepest section: a "
            "DEDICATED paragraph (3-5 sentences) for EACH watchlist stock (SK Hynix, "
            "Samsung, AMD, Micron, SOXX, SanDisk, Broadcom, SK Telecom, Samsung SDS, "
            "Naver, KODEX 200). In PROSE (no table) state where each source stands "
            "(Kiwoom / News / YouTube → BUY/HOLD/SELL) and the CONSENSUS, then EXPLAIN "
            "WHY the three land where they do (technical + news + video evidence) and "
            "the 일정매매 PLAY: which FUTURE catalyst to position before and when to sell "
            "into the attention. Be concrete; cite the real change% in the prose.\n"
            "- Section 3: where the 3 sources AGREE (high-confidence) vs DISAGREE "
            "(watch) — name the stocks and what the disagreement means.\n"
            "- Section 4: merge ONLY FUTURE catalysts into a table | Date / Timing | "
            "Event | Stock(s) | Likely impact | Early-position play | — every date "
            "AFTER today; then 2-3 bullet 'positioning plays' (buy before <future "
            "event> → sell when the crowd arrives).\n"
            "- Section 5 (증권사 추천): use ONLY the 'Korean Securities Firms' "
            "consensus' material provided. Lead with a table | 종목 | 컨센서스 목표주가 | "
            "상승여력 | 투자의견 | for the KR stocks that have data, then a 2-3 paragraph "
            "synthesis: what the brokerages collectively favour, notable target-price "
            "moves, and cite SPECIFIC recent reports by firm (e.g. '미래에셋: <title>', "
            "'한국투자: <title>'). If no broker data was provided, write one line saying "
            "so and skip the table. NEVER invent a target price or a firm's call.\n"
            "- Section 6: final | Stock | Action | Confidence | Reason | (BUY/HOLD/"
            "SELL) — let the brokerage consensus INFORM these; THEN '### Top Conviction "
            "Ideas' — the 2-3 strongest event-driven setups with the entry-before / "
            "exit-on-attention logic.\n"
            "Be decisive and specific. The whole report must be ~2200-2800 words "
            "(about 4 pages); Section 2 alone ~1000 words. Never truncate. Output ONLY "
            "the English Markdown report — no preamble."
        )
        user = (f"TODAY'S DATE (KST): {kst_date}  ← every catalyst MUST be dated AFTER this.\n\n"
                f"PRICE CONTEXT (for the prose only — do NOT print a table):\n{table_en}\n\n"
                f"{_digest(kiwoom, 'KIWOOM (price/technical) report')}\n\n"
                f"{_digest(news, 'NEWSPAPER report')}\n\n"
                f"{_digest(youtube, 'YOUTUBE report')}\n\n"
                f"{broker_facts or _NO_BROKER}")
        out = chat_completion_sync(
            system_prompt=sysmsg, messages=[{"role": "user", "content": user[:26000]}],
            max_tokens=12000, temperature=0.4, model="groq-llama-3.3-70b", prefer_paid=True) or ""
        bad = (not out.strip()) or out.lstrip().startswith(("[LLM unavailable]", "[server error]"))
        if not bad:
            detail_en = out.strip()
            try:
                ko_sys = (
                    "You are a professional Korean financial translator. Translate the "
                    "ENTIRE English master report into natural, professional Korean "
                    "(존댓말). Translate EVERYTHING — never summarise or stub — including "
                    "ALL section headings and English words. The output must contain NO "
                    "English prose. Translate the action verbs: BUY→매수, HOLD→보유, "
                    "SELL→매도, Strong Buy→적극 매수. Keep ONLY numbers, %, 원, and ticker "
                    "symbols identical; English company names may keep a Korean label. "
                    "Preserve ALL Markdown structure and tables. Replace the price "
                    f"columns' table with values consistent with this Korean table:\n{table_ko}\n"
                    "Output ONLY the Korean Markdown report.")
                ko_out = chat_completion_sync(
                    system_prompt=ko_sys,
                    messages=[{"role": "user", "content": detail_en[:26000]}],
                    max_tokens=12000, temperature=0.3, model="groq-llama-3.3-70b", prefer_paid=True) or ""
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
        detail_en = (f"# Daily Recommendation Report\n*{kst_date}*\n\n## 1. Executive Summary\n{sum_en}\n\n"
                     f"## 2. Signal Explanations (per stock)\n- See the three source reports.\n\n"
                     f"## 3. Where the Sources Agree & Disagree\n- See the three source reports.\n\n"
                     f"## 4. Upcoming Catalysts & Schedule (일정매매 · FUTURE ONLY)\n- See News/YouTube catalysts.\n\n"
                     f"## 5. Final Consensus Recommendations\n| Stock | Action | Confidence | Reason |\n"
                     f"|---|---|---|---|\n| — | HOLD | low | LLM unavailable — manual review |")
    if not detail_ko:
        detail_ko = detail_en

    return {
        "agent_type": "master", "name": "Daily Recommendation Report", "emoji": "💡",
        "status": "ok" if have else "partial",
        "summary_en": sum_en, "summary_ko": sum_ko,
        "detail_en": detail_en, "detail_ko": detail_ko,
        "table_en": table_en, "table_ko": table_ko,
        "rows": rows,
        "sources_used": have,
        "source": "TripleH Master Synthesis (Kiwoom + Newspaper + YouTube)",
    }
