"""
asset_report — DETAILED daily Asset Agent report (multi-page, bilingual EN/KO).

PRIMARY data source = the company's real asset workbook, loaded into Supabase by
scripts/import_assets.py (tables asset_portfolio + asset_units) and read via
services.asset_data. This is the ~812억 portfolio (land / commercial / 도생 /
생숙 / etc.) with per-unit rent + status.

If that data isn't loaded yet, it falls back to the live Asset Operations backend
(`asset_summary` task) — which currently returns near-zero aggregates, so the
Supabase path is strongly preferred.

Sections:
  1. 자산 포트폴리오 개요   2. 자산 구성 (구분별)   3. 임대 현황 · 공실
  4. 임대 수익 · 현금흐름   5. 자산별 상세         6. 매각/관리 현황
  7. 리스크 평가           8. 추천 액션
Saved as report_type='asset_report'.
"""

from __future__ import annotations

from typing import Any

from services.logger import log


def _won(v: Any) -> str:
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


def _fetch_backend(db, trace_id: str) -> dict:
    """Live Asset backend payload (operational extras / fallback)."""
    try:
        from services.agent_report_builder import _dispatch
        return (_dispatch(db, "asset_summary", "asset", trace_id + "-asset").get("output")) or {}
    except Exception as e:
        log.warning(f"asset_report: backend fetch failed: {str(e)[:120]}")
        return {}


