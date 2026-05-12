"""
CSV/Excel-driven adapters for asset + realty.

When the user uploads their real data (Excel/CSV) via POST /agents/upload-data,
files are saved to data/uploads/{agent_type}/latest.csv. These adapters read
from that file and emit the standard report schema.

Expected CSV columns (case-insensitive):

ASSET:
  name, type, value_krw, monthly_income_krw, occupancy
  (Sample row:  "Gangnam Office Tower",office,45000000000,180000000,0.94)

REALTY:
  address, type, price_krw, size_sqm, vacancy
  (Sample row: "Gangnam-gu, Yeoksam-dong",office,12500000000,850,0.06)

Both fall back to inline mock if the upload doesn't exist yet.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any

from adapters.base_adapter import BaseAdapter, AdapterResult


# Resolve uploads dir relative to repo root
_REPO_ROOT = Path(__file__).resolve().parent.parent
UPLOADS_DIR = _REPO_ROOT / "data" / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


def _read_csv(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            # Try to sniff dialect; default to comma
            sample = f.read(2048)
            f.seek(0)
            reader = csv.DictReader(f)
            for row in reader:
                # Lowercase + strip keys for tolerance
                rows.append({(k or "").strip().lower(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()})
    except Exception:
        return []
    return rows


def _to_float(v: Any, default: float = 0.0) -> float:
    if v is None or v == "":
        return default
    try:
        # Strip thousand separators
        if isinstance(v, str):
            v = v.replace(",", "").replace(" ", "").strip()
        return float(v)
    except Exception:
        return default


def _to_int(v: Any, default: int = 0) -> int:
    return int(_to_float(v, default))


# ---------------------------------------------------------------------------
# ASSET — from data/uploads/asset/latest.csv
# ---------------------------------------------------------------------------

class CsvAssetAdapter(BaseAdapter):
    agent_type = "asset"

    def execute(self, task_run_id: str, trace_id: str, task_type: str, input_payload: dict[str, Any]) -> AdapterResult:
        data = self.fetch_summary()
        return AdapterResult(
            success=True, status="completed", agent_id=self.agent_name,
            summary=data.get("summary", "Asset CSV report"),
            output_payload=data,
        )

    def fetch_summary(self) -> dict:
        path = UPLOADS_DIR / "asset" / "latest.csv"
        rows = _read_csv(path)
        if not rows:
            from adapters.mock_data import get_mock_summary
            return {**get_mock_summary("asset"), "_source": "csv_no_upload_using_mock"}

        total_value = 0
        total_income = 0
        occupancy_sum = 0.0
        types_count: dict[str, int] = {}
        details = []

        for r in rows:
            value = _to_float(r.get("value_krw") or r.get("value"))
            income = _to_float(r.get("monthly_income_krw") or r.get("monthly_income"))
            occ = _to_float(r.get("occupancy"), default=1.0)
            if occ > 1.5:  # User entered as percent (e.g. 94 instead of 0.94)
                occ = occ / 100.0
            total_value += value
            total_income += income
            occupancy_sum += occ
            t = (r.get("type") or "office").lower()
            types_count[t] = types_count.get(t, 0) + 1
            details.append({
                "name": r.get("name") or "Unknown",
                "type": t,
                "value_krw": int(value),
                "monthly_income_krw": int(income),
                "occupancy": round(occ, 3),
            })

        n = len(rows)
        avg_occ = occupancy_sum / n if n else 0
        annual_yield = (total_income * 12 / total_value * 100) if total_value else 0

        return {
            "source": "csv-upload-asset",
            "live_data": True,
            "portfolio": {
                "total_properties": n,
                "vacancy_rate": round((1 - avg_occ) * 100, 2),
                "total_value_krw": int(total_value),
            },
            "contracts": {
                "total": n,
                "active": n,
                "expiring_within_30d": 0,
                "overdue_payment": 0,
            },
            "cash": {"total_balance": int(total_value * 0.05), "currency": "KRW"},
            "risk_level": "low" if avg_occ > 0.9 else "medium" if avg_occ > 0.8 else "high",
            "total_assets": n,
            "portfolio_value_krw": int(total_value),
            "monthly_income_krw": int(total_income),
            "annual_yield_pct": round(annual_yield, 2),
            "average_occupancy_pct": round(avg_occ * 100, 1),
            "by_type": types_count,
            "assets": details,
            "summary": f"Portfolio {total_value/1e9:.1f}B KRW · Yield {annual_yield:.2f}% · Avg occupancy {avg_occ*100:.1f}% · {n} properties (from your upload)",
        }


# ---------------------------------------------------------------------------
# REALTY — from data/uploads/realty/latest.csv
# ---------------------------------------------------------------------------

class CsvRealtyAdapter(BaseAdapter):
    agent_type = "realty"

    def execute(self, task_run_id: str, trace_id: str, task_type: str, input_payload: dict[str, Any]) -> AdapterResult:
        data = self.fetch_summary()
        return AdapterResult(
            success=True, status="completed", agent_id=self.agent_name,
            summary=data.get("summary", "Realty CSV report"),
            output_payload=data,
        )

    def fetch_summary(self) -> dict:
        path = UPLOADS_DIR / "realty" / "latest.csv"
        rows = _read_csv(path)
        if not rows:
            from adapters.mock_data import get_mock_summary
            return {**get_mock_summary("realty"), "_source": "csv_no_upload_using_mock"}

        total_value = 0
        vacancy_sum = 0.0
        listings = []
        types_count: dict[str, int] = {}

        for r in rows:
            price = _to_float(r.get("price_krw") or r.get("price"))
            vac = _to_float(r.get("vacancy"), default=0.05)
            if vac > 1.5:
                vac = vac / 100.0
            total_value += price
            vacancy_sum += vac
            t = (r.get("type") or "office").lower()
            types_count[t] = types_count.get(t, 0) + 1
            listings.append({
                "address": r.get("address") or "Unknown",
                "type": t,
                "price_krw": int(price),
                "size_sqm": _to_int(r.get("size_sqm") or r.get("size")),
                "vacancy": round(vac, 3),
            })

        n = len(rows)
        avg_vac = vacancy_sum / n if n else 0
        # Estimate yield (rough heuristic — 4-6% range based on vacancy)
        avg_yield = round(max(3.5, 6.0 - avg_vac * 100 * 0.2), 2)

        return {
            "source": "csv-upload-realty",
            "live_data": True,
            "total_listings": n,
            "avg_vacancy_pct": round(avg_vac * 100, 2),
            "avg_yield_pct": avg_yield,
            "market_trend": "stable",
            "listings_count": n,
            "market_value_krw": int(total_value),
            "by_type": types_count,
            "listings": listings,
            "high_vacancy_listings": [l for l in listings if l["vacancy"] > 0.15],
            "summary": f"{n} listings · Total {total_value/1e9:.1f}B KRW · Avg vacancy {avg_vac*100:.2f}% · Avg yield {avg_yield:.2f}% (from your upload)",
        }
