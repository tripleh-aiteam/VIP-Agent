"""
realty_report — DETAILED daily Real Estate Agent report (multi-page, EN/KO).

Mirrors asset_report but for the Real Estate agent's data:
  - the Triple H listing workbook  (services.realty_kb_loader.load_real_listings)
  - live OnBid 공매 auction opportunities  (services.onbid_tools)

Sections:
  1. 부동산 포트폴리오 개요   2. 구분별 구성   3. 지역별 분포
  4. 주요 매물 (상세)        5. 공매(OnBid) 기회   6. 시장 · 가치 관찰
  7. 리스크 평가            8. 추천 액션
Saved as report_type='realty_report'.
"""

from __future__ import annotations

from typing import Any

from services.logger import log


def _num(v: Any) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except Exception:
        return 0.0


def _won(v: Any) -> str:
    f = _num(v)
    if f == 0:
        return "—"
    if abs(f) >= 1e8:
        return f"{f/1e8:,.1f}억원"
    if abs(f) >= 1e4:
        return f"{f/1e4:,.0f}만원"
    return f"{f:,.0f}원"


def _load() -> tuple[list[dict], list[dict]]:
    """(listings, onbid_items) — both best-effort."""
    listings = []
    try:
        from services.realty_kb_loader import load_real_listings
        listings = load_real_listings() or []
    except Exception as e:
        log.warning(f"realty_report: listings load failed: {str(e)[:120]}")
    onbid = []
    try:
        from services.onbid_tools import tool_onbid_search
        ob = tool_onbid_search(category="real estate", sort="cheap", limit=8)
        onbid = ob.get("items") or []
    except Exception as e:
        log.warning(f"realty_report: onbid failed: {str(e)[:120]}")
    return listings, onbid


def _aggregate(listings: list[dict]) -> dict:
    total_val = sum(_num(p.get("official_value")) for p in listings)
    cats: dict[str, dict] = {}
    regions: dict[str, dict] = {}
    for p in listings:
        c = (p.get("category") or "기타").strip() or "기타"
        r = (p.get("sheet") or "—").strip() or "—"
        cd = cats.setdefault(c, {"category": c, "count": 0, "value": 0.0})
        cd["count"] += 1
        cd["value"] += _num(p.get("official_value"))
        rd = regions.setdefault(r, {"region": r, "count": 0, "value": 0.0})
        rd["count"] += 1
        rd["value"] += _num(p.get("official_value"))
    return {
        "count": len(listings), "total_value": total_val,
        "by_category": sorted(cats.values(), key=lambda x: x["value"], reverse=True),
        "by_region": sorted(regions.values(), key=lambda x: x["value"], reverse=True),
        "top": sorted(listings, key=lambda p: _num(p.get("official_value")), reverse=True)[:12],
    }


def _cat_table(agg: dict, ko: bool) -> str:
    rows = agg["by_category"]
    if not rows:
        return ""
    tot = agg["total_value"] or 1
    head = ("| 구분 | 매물수 | 공시가치 | 비중 |\n|---|---|---|---|" if ko
            else "| Category | Listings | Official value | Share |\n|---|---|---|---|")
    return head + "\n" + "\n".join(
        f"| {r['category']} | {r['count']} | {_won(r['value'])} | {r['value']/tot*100:.1f}% |" for r in rows)


def _region_table(agg: dict, ko: bool) -> str:
    rows = agg["by_region"]
    if not rows:
        return ""
    head = ("| 지역 | 매물수 | 공시가치 |\n|---|---|---|" if ko
            else "| Region | Listings | Official value |\n|---|---|---|")
    return head + "\n" + "\n".join(
        f"| {r['region']} | {r['count']} | {_won(r['value'])} |" for r in rows[:15])


def _top_table(agg: dict, ko: bool) -> str:
    rows = agg["top"]
    if not rows:
        return ""
    head = ("| 매물 | 구분 | 주소 | 공시가치 |\n|---|---|---|---|" if ko
            else "| Listing | Category | Address | Official value |\n|---|---|---|---|")
    lines = [head]
    for p in rows:
        lines.append(f"| {str(p.get('title') or '')[:24]} | {p.get('category') or ''} | "
                     f"{str(p.get('address') or '')[:30]} | {_won(p.get('official_value'))} |")
    return "\n".join(lines)