def build_asset_report(db, trace_id: str) -> dict:
    """Detailed Asset Agent report — real Supabase asset data + LLM analysis (EN+KO)."""
    from services.kst import kst_date as _kst_date
    from services import asset_data as _ad
    kst_date = _kst_date()

    adata = _ad.load_asset_data(db)
    backend = _fetch_backend(db, trace_id)
    have_real = bool(adata.get("available"))
    have_backend = bool(backend) and not backend.get("fallback")
    status = "ok" if (have_real or have_backend) else ("partial" if backend else "unavailable")

    if have_real:
        t = adata["totals"]
        occ = adata["occupancy"]
        cat_ko, cat_en = _ad.category_table(adata, True), _ad.category_table(adata, False)
        pf_ko, pf_en = _ad.portfolio_table(adata, True), _ad.portfolio_table(adata, False)
        prop_ko, prop_en = _ad.property_table(adata, True), _ad.property_table(adata, False)
        table_ko = (f"**자산 구성 (구분별)**\n{cat_ko}\n\n**자산 목록 (총괄)**\n{pf_ko}"
                    f"\n\n**자산별 현황**\n{prop_ko}")
        table_en = (f"**Portfolio by category**\n{cat_en}\n\n**Asset list**\n{pf_en}"
                    f"\n\n**By property**\n{prop_en}")
        sum_ko = (f"자산 포트폴리오: 총 자산가치 {_num(t.get('value'))/1e8:,.0f}억원 "
                  f"({t.get('items',0)}개 항목·{t.get('units',0)}개 세부자산), "
                  f"월 임대수입 {_won(t.get('monthly_rent'))}, "
                  f"공실 {occ.get('vacant',0)}/{occ.get('classified',0)}({occ.get('vacancy_rate',0):.0f}%).")
        sum_en = (f"Asset portfolio: total value {_num(t.get('value'))/1e8:,.0f}억 KRW "
                  f"({t.get('items',0)} items, {t.get('units',0)} units), "
                  f"monthly rent {_won(t.get('monthly_rent'))}, "
                  f"vacancy {occ.get('vacant',0)}/{occ.get('classified',0)} ({occ.get('vacancy_rate',0):.0f}%).")
        facts = _ad.facts(adata)
        if have_backend:
            facts += ("\nLIVE BACKEND (operational, supplementary): "
                      f"cash_balance={_won((backend.get('cash') or {}).get('total_balance'))}, "
                      f"overdue={_won((backend.get('portfolio') or {}).get('total_overdue'))}, "
                      f"expiries_30d={(backend.get('portfolio') or {}).get('upcoming_expiries_30d', 0)}.")
        cat_blob = f"PORTFOLIO-BY-CATEGORY TABLE (Section 2, verbatim):\n{cat_en}\n\n"
        list_blob = f"ASSET LIST TABLE (Section 5, verbatim):\n{pf_en}\n\n"
        prop_blob = f"BY-PROPERTY TABLE (Section 5, verbatim):\n{prop_en}\n\n"
        src = "Company asset workbook (자산관리.xlsx → Supabase)"
    else:
        # Fallback: live-backend-only (near-zero data). Keep it honest.
        pf = backend.get("portfolio", {}) or {}
        cash = backend.get("cash", {}) or {}
        table_ko = table_en = ""
        sum_ko = (f"자산 에이전트(백엔드): 부동산 {int(_num(pf.get('total_properties')))}건, "
                  f"월 임대수입 {_won(pf.get('monthly_rental_income'))}.")
        sum_en = (f"Asset backend: {int(_num(pf.get('total_properties')))} properties, "
                  f"monthly rent {_won(pf.get('monthly_rental_income'))}.")
        facts = (f"backend portfolio={pf}, cash={cash}; NOTE: the real asset workbook "
                 "has not been imported to Supabase yet (run scripts/import_assets.py).")
        cat_blob = list_blob = prop_blob = ""
        src = "Asset Operations backend (provisional — workbook not yet imported)"

    detail_en = detail_ko = ""
    try:
        from services.llm_client import chat_completion_sync
        sysmsg = (
            "You are TripleH's senior real-estate asset manager writing the DETAILED "
            "DAILY ASSET REPORT for the boss. Use ONLY the data provided — NEVER invent "
            "numbers. All amounts are Korean Won (KRW); a portfolio of tens of billions "
            "of won (억/조) is expected. This must be a SUBSTANTIAL ~3-page report "
            "(AT LEAST 1500 words). Produce EXACTLY this structure:\n"
            "## 1. 자산 포트폴리오 개요\n## 2. 자산 구성 (구분별)\n## 3. 임대 현황 · 공실\n"
            "## 4. 임대 수익 · 현금흐름\n## 5. 자산별 상세\n## 6. 매각 · 관리 현황\n"
            "## 7. 리스크 평가\n## 8. 추천 액션\n\n"
            "Rules per section:\n"
            "- Section 1: state the total portfolio value, item/unit counts and overall "
            "composition; 2 paragraphs on scale and health.\n"
            "- Section 2: insert the PORTFOLIO-BY-CATEGORY table verbatim, then analyse "
            "the mix (land vs commercial vs 도생/생숙/etc.) and concentration risk.\n"
            "- Section 3: occupancy vs vacancy across the units — how much rent is at "
            "risk from vacant units, and which categories drive it.\n"
            "- Section 4: total monthly rent, deposits held, and the implied annual "
            "income; comment on yield relative to asset value.\n"
            "- Section 5: insert the ASSET LIST and BY-PROPERTY tables verbatim, then "
            "call out the largest assets and the biggest rent contributors.\n"
            "- Section 6: assets marked for sale / 매각 진행중 / 미분양 / 공실 and what to "
            "prioritise.\n"
            "- Section 7: concrete risk factors (vacancy, concentration in land, "
            "non-earning assets, data gaps) with severity.\n"
            "- Section 8: 4-6 concrete prioritised actions this week, each tied to a "
            "specific number above.\n"
            "Write substantive executive prose. Output ONLY the finished English "
            "Markdown report (Sections 1-8)."
        )
        data_blob = (f"DATE (KST): {kst_date}\n\nKEY FACTS:\n{facts}\n\n"
                     + cat_blob + list_blob + prop_blob)
        out = chat_completion_sync(
            system_prompt=sysmsg, messages=[{"role": "user", "content": data_blob[:13000]}],
            max_tokens=8000, temperature=0.5, model="groq-llama-3.3-70b", prefer_paid=True) or ""
        if out.strip() and not out.lstrip().startswith(("[LLM unavailable]", "[server error]")):
            detail_en = out.strip()
            try:
                ko_tables = ""
                if have_real:
                    ko_tables = ("Section 2 표:\n" + _ad.category_table(adata, True)
                                 + "\n\nSection 5 표(자산 목록):\n" + _ad.portfolio_table(adata, True)
                                 + "\n\nSection 5 표(자산별):\n" + _ad.property_table(adata, True))
                ko_sys = (
                    "You are a professional Korean financial translator. Translate the "
                    "ENTIRE English asset report below into natural, professional Korean "
                    "(존댓말). Translate EVERYTHING incl. section headings; NO English "
                    "prose. Keep every number, %, 원/억 amount and date IDENTICAL — "
                    "translate only words. Preserve ALL Markdown structure and tables."
                    + (f" Replace the English tables with these EXACT Korean tables:\n{ko_tables}\n" if ko_tables else "")
                    + "Output ONLY the Korean Markdown report.")
                ko_out = chat_completion_sync(
                    system_prompt=ko_sys,
                    messages=[{"role": "user", "content": detail_en[:22000]}],
                    max_tokens=13000, temperature=0.3, model="groq-llama-3.3-70b", prefer_paid=True) or ""
                if ko_out.strip() and not ko_out.lstrip().startswith(("[LLM unavailable]", "[server error]")) \
                        and len(ko_out.strip()) > 400:
                    detail_ko = ko_out.strip()
            except Exception as e:
                log.warning(f"asset_report: KO translation failed: {str(e)[:100]}")
    except Exception as e:
        log.warning(f"asset_report: LLM compose failed: {str(e)[:120]}")

    if not detail_en:
        detail_en = (f"# Asset Agent Daily Report\n*{kst_date}*\n\n## 1. 자산 포트폴리오 개요\n{sum_en}\n\n"
                     + (f"## 2. 자산 구성\n{table_en}\n\n" if table_en else ""))
    if not detail_ko:
        detail_ko = detail_en

    return {
        "agent_type": "asset", "name": "Asset Agent Report", "emoji": "🏢",
        "status": status,
        "summary_en": sum_en, "summary_ko": sum_ko,
        "detail_en": detail_en, "detail_ko": detail_ko,
        "table_en": table_ko, "table_ko": table_ko,
        "data": {"totals": adata.get("totals"), "occupancy": adata.get("occupancy"),
                 "by_category": adata.get("by_category")},
        "source": src,
    }
