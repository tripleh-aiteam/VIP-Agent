"""
asset_report — DETAILED daily Asset Agent report (multi-page, bilingual EN/KO).

The small Telegram card (agent_report_builder.build_asset_report) only surfaces a
handful of metrics. This builds the FULL report the boss gets in the 6:50 AM
consolidated email next to Kiwoom / Newspaper / YouTube / Recommendation:

  1. 자산 포트폴리오 개요   (portfolio overview — properties, units, value)
  2. 임대 현황 · 공실 분석   (occupancy & vacancy)
  3. 임대 수익 · 현금흐름     (rental income, collection rate, 3-month forecast)
  4. 계약 현황               (active contracts table + analysis)
  5. 만료 예정 임대차         (expiring leases table + renewal strategy)
  6. 재무 요약               (cash, overdue, liquidity)
  7. 리스크 평가             (risk factors)
  8. 추천 액션               (concrete next steps)

Real data comes from the Asset Operations backend via the existing
`asset_summary` task (same source as the small card). Saved as
report_type='asset_report' (period='daily'); also emailed + Telegram.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from services.logger import log


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _won(v: Any) -> str:
    """KRW amount as 억/만원 for readability ('—' if not a number)."""
    try:
        f = float(str(v).replace(",", "").strip())
    except Exception:
        return "—"
    if abs(f) >= 1e8:
        return f"{f/1e8:,.1f}억원"
    if abs(f) >= 1e4:
        return f"{f/1e4:,.0f}만원"
    return f"{f:,.0f}원"


def _num(v: Any) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except Exception:
        return 0.0


def _fetch_asset_data(db, trace_id: str) -> dict:
    """Run the asset_summary task and return its normalized output_payload (the
    same rich payload the real_asset_adapter produces)."""
    try:
        from services.agent_report_builder import _dispatch
        d = _dispatch(db, "asset_summary", "asset", trace_id + "-asset")
        return d.get("output") or {}
    except Exception as e:
        log.warning(f"asset_report: data fetch failed: {str(e)[:120]}")
        return {}


# ---------------------------------------------------------------------------
# Deterministic tables (real numbers — never invented)
# ---------------------------------------------------------------------------

def _summary_table(o: dict, ko: bool) -> str:
    pf = o.get("portfolio", {}) or {}
    cash = o.get("cash", {}) or {}
    units = _num(pf.get("total_units"))
    occ = _num(pf.get("occupied_units"))
    occ_rate = round(occ / units * 100, 1) if units else (100 - _num(pf.get("vacancy_rate")))
    rows = [
        ("총 자산(부동산) 수" if ko else "Total properties", str(int(_num(pf.get("total_properties"))) or "—")),
        ("총 세대/호실" if ko else "Total units", str(int(units)) if units else "—"),
        ("점유 세대" if ko else "Occupied units",
         (f"{int(occ)}/{int(units)} ({occ_rate:.0f}%)" if units else "—")),
        ("공실률" if ko else "Vacancy rate", f"{_num(pf.get('vacancy_rate')):.1f}%"),
        ("월 임대수입" if ko else "Monthly rental income", _won(pf.get("monthly_rental_income"))),
        ("현금 잔고" if ko else "Cash balance", _won(cash.get("total_balance"))),
        ("연체 금액" if ko else "Overdue", _won(pf.get("total_overdue"))),
        ("30일 내 만료 계약" if ko else "Expiries (30d)", str(int(_num(pf.get("upcoming_expiries_30d"))))),
        ("90일 내 만료 계약" if ko else "Expiries (90d)", str(int(_num(pf.get("upcoming_expiries_90d"))))),
        ("승인 대기" if ko else "Pending approvals", str(int(_num(pf.get("pending_approvals"))))),
        ("리스크 등급" if ko else "Risk level", str(o.get("risk_level", "—"))),
    ]
    head = ("| 항목 | 값 |\n|---|---|" if ko else "| Metric | Value |\n|---|---|")
    return head + "\n" + "\n".join(f"| {k} | {v} |" for k, v in rows)


def _contracts_table(o: dict, ko: bool) -> str:
    lst = ((o.get("contracts", {}) or {}).get("list")) or []
    if not lst:
        return ""
    head = ("| 임차인 | 월세 | 보증금 | 종료일 | 상태 |\n|---|---|---|---|---|" if ko
            else "| Tenant | Monthly rent | Deposit | End date | Status |\n|---|---|---|---|---|")
    lines = [head]
    for c in lst[:15]:
        lines.append(f"| {c.get('tenant','?')} | {_won(c.get('monthly_rent'))} | "
                     f"{_won(c.get('deposit'))} | {c.get('end_date','?')} | {c.get('status','?')} |")
    return "\n".join(lines)


def _expiries_table(o: dict, ko: bool) -> str:
    lst = ((o.get("expiring_leases", {}) or {}).get("list")) or []
    if not lst:
        return ""
    head = ("| 임차인 | 월세 | 만료일 |\n|---|---|---|" if ko
            else "| Tenant | Monthly rent | Expires |\n|---|---|---|")
    lines = [head]
    for e in lst[:10]:
        lines.append(f"| {e.get('tenant','?')} | {_won(e.get('monthly_rent'))} | {e.get('end_date','?')} |")
    return "\n".join(lines)


def _forecast_block(o: dict, ko: bool) -> str:
    fc = o.get("forecast") or []
    ri = o.get("rental_income") or []
    if not fc and not ri:
        return ""
    parts = []
    if fc:
        if ko:
            parts.append("3개월 현금흐름 전망: " + ", ".join(
                f"{f.get('month','?')} 순현금 {_won(f.get('net_cashflow'))}" for f in fc[:3]))
        else:
            parts.append("3-month cash-flow forecast: " + ", ".join(
                f"{f.get('month','?')} net {_won(f.get('net_cashflow'))}" for f in fc[:3]))
    if ri:
        if ko:
            parts.append("임대료 수금률: " + ", ".join(
                f"{r.get('month','?')} {r.get('collection_rate','?')}% (연체 {_won(r.get('overdue'))})" for r in ri[:3]))
        else:
            parts.append("Rent collection: " + ", ".join(
                f"{r.get('month','?')} {r.get('collection_rate','?')}% (overdue {_won(r.get('overdue'))})" for r in ri[:3]))
    return "\n".join(parts)


def _facts(o: dict) -> str:
    """Compact, complete fact sheet for the LLM — every real number it may use."""
    pf = o.get("portfolio", {}) or {}
    cash = o.get("cash", {}) or {}
    lines = [
        f"properties={int(_num(pf.get('total_properties')))}, units={int(_num(pf.get('total_units')))}, "
        f"occupied={int(_num(pf.get('occupied_units')))}, vacant={int(_num(pf.get('vacant_units')))}, "
        f"vacancy_rate={_num(pf.get('vacancy_rate'))}%, monthly_rent_income={_won(pf.get('monthly_rental_income'))}, "
        f"cash_balance={_won(cash.get('total_balance'))} ({cash.get('currency','KRW')}, {cash.get('accounts',0)} accounts), "
        f"overdue={_won(pf.get('total_overdue'))}, expiries_30d={int(_num(pf.get('upcoming_expiries_30d')))}, "
        f"expiries_90d={int(_num(pf.get('upcoming_expiries_90d')))}, pending_approvals={int(_num(pf.get('pending_approvals')))}, "
        f"alerts={o.get('alerts_count',0)}, vacancies_listed={o.get('vacancies_count',0)}, "
        f"risk_level={o.get('risk_level','?')}.",
    ]
    fb = _forecast_block(o, ko=False)
    if fb:
        lines.append(fb)
    risks = o.get("risk_factors") or []
    if risks:
        lines.append("risk_factors: " + "; ".join(str(r) for r in risks))
    cl = ((o.get("contracts", {}) or {}).get("list")) or []
    if cl:
        lines.append("contracts(sample): " + "; ".join(
            f"{c.get('tenant','?')} {_won(c.get('monthly_rent'))}/mo ends {c.get('end_date','?')}" for c in cl[:10]))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build_asset_report(db, trace_id: str) -> dict:
    """Detailed Asset Agent report (real data tables + LLM analysis, EN+KO)."""
    from services.kst import kst_date as _kst_date
    kst_date = _kst_date()
    o = _fetch_asset_data(db, trace_id)
    has_data = bool(o) and not o.get("fallback")

    table_ko = _summary_table(o, ko=True)
    table_en = _summary_table(o, ko=False)
    c_ko, c_en = _contracts_table(o, ko=True), _contracts_table(o, ko=False)
    e_ko, e_en = _expiries_table(o, ko=True), _expiries_table(o, ko=False)

    pf = o.get("portfolio", {}) or {}
    units = _num(pf.get("total_units"))
    occ = _num(pf.get("occupied_units"))
    occ_rate = round(occ / units * 100, 1) if units else 0
    sum_en = (f"Asset Agent daily: {int(_num(pf.get('total_properties')))} properties, "
              f"{int(occ)}/{int(units)} units occupied ({occ_rate:.0f}%), "
              f"monthly rent {_won(pf.get('monthly_rental_income'))}, risk {o.get('risk_level','—')}.")
    sum_ko = (f"자산 에이전트 일일: 부동산 {int(_num(pf.get('total_properties')))}건, "
              f"{int(occ)}/{int(units)}세대 점유({occ_rate:.0f}%), "
              f"월 임대수입 {_won(pf.get('monthly_rental_income'))}, 리스크 {o.get('risk_level','—')}.")

    detail_en = detail_ko = ""
    try:
        from services.llm_client import chat_completion_sync
        sysmsg = (
            "You are TripleH's senior real-estate asset manager writing the DETAILED "
            "DAILY ASSET REPORT for the boss. Use ONLY the data provided — NEVER invent "
            "numbers. ALL amounts are Korean Won (KRW). This must be a SUBSTANTIAL ~3-page "
            "report (AT LEAST 1500 words). Produce EXACTLY this structure:\n"
            "## 1. 자산 포트폴리오 개요\n## 2. 임대 현황 · 공실 분석\n"
            "## 3. 임대 수익 · 현금흐름\n## 4. 계약 현황\n## 5. 만료 예정 임대차\n"
            "## 6. 재무 요약\n## 7. 리스크 평가\n## 8. 추천 액션\n\n"
            "Rules per section:\n"
            "- Section 1: insert the SUMMARY TABLE verbatim, then 2 paragraphs interpreting "
            "the portfolio scale, occupancy and overall health.\n"
            "- Section 2: analyse occupancy vs vacancy — what the vacancy rate means, which "
            "direction it's trending, and the revenue impact of filling vacant units.\n"
            "- Section 3: interpret the rent collection rate and the 3-month cash-flow "
            "forecast; explain what the net-cashflow path implies and any collection risk.\n"
            "- Section 4: insert the CONTRACTS TABLE verbatim (if provided), then analyse the "
            "tenant mix, rent concentration, and contract health.\n"
            "- Section 5: insert the EXPIRING-LEASES TABLE verbatim (if provided), then give a "
            "concrete RENEWAL STRATEGY for the leases expiring soonest (who to contact, "
            "renewal vs re-let, rent-review opportunities).\n"
            "- Section 6: cash balance, overdue, liquidity runway — is the cash position "
            "comfortable relative to monthly obligations?\n"
            "- Section 7: lay out each real risk factor and its severity + mitigation.\n"
            "- Section 8: 4-6 concrete, prioritised action items the boss/team should take "
            "this week, each tied to a specific number above.\n"
            "Write substantive prose — this is an executive briefing, not a list of metrics. "
            "Output ONLY the finished English Markdown report (Sections 1-8)."
        )
        if not has_data:
            sysmsg += ("\nNOTE: the asset backend returned fallback/seed data this run — write "
                       "the report normally from the numbers given, but add one honest line in "
                       "Section 1 noting the figures are provisional pending a live data sync.")
        data_blob = (
            f"DATE (KST): {kst_date}\n\n"
            f"SUMMARY TABLE (insert verbatim in Section 1):\n{table_en}\n\n"
            + (f"CONTRACTS TABLE (insert verbatim in Section 4):\n{c_en}\n\n" if c_en else "")
            + (f"EXPIRING-LEASES TABLE (insert verbatim in Section 5):\n{e_en}\n\n" if e_en else "")
            + f"FACTS / TECHNICALS:\n{_facts(o)}"
        )
        out = chat_completion_sync(
            system_prompt=sysmsg, messages=[{"role": "user", "content": data_blob[:12000]}],
            max_tokens=7000, temperature=0.5, model="groq-llama-3.3-70b", prefer_paid=True) or ""
        if out.strip() and not out.lstrip().startswith(("[LLM unavailable]", "[server error]")):
            detail_en = out.strip()
            # Korean translation (dedicated call — keeps full length + swaps KO tables).
            try:
                ko_tables = "Section 1 표:\n" + table_ko
                if c_ko:
                    ko_tables += "\n\nSection 4 표:\n" + c_ko
                if e_ko:
                    ko_tables += "\n\nSection 5 표:\n" + e_ko
                ko_sys = (
                    "You are a professional Korean financial translator. Translate the ENTIRE "
                    "English asset report below into natural, professional Korean (존댓말). "
                    "Translate EVERYTHING incl. section headings; the output must contain NO "
                    "English prose. Keep every number, %, 원 amount and date IDENTICAL — "
                    "translate only the words. Preserve ALL Markdown structure and tables. "
                    "Replace the English data tables with these EXACT Korean tables:\n"
                    f"{ko_tables}\n"
                    "Output ONLY the Korean Markdown report.")
                ko_out = chat_completion_sync(
                    system_prompt=ko_sys,
                    messages=[{"role": "user", "content": detail_en[:16000]}],
                    max_tokens=8000, temperature=0.3, model="groq-llama-3.3-70b", prefer_paid=True) or ""
                if ko_out.strip() and not ko_out.lstrip().startswith(("[LLM unavailable]", "[server error]")) \
                        and len(ko_out.strip()) > 400:
                    detail_ko = ko_out.strip()
            except Exception as e:
                log.warning(f"asset_report: KO translation failed: {str(e)[:100]}")
    except Exception as e:
        log.warning(f"asset_report: LLM compose failed: {str(e)[:120]}")

    # Deterministic fallback so the report is never empty / never tiny.
    if not detail_en:
        detail_en = (
            f"# Asset Agent Daily Report\n*{kst_date}*\n\n"
            f"## 1. 자산 포트폴리오 개요\n{sum_en}\n\n{table_en}\n\n"
            + (f"## 4. 계약 현황\n{c_en}\n\n" if c_en else "")
            + (f"## 5. 만료 예정 임대차\n{e_en}\n\n" if e_en else "")
            + "## 7. 리스크 평가\n"
            + ("\n".join(f"- {r}" for r in (o.get('risk_factors') or [])) or "- 정상 범위 내")
        )
    if not detail_ko:
        detail_ko = detail_en

    return {
        "agent_type": "asset", "name": "Asset Agent Report", "emoji": "🏢",
        "status": "ok" if has_data else ("partial" if o else "unavailable"),
        "summary_en": sum_en, "summary_ko": sum_ko,
        "detail_en": detail_en, "detail_ko": detail_ko,
        "table_en": table_en, "table_ko": table_ko,
        "data": o,
        "source": "Asset Operations backend (live)" + ("" if has_data else " — provisional/seed data"),
    }