def _onbid_table(onbid: list[dict], ko: bool) -> str:
    if not onbid:
        return ""
    head = ("| 물건 | 주소 | 최저입찰가 |\n|---|---|---|" if ko
            else "| Item | Address | Min bid |\n|---|---|---|")
    lines = [head]
    for it in onbid[:8]:
        lines.append(f"| {str(it.get('name') or '')[:24]} | "
                     f"{str(it.get('address') or '')[:30]} | {it.get('min_bid', '—')} |")
    return "\n".join(lines)


def _facts(agg: dict, onbid: list[dict]) -> str:
    lines = [
        f"PORTFOLIO: {agg['count']} listings, total official value {agg['total_value']:,.0f}원 "
        f"(~{agg['total_value']/1e8:,.0f}억).",
        "BY CATEGORY: " + "; ".join(f"{r['category']} {r['count']}건 {r['value']/1e8:,.1f}억"
                                    for r in agg["by_category"]),
        "BY REGION: " + "; ".join(f"{r['region']} {r['count']}건" for r in agg["by_region"][:10]),
    ]
    if onbid:
        lines.append(f"ONBID(공매): {len(onbid)} active opportunities; cheapest e.g. "
                     + "; ".join(f"{(it.get('address') or it.get('name') or '?')[:24]} 최저 {it.get('min_bid','?')}"
                                 for it in onbid[:3]))
    return "\n".join(lines)


