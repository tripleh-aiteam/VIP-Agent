"""
molit_tools — 국토교통부 실거래가 (MOLIT real-transaction-price) integration.

OnBid only knows assets currently up for public auction. To answer "how much is
this property worth / 시세 / 얼마" for an ordinary address, we use the government
실거래가 (actual recorded sale prices) API at data.go.kr (provider 1613000).

Key is read from env `MOLIT_SERVICE_KEY`, falling back to `ONBID_SERVICE_KEY`
(data.go.kr keys are account-wide — once the RTMS services are subscribed, the
same key works). Never hardcoded.

The user must subscribe (활용신청) these services on data.go.kr:
  국토교통부_아파트 매매 실거래가 / 단독·다가구 / 연립·다세대 / 오피스텔 / 토지 매매.
Until then the API returns HTTP 'Forbidden' and this tool reports not-configured.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any

import httpx

try:
    import defusedxml.ElementTree as ET  # type: ignore
except Exception:  # pragma: no cover
    import xml.etree.ElementTree as ET  # type: ignore

from services.logger import log
from services.molit_lawd import resolve_lawd

_BASE = "https://apis.data.go.kr/1613000"

# property type -> (service path, operation)
_ENDPOINTS: dict[str, tuple[str, str]] = {
    "apartment": ("RTMSDataSvcAptTradeDev", "getRTMSDataSvcAptTradeDev"),
    "house": ("RTMSDataSvcSHTrade", "getRTMSDataSvcSHTrade"),
    "villa": ("RTMSDataSvcRHTrade", "getRTMSDataSvcRHTrade"),
    "officetel": ("RTMSDataSvcOffiTrade", "getRTMSDataSvcOffiTrade"),
    "land": ("RTMSDataSvcLandTrade", "getRTMSDataSvcLandTrade"),
}

_TYPE_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("아파트", "apartment", "apt", "apꞇ"), "apartment"),
    (("단독", "다가구", "단독주택", "주택", "house", "detached"), "house"),
    (("연립", "다세대", "빌라", "villa", "rowhouse"), "villa"),
    (("오피스텔", "officetel", "offi"), "officetel"),
    (("토지", "땅", "대지", "임야", "land", "lot"), "land"),
]


def _service_key() -> str:
    return (os.getenv("MOLIT_SERVICE_KEY") or os.getenv("ONBID_SERVICE_KEY") or "").strip()


def _detect_type(text: str) -> str | None:
    t = (text or "").lower()
    for terms, key in _TYPE_KEYWORDS:
        if any(term.lower() in t for term in terms):
            return key
    return None


def _recent_yyyymm(n: int) -> list[str]:
    """Last n year-months (YYYYMM), newest first, starting last month (current
    month's filings are usually incomplete)."""
    y, m = date.today().year, date.today().month
    out: list[str] = []
    # start from previous month
    m -= 1
    if m == 0:
        y, m = y - 1, 12
    for _ in range(n):
        out.append(f"{y}{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out


def _g(item, *tags: str) -> str:
    for t in tags:
        v = item.findtext(t)
        if v is not None and v.strip():
            return v.strip()
    return ""


def _fmt_amount(man: int) -> str:
    """만원 integer -> human '12억 3,400만원'."""
    if not man:
        return ""
    eok, rem = divmod(man, 10000)
    if eok and rem:
        return f"{eok}억 {rem:,}만원"
    if eok:
        return f"{eok}억원"
    return f"{rem:,}만원"


def _parse(xml_text: str, ptype: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return out
    for it in root.iter("item"):
        amt_raw = _g(it, "거래금액", "dealAmount").replace(",", "").strip()
        try:
            man = int(amt_raw)
        except Exception:
            continue
        dong = _g(it, "법정동", "umdNm")
        y = _g(it, "년", "dealYear")
        mo = _g(it, "월", "dealMonth")
        d = _g(it, "일", "dealDay")
        area = _g(it, "전용면적", "excluUseAr", "연면적", "totalFloorAr",
                  "대지면적", "plottageAr", "거래면적")
        name = _g(it, "아파트", "aptNm", "연립다세대", "mhouseNm", "오피스텔",
                  "offiNm", "단지명")
        out.append({
            "type": ptype,
            "amount_man": man,
            "amount": _fmt_amount(man),
            "dong": dong,
            "name": name,
            "area_m2": area,
            "build_year": _g(it, "건축년도", "buildYear"),
            "floor": _g(it, "층", "floor"),
            "jibun": _g(it, "지번", "jibun"),
            "date": f"{y}-{int(mo):02d}-{int(d):02d}" if (y and mo and d) else "",
            "_ym": f"{y}{int(mo):02d}" if (y and mo) else "",
        })
    return out


def _fetch(path: str, op: str, lawd: str, ym: str) -> list[dict[str, Any]]:
    key = _service_key()
    if not key:
        return "forbidden"  # type: ignore
    try:
        with httpx.Client(timeout=10) as c:
            r = c.get(f"{_BASE}/{path}/{op}", params={
                "serviceKey": key, "LAWD_CD": lawd, "DEAL_YMD": ym,
                "numOfRows": 200, "pageNo": 1,
            })
        body = r.text or ""
        if r.status_code == 403 or body.strip() == "Forbidden":
            return "forbidden"  # type: ignore
        return body
    except Exception as e:
        log.warning(f"molit {op} {lawd}/{ym} failed: {e}")
        return ""


def tool_realprice_search(
    region: str = "",
    dong: str = "",
    property_type: str = "",
    sort: str = "",
    months: int = 4,
    limit: int = 8,
    db=None,
    **_kw,
) -> dict[str, Any]:
    """Look up actual recorded sale prices (국토교통부 실거래가) for a Korean area.

    Args:
      region: 시군구 (송파구 / Songpa / 수원시) — required to resolve the area code.
      dong:   optional 법정동 to narrow within the 시군구 (e.g. 거여동).
      property_type: 'apartment'/'아파트', 'house'/'단독주택', 'villa'/'연립다세대',
                     'officetel'/'오피스텔', 'land'/'토지'. Defaults to apartment.
      sort:   'expensive' (high→low) or 'cheap' (low→high) or '' (recent first).
      months: how many recent months to scan (default 4).
      limit:  max transactions to return (1-20).
    """
    if not _service_key():
        return {"ok": False, "error": "실거래가 API not configured — set "
                "MOLIT_SERVICE_KEY (or subscribe the RTMS 실거래가 services to the "
                "data.go.kr account behind ONBID_SERVICE_KEY)."}

    codes = resolve_lawd(region) or resolve_lawd(dong)
    if not codes:
        return {"ok": False, "error": f"I don't have the area code for '{region}'. "
                "Tell me the 시군구 (e.g. 송파구, 수원시) and I'll look it up."}

    ptype = _detect_type(f"{property_type} {dong} {region}") or "apartment"
    path, op = _ENDPOINTS[ptype]
    try:
        n_months = max(1, min(int(months or 4), 12))
    except Exception:
        n_months = 4
    try:
        lim = max(1, min(int(limit or 8), 20))
    except Exception:
        lim = 8

    yms = _recent_yyyymm(n_months)
    # bound total calls: codes × months
    jobs = [(c, ym) for c in codes[:2] for ym in yms]

    from concurrent.futures import ThreadPoolExecutor
    forbidden = False
    items: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for res in ex.map(lambda j: _fetch(path, op, j[0], j[1]), jobs):
            if res == "forbidden":
                forbidden = True
                continue
            if isinstance(res, str) and res:
                items.extend(_parse(res, ptype))

    if forbidden and not items:
        return {"ok": False, "error": "실거래가 API returned Forbidden — the "
                "data.go.kr key isn't subscribed to the RTMS 실거래가 services yet. "
                "Subscribe them (활용신청) and it will work with the same key."}

    d = (dong or "").strip().replace("동", "")
    if d:
        items = [it for it in items if d in (it.get("dong") or "")]

    want = (sort or "").strip().lower()
    if any(w in want for w in ("expensive", "high", "비싼", "고가")):
        items.sort(key=lambda it: it["amount_man"], reverse=True)
    elif any(w in want for w in ("cheap", "low", "저렴", "싼")):
        items.sort(key=lambda it: it["amount_man"])
    else:
        items.sort(key=lambda it: (it.get("_ym", ""), it["amount_man"]), reverse=True)

    amounts = [it["amount_man"] for it in items]
    summary = None
    if amounts:
        summary = {
            "count": len(amounts),
            "avg": _fmt_amount(sum(amounts) // len(amounts)),
            "min": _fmt_amount(min(amounts)),
            "max": _fmt_amount(max(amounts)),
        }

    out = []
    for it in items[:lim]:
        out.append({k: v for k, v in it.items() if not k.startswith("_")})

    type_ko = {"apartment": "아파트", "house": "단독/다가구", "villa": "연립/다세대",
               "officetel": "오피스텔", "land": "토지"}[ptype]
    note = None
    if not out:
        note = (f"No recorded {type_ko} sales found in {region}"
                f"{(' '+dong) if dong else ''} over the last {n_months} months. "
                f"Try a different property type or a wider area.")
    return {
        "ok": True,
        "source": "국토교통부 실거래가 (MOLIT actual sale prices)",
        "region": region, "dong": dong or None, "property_type": type_ko,
        "summary": summary,
        "count": len(out),
        "transactions": out,
        "note": note,
    }
