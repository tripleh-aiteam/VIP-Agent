"""
asset_data — read the company's real asset data from Supabase (loaded from the
자산관리 Excel by scripts/import_assets.py) for the Asset report + chatbot.

Two tables:
  asset_portfolio  — 총괄 line items (category / description / value / rent / deposit)
  asset_units      — per-unit / per-parcel detail (heterogeneous, + JSONB extra)

Everything is best-effort: if the tables don't exist yet (importer not run) the
functions return empty structures so the report falls back gracefully.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from services.logger import log

# status text → occupancy class
_OCCUPIED_KW = ("임차", "월세", "임대", "운영")
_VACANT_KW = ("공실", "미임대", "미분양")


def _f(v) -> float:
    try:
        return float(v)
    except Exception:
        return 0.0


def _occ_class(status: str | None) -> str:
    s = str(status or "")
    if any(k in s for k in _VACANT_KW):
        return "vacant"
    if any(k in s for k in _OCCUPIED_KW):
        return "occupied"
    return "other"


def load_asset_data(db) -> dict[str, Any]:
    """Return the full real-asset picture, or {'available': False} if not loaded."""
    out: dict[str, Any] = {"available": False, "portfolio": [], "units": [],
                           "totals": {}, "by_category": [], "by_property": [], "occupancy": {}}
    try:
        db.execute(text("SELECT 1 FROM asset_portfolio LIMIT 1"))
    except Exception:
        # table missing → not imported yet
        try:
            db.rollback()
        except Exception:
            pass
        return out

    try:
        pf = [dict(r._mapping) for r in db.execute(text(
            "SELECT category, description, sale_price, deposit, monthly_rent "
            "FROM asset_portfolio ORDER BY sale_price DESC NULLS LAST"))]
        units = [dict(r._mapping) for r in db.execute(text(
            "SELECT property, category, unit_no, address, area_pyeong, price, "
            "official_price, market_value, deposit, monthly_rent, status, tenant "
            "FROM asset_units"))]
    except Exception as e:
        log.warning(f"asset_data: query failed: {str(e)[:120]}")
        try:
            db.rollback()
        except Exception:
            pass
        return out

    if not pf and not units:
        return out

    out["available"] = True
    out["portfolio"] = pf
    out["units"] = units

    out["totals"] = {
        "value": sum(_f(p.get("sale_price")) for p in pf),
        "deposit": sum(_f(p.get("deposit")) for p in pf),
        "monthly_rent": sum(_f(p.get("monthly_rent")) for p in pf),
        "items": len(pf),
        "units": len(units),
    }

    cats: dict[str, dict] = {}
    for p in pf:
        c = p.get("category") or "기타"
        d = cats.setdefault(c, {"category": c, "count": 0, "value": 0.0, "monthly_rent": 0.0})
        d["count"] += 1
        d["value"] += _f(p.get("sale_price"))
        d["monthly_rent"] += _f(p.get("monthly_rent"))
    out["by_category"] = sorted(cats.values(), key=lambda x: x["value"], reverse=True)

    props: dict[str, dict] = {}
    occ = vac = 0
    for u in units:
        pr = u.get("property") or "?"
        d = props.setdefault(pr, {"property": pr, "units": 0, "occupied": 0, "vacant": 0,
                                  "value": 0.0, "monthly_rent": 0.0})
        d["units"] += 1
        d["value"] += _f(u.get("price")) or _f(u.get("market_value"))
        d["monthly_rent"] += _f(u.get("monthly_rent"))
        cls = _occ_class(u.get("status"))
        if cls == "occupied":
            d["occupied"] += 1
            occ += 1
        elif cls == "vacant":
            d["vacant"] += 1
            vac += 1
    out["by_property"] = sorted(props.values(), key=lambda x: x["value"], reverse=True)
    classified = occ + vac
    out["occupancy"] = {
        "occupied": occ, "vacant": vac, "classified": classified,
        "vacancy_rate": round(vac / classified * 100, 1) if classified else 0.0,
    }
    return out


# ---------------------------------------------------------------------------
# Markdown tables for the report (KRW formatted)
# ---------------------------------------------------------------------------

def _won(v) -> str:
    f = _f(v)
    if f == 0:
        return "—"
    if abs(f) >= 1e8:
        return f"{f/1e8:,.1f}억원"
    if abs(f) >= 1e4:
        return f"{f/1e4:,.0f}만원"
    return f"{f:,.0f}원"


def category_table(data: dict, ko: bool = True) -> str:
    rows = data.get("by_category") or []
    if not rows:
        return ""
    total = sum(r["value"] for r in rows) or 1
    head = ("| 구분 | 건수 | 자산가치 | 비중 | 월세 |\n|---|---|---|---|---|" if ko
            else "| Category | Items | Value | Share | Monthly rent |\n|---|---|---|---|---|")
    lines = [head]
    for r in rows:
        lines.append(f"| {r['category']} | {r['count']} | {_won(r['value'])} | "
                     f"{r['value']/total*100:.1f}% | {_won(r['monthly_rent'])} |")
    return "\n".join(lines)


def portfolio_table(data: dict, ko: bool = True) -> str:
    rows = data.get("portfolio") or []
    if not rows:
        return ""
    head = ("| 구분 | 내용 | 자산가치 | 보증금 | 월세 |\n|---|---|---|---|---|" if ko
            else "| Category | Asset | Value | Deposit | Monthly rent |\n|---|---|---|---|---|")
    lines = [head]
    for r in rows[:30]:
        lines.append(f"| {r.get('category') or ''} | {str(r.get('description') or '')[:40]} | "
                     f"{_won(r.get('sale_price'))} | {_won(r.get('deposit'))} | {_won(r.get('monthly_rent'))} |")
    return "\n".join(lines)


def property_table(data: dict, ko: bool = True) -> str:
    rows = data.get("by_property") or []
    if not rows:
        return ""
    head = ("| 자산(시트) | 호실수 | 점유 | 공실 | 자산가치 | 월세 |\n|---|---|---|---|---|---|" if ko
            else "| Property | Units | Occ | Vac | Value | Monthly rent |\n|---|---|---|---|---|---|")
    lines = [head]
    for r in rows:
        lines.append(f"| {r['property']} | {r['units']} | {r['occupied']} | {r['vacant']} | "
                     f"{_won(r['value'])} | {_won(r['monthly_rent'])} |")
    return "\n".join(lines)


def facts(data: dict) -> str:
    """Compact fact sheet for the LLM — the real numbers it may cite."""
    t = data.get("totals", {})
    o = data.get("occupancy", {})
    lines = [
        f"PORTFOLIO TOTAL: value={t.get('value',0):,.0f}원 (~{_f(t.get('value'))/1e8:,.0f}억), "
        f"deposit={t.get('deposit',0):,.0f}원, monthly_rent={t.get('monthly_rent',0):,.0f}원, "
        f"{t.get('items',0)} portfolio line items, {t.get('units',0)} detailed units/parcels.",
        "OCCUPANCY (from unit status): "
        f"occupied={o.get('occupied',0)}, vacant={o.get('vacant',0)}, vacancy_rate={o.get('vacancy_rate',0)}%.",
        "BY CATEGORY: " + "; ".join(
            f"{r['category']} {r['value']/1e8:,.1f}억({r['count']}건, 월세 {r['monthly_rent']:,.0f}원)"
            for r in data.get("by_category", [])),
    ]
    return "\n".join(lines)