def build_realty_report(db, trace_id: str) -> dict:
    """Detailed Real Estate report (listings + OnBid + LLM analysis, EN+KO)."""
    from services.kst import kst_date as _kst_date
    kst_date = _kst_date()
    listings, onbid = _load()
    agg = _aggregate(listings)
    have = bool(listings)
    status = "ok" if have else ("partial" if onbid else "unavailable")

    cat_ko, cat_en = _cat_table(agg, True), _cat_table(agg, False)
    reg_ko, reg_en = _region_table(agg, True), _region_table(agg, False)
    top_ko, top_en = _top_table(agg, True), _top_table(agg, False)
    ob_ko, ob_en = _onbid_table(onbid, True), _onbid_table(onbid, False)
    table_ko = (f"**구분별 구성**\n{cat_ko}\n\n**지역별 분포**\n{reg_ko}\n\n**주요 매물**\n{top_ko}"
                + (f"\n\n**공매(OnBid) 기회**\n{ob_ko}" if ob_ko else ""))

    sum_ko = (f"부동산 포트폴리오: 매물 {agg['count']}건, 공시가치 약 {agg['total_value']/1e8:,.0f}억원, "
              f"공매(OnBid) 기회 {len(onbid)}건.")
    sum_en = (f"Real-estate portfolio: {agg['count']} listings, official value ~{agg['total_value']/1e8:,.0f}억 KRW, "
              f"{len(onbid)} live OnBid opportunities.")

    detail_en = detail_ko = ""
    try:
        from services.llm_client import chat_completion_sync
        sysmsg = (
            "You are TripleH's senior real-estate analyst writing the DETAILED DAILY "
            "REAL ESTATE report for the boss. Use ONLY the data provided — NEVER invent "
            "numbers. All amounts are Korean Won (KRW). This must be a SUBSTANTIAL "
            "~3-page report (AT LEAST 1500 words). Produce EXACTLY this structure:\n"
            "## 1. 부동산 포트폴리오 개요\n## 2. 구분별 구성\n## 3. 지역별 분포\n"
            "## 4. 주요 매물 (상세)\n## 5. 공매(OnBid) 기회\n## 6. 시장 · 가치 관찰\n"
            "## 7. 리스크 평가\n## 8. 추천 액션\n\n"
            "Rules:\n"
            "- Section 1: total listing count + total official value, overall composition; 2 paragraphs.\n"
            "- Section 2: insert the BY-CATEGORY table verbatim, then analyse the mix + concentration.\n"
            "- Section 3: insert the BY-REGION table verbatim, then comment on geographic concentration.\n"
            "- Section 4: insert the TOP-LISTINGS table verbatim, then discuss the largest holdings.\n"
            "- Section 5: insert the ONBID table verbatim (if present) and assess which auction "
            "opportunities look attractive and why; if none, say so honestly.\n"
            "- Section 6: value observations — official value vs likely market, regions to watch.\n"
            "- Section 7: concrete risks (concentration, illiquid categories like 토지, vacancy, data gaps).\n"
            "- Section 8: 4-6 concrete prioritised actions this week, each tied to a number above.\n"
            "Write substantive executive prose. Output ONLY the finished English Markdown report (Sections 1-8)."
        )
        if not have:
            sysmsg += ("\nNOTE: the listing workbook returned no data this run — write briefly and "
                       "honestly that listing data is unavailable and base the report on the OnBid items only.")
        data_blob = (
            f"DATE (KST): {kst_date}\n\nKEY FACTS:\n{_facts(agg, onbid)}\n\n"
            + (f"BY-CATEGORY TABLE (Section 2, verbatim):\n{cat_en}\n\n" if cat_en else "")
            + (f"BY-REGION TABLE (Section 3, verbatim):\n{reg_en}\n\n" if reg_en else "")
            + (f"TOP-LISTINGS TABLE (Section 4, verbatim):\n{top_en}\n\n" if top_en else "")
            + (f"ONBID TABLE (Section 5, verbatim):\n{ob_en}\n\n" if ob_en else "")
        )
        out = chat_completion_sync(
            system_prompt=sysmsg, messages=[{"role": "user", "content": data_blob[:13000]}],
            max_tokens=8000, temperature=0.5, model="groq-llama-3.3-70b", prefer_paid=True) or ""
        if out.strip() and not out.lstrip().startswith(("[LLM unavailable]", "[server error]")):
            detail_en = out.strip()
            try:
                ko_tables = ("Section 2 표:\n" + cat_ko + "\n\nSection 3 표:\n" + reg_ko
                             + "\n\nSection 4 표:\n" + top_ko + (("\n\nSection 5 표:\n" + ob_ko) if ob_ko else ""))
                ko_sys = (
                    "You are a professional Korean financial translator. Translate the ENTIRE "
                    "English real-estate report into natural professional Korean (존댓말). "
                    "Translate EVERYTHING incl. section headings; NO English prose. Keep every "
                    "number, %, 원/억 amount and address IDENTICAL. Preserve all Markdown + tables. "
                    f"Replace the English tables with these EXACT Korean tables:\n{ko_tables}\n"
                    "Output ONLY the Korean Markdown report.")
                ko_out = chat_completion_sync(
                    system_prompt=ko_sys, messages=[{"role": "user", "content": detail_en[:22000]}],
                    max_tokens=13000, temperature=0.3, model="groq-llama-3.3-70b", prefer_paid=True) or ""
                if ko_out.strip() and not ko_out.lstrip().startswith(("[LLM unavailable]", "[server error]")) \
                        and len(ko_out.strip()) > 400:
                    detail_ko = ko_out.strip()
            except Exception as e:
                log.warning(f"realty_report: KO translation failed: {str(e)[:100]}")
    except Exception as e:
        log.warning(f"realty_report: LLM compose failed: {str(e)[:120]}")

    if not detail_en:
        detail_en = (f"# Real Estate Agent Daily Report\n*{kst_date}*\n\n## 1. 부동산 포트폴리오 개요\n{sum_en}\n\n"
                     + (f"## 2. 구분별 구성\n{cat_en}\n\n" if cat_en else "")
                     + (f"## 5. 공매(OnBid) 기회\n{ob_en}\n" if ob_en else ""))
    if not detail_ko:
        detail_ko = detail_en

    return {
        "agent_type": "realty", "name": "Real Estate Agent Report", "emoji": "🏠",
        "status": status,
        "summary_en": sum_en, "summary_ko": sum_ko,
        "detail_en": detail_en, "detail_ko": detail_ko,
        "table_en": table_ko, "table_ko": table_ko,
        "data": {"count": agg["count"], "total_value": agg["total_value"],
                 "by_category": agg["by_category"], "onbid": len(onbid)},
        "source": "Triple H listing workbook + OnBid 공매" + ("" if have else " (listings unavailable)"),
    }
