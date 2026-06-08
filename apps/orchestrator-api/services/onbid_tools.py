"""
onbid_tools — OnBid (온비드 / KAMCO public-auction) integration for the assistant.

OnBid is Korea's public asset-disposal/auction platform run by KAMCO. This wraps
the next-gen OnBid OpenAPI (`/newopenapi/services/ThingInfoInquireSvc/`) so the
chatbot can answer questions about current public-auction items (공매 물건):
property, vehicles, equipment, etc.

The API key is read from env `ONBID_SERVICE_KEY` (data.go.kr serviceKey) — never
hardcoded. Set it on the orchestrator (Render). Get/subscribe the key at
data.go.kr → 한국자산관리공사_차세대 온비드 (ThingInfoInquireSvc).

Verified operations (resultCode 00):
  - getInterestTop20    : top-20 most-watched current auction items
  - getUnifyNewCltrList  : newly-listed unified items
"""

from __future__ import annotations

import os
from typing import Any

import httpx

try:
    # defusedxml guards against XXE / billion-laughs on untrusted external XML.
    import defusedxml.ElementTree as ET  # type: ignore
except Exception:  # pragma: no cover - fallback if dep missing
    import xml.etree.ElementTree as ET  # type: ignore

from services.logger import log

_BASE = "http://openapi.onbid.co.kr/newopenapi/services/ThingInfoInquireSvc"


def _service_key() -> str:
    return os.getenv("ONBID_SERVICE_KEY", "").strip()


def _fmt_won(v: str) -> str:
    try:
        return f"{int(v):,}원"
    except Exception:
        return v or ""


def _fmt_dtm(v: str) -> str:
    # YYYYMMDDHHMMSS -> YYYY-MM-DD HH:MM
    v = (v or "").strip()
    if len(v) >= 12:
        return f"{v[0:4]}-{v[4:6]}-{v[6:8]} {v[8:10]}:{v[10:12]}"
    return v


def _parse_items(xml_text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return out
    for it in root.iter("item"):
        def g(tag: str) -> str:
            return (it.findtext(tag) or "").strip()
        name = g("CLTR_NM")
        if not name:
            continue
        out.append({
            "id": g("CLTR_NO"),
            "name": name,
            "category": g("CTGR_FULL_NM"),
            "address": g("LDNM_ADRS") or g("NMRD_ADRS"),
            "dispose_method": g("DPSL_MTD_NM"),       # 매각 / 임대
            "bid_method": g("BID_MTD_NM"),            # 최고가방식 등
            "min_bid": _fmt_won(g("MIN_BID_PRC")),
            "appraisal": _fmt_won(g("APSL_ASES_AVG_AMT")),
            "fee_rate": g("FEE_RATE"),
            "bid_open": _fmt_dtm(g("PBCT_BEGN_DTM")),
            "bid_close": _fmt_dtm(g("PBCT_CLS_DTM")),
            "status": g("PBCT_CLTR_STAT_NM"),
            "interest_count": g("CLTR_ITRS_CNT"),
            "mgmt_no": g("CLTR_MNMT_NO"),
        })
    return out


def _fetch(op: str, rows: int = 50) -> list[dict[str, Any]]:
    key = _service_key()
    if not key:
        return []
    try:
        r = httpx.get(
            f"{_BASE}/{op}",
            params={"serviceKey": key, "pageNo": 1, "numOfRows": rows},
            timeout=15,
        )
        if r.status_code != 200 or "<resultCode>00</resultCode>" not in r.text:
            log.warning(f"onbid {op}: non-OK response ({r.status_code})")
            return []
        return _parse_items(r.text)
    except Exception as e:
        log.warning(f"onbid {op} failed: {e}")
        return []


def tool_onbid_search(keyword: str = "", limit: int = 8, db=None, **_kw) -> dict[str, Any]:
    """Search current OnBid (온비드/KAMCO) public-auction items. Optional keyword
    filters by name / address / category (e.g. 'car', '아파트', '대전', '승용차').
    Returns live auction items with min-bid, appraisal, bid dates, and status."""
    if not _service_key():
        return {
            "ok": False,
            "error": "OnBid is not configured — set ONBID_SERVICE_KEY on the server "
                     "(data.go.kr serviceKey for 차세대 온비드 ThingInfoInquireSvc).",
        }
    # Pull popular + newly-listed items, dedupe by id.
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for op in ("getInterestTop20", "getUnifyNewCltrList"):
        for it in _fetch(op):
            k = it.get("id") or it.get("name")
            if k and k not in seen:
                seen.add(k)
                items.append(it)

    kw = (keyword or "").strip().lower()
    if kw:
        items = [
            it for it in items
            if kw in (f"{it['name']} {it['address']} {it['category']}").lower()
        ]

    try:
        lim = max(1, min(int(limit or 8), 20))
    except Exception:
        lim = 8
    items = items[:lim]
    return {
        "ok": True,
        "source": "OnBid (온비드 / 한국자산관리공사 공매)",
        "keyword": keyword or None,
        "count": len(items),
        "items": items,
    }
