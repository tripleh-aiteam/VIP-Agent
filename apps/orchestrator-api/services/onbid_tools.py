"""
onbid_tools — OnBid (온비드 / KAMCO public-auction) integration for the assistant.

OnBid is Korea's public asset-disposal/auction platform run by KAMCO. This wraps
the next-gen OnBid OpenAPI so the assistant can SEARCH current public-auction
items (공매 물건) — real estate, vehicles, equipment, rights — by region,
category and price, instead of the user browsing onbid.co.kr manually.

The API key is read from env `ONBID_SERVICE_KEY` (data.go.kr serviceKey) — never
hardcoded. Set it on the orchestrator (Render).

What the OnBid OpenAPI does (and doesn't) support with this key:
  - Category filter (CTGR_HIRK_ID) works server-side: 10000=부동산(real estate),
    12000=자동차(vehicles), 16000=건축/건설, 11000=권리/증권, etc.
  - Region (SIDO) filter does NOT work server-side (the key/operation rejects
    every region code/name), so region is filtered CLIENT-side by matching the
    item address (LDNM_ADRS / NMRD_ADRS).
  - Verified operations (resultCode 00):
      KamcoPblsalThingInquireSvc/getKamcoPbctCltrList   (full KAMCO catalogue,
          category-filterable, paged — the main search source)
      ThingInfoInquireSvc/getUnifyDegression50PerCltrList  (active, ~hundreds)
      ThingInfoInquireSvc/getUnifyDeadlineCltrList         (closing soon)
      ThingInfoInquireSvc/getUnifyInterestTop20CltrList    (most-watched)
      ThingInfoInquireSvc/getUnifyClickTop20CltrList       (most-clicked)
      ThingInfoInquireSvc/getUnifyNewCltrList              (newest)
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

_BASE = "http://openapi.onbid.co.kr/newopenapi/services"
_KAMCO_OP = "KamcoPblsalThingInquireSvc/getKamcoPbctCltrList"
# Small, fully-fetchable "current highlights" that add region/category variety.
_ACTIVE_OPS = [
    "ThingInfoInquireSvc/getUnifyDegression50PerCltrList",
    "ThingInfoInquireSvc/getUnifyDeadlineCltrList",
    "ThingInfoInquireSvc/getUnifyInterestTop20CltrList",
    "ThingInfoInquireSvc/getUnifyClickTop20CltrList",
    "ThingInfoInquireSvc/getUnifyNewCltrList",
]

# Top-level OnBid category codes (getOnbidTopCodeInfo).
_CATEGORY_CODES: dict[str, str] = {
    "real_estate": "10000", "vehicle": "12000", "rights": "11000",
    "construction": "16000", "agriculture": "13000", "fishery": "14000",
    "mining": "15000", "it": "17000", "manufacturing": "21000",
    "transport": "32000", "art": "28000", "sports": "27000",
}

# Korean CTGR_FULL_NM fragments per category — used to filter items CLIENT-side
# (the active highlight lists aren't category-filtered server-side).
_CATEGORY_FRAGMENTS: dict[str, tuple[str, ...]] = {
    "real_estate": ("건물", "토지", "주거", "상가", "오피스텔", "아파트", "주택",
                    "임야", "대지", "근린", "공장", "업무용", "복합용", "전", "답"),
    "vehicle": ("자동차", "승용차", "차량", "화물차", "이륜", "버스", "트럭", "특수차"),
    "rights": ("유가증권", "증권", "권리", "회원권", "주식", "채권"),
    "construction": ("건축", "건설", "중장비", "건설기계"),
    "transport": ("운송", "선박", "항공", "중기"),
}

# Map free-text (EN/KO) → category key.
_CATEGORY_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("부동산", "real estate", "real-estate", "property", "building", "건물",
      "빌딩", "아파트", "apartment", "주택", "house", "home", "토지", "land",
      "상가", "오피스텔", "원룸", "근린", "공장", "factory", "warehouse", "창고"),
     "real_estate"),
    (("자동차", "차량", "승용차", "car", "cars", "vehicle", "vehicles", "차"),
     "vehicle"),
    (("권리", "증권", "회원권", "rights", "securities", "membership", "stock"),
     "rights"),
    (("건축", "건설", "construction"), "construction"),
    (("운송", "선박", "ship", "vessel", "transport"), "transport"),
]

# 시도 short-form → match terms. English aliases map to the Korean term.
_REGION_ALIASES: dict[str, str] = {
    "jeju": "제주", "seoul": "서울", "busan": "부산", "pusan": "부산",
    "daegu": "대구", "incheon": "인천", "gwangju": "광주", "daejeon": "대전",
    "ulsan": "울산", "sejong": "세종", "gyeonggi": "경기", "gangwon": "강원",
    "chungbuk": "충청북", "chungnam": "충청남", "jeonbuk": "전라북",
    "jeonnam": "전라남", "gyeongbuk": "경상북", "gyeongnam": "경상남",
}


def _service_key() -> str:
    return os.getenv("ONBID_SERVICE_KEY", "").strip()


def _to_int(v: str) -> int:
    try:
        return int(str(v).strip())
    except Exception:
        return 0


def _fmt_won(v: int) -> str:
    return f"{v:,}원" if v else ""


def _fmt_dtm(v: str) -> str:
    v = (v or "").strip()
    if len(v) >= 12:
        return f"{v[0:4]}-{v[4:6]}-{v[6:8]} {v[8:10]}:{v[10:12]}"
    return v


def _detect_category_key(text: str) -> str | None:
    t = (text or "").lower()
    for terms, key in _CATEGORY_KEYWORDS:
        if any(term.lower() in t for term in terms):
            return key
    return None


def _region_term(region: str) -> str:
    """Normalise a region argument to a Korean substring to match in addresses."""
    r = (region or "").strip().lower()
    if not r:
        return ""
    for en, ko in _REGION_ALIASES.items():
        if en in r:
            return ko
    # Already Korean (or unknown) — strip common suffixes for loose matching.
    raw = region.strip()
    for suf in ("특별자치도", "특별자치시", "광역시", "특별시", "도", "시"):
        if raw.endswith(suf) and len(raw) > len(suf):
            return raw[: -len(suf)]
    return raw


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
        min_bid = _to_int(g("MIN_BID_PRC"))
        appraisal = _to_int(g("APSL_ASES_AVG_AMT"))
        out.append({
            "id": g("CLTR_NO") or g("CLTR_MNMT_NO"),
            "name": name,
            "category": g("CTGR_FULL_NM"),
            "address": g("LDNM_ADRS") or g("NMRD_ADRS"),
            "dispose_method": g("DPSL_MTD_NM"),
            "bid_method": g("BID_MTD_NM"),
            "min_bid": _fmt_won(min_bid),
            "appraisal": _fmt_won(appraisal),
            "_min_bid": min_bid,
            "_appraisal": appraisal,
            "fee_rate": g("FEE_RATE"),
            "bid_open": _fmt_dtm(g("PBCT_BEGN_DTM")),
            "bid_close": _fmt_dtm(g("PBCT_CLS_DTM")),
            "status": g("PBCT_CLTR_STAT_NM"),
            "fail_count": g("USCBD_CNT"),
            "detail": g("GOODS_NM"),
        })
    return out


def _fetch(op: str, params: dict[str, Any], timeout: float = 9.0) -> list[dict[str, Any]]:
    """Fetch one operation. Creates its own client so it is safe to call from
    multiple threads concurrently."""
    key = _service_key()
    if not key:
        return []
    q = {"serviceKey": key, "pageNo": 1, "numOfRows": 100, **params}
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(f"{_BASE}/{op}", params=q)
        if r.status_code != 200 or "<resultCode>00</resultCode>" not in r.text:
            return []
        return _parse_items(r.text)
    except Exception as e:
        log.warning(f"onbid {op} failed: {e}")
        return []


def tool_onbid_search(
    keyword: str = "",
    region: str = "",
    category: str = "",
    sort: str = "",
    limit: int = 8,
    db=None,
    **_kw,
) -> dict[str, Any]:
    """Search live OnBid (온비드 / KAMCO) public-auction items (공매 물건).

    Args:
      keyword:  free text to match in item name / detail (e.g. '아파트', 'E300').
      region:   province/city to filter by (KO or EN: '제주', 'Jeju', '서울', 'Seoul').
                Filtered client-side against the item address.
      category: item type — 'real estate'/'부동산', 'car'/'자동차', 'construction',
                'rights', 'transport'. Maps to OnBid category for server-side filter.
      sort:     'expensive'/'price_desc' (most expensive first) or
                'cheap'/'price_asc'. Sorts by appraisal then minimum bid.
      limit:    max items (1-20, default 8).
    """
    if not _service_key():
        return {
            "ok": False,
            "error": "OnBid is not configured — set ONBID_SERVICE_KEY on the server "
                     "(data.go.kr serviceKey for 차세대 온비드 / KAMCO).",
        }

    # Resolve category from explicit arg or by sniffing keyword/region text.
    cat_key = _detect_category_key(f"{category} {keyword}")
    cat_code = _CATEGORY_CODES.get(cat_key) if cat_key else None
    cat_frags = _CATEGORY_FRAGMENTS.get(cat_key, ()) if cat_key else ()
    rterm = _region_term(region) or _region_term(keyword)
    kw = (keyword or "").strip().lower()
    # If the keyword was purely a region/category cue, don't also name-filter on it.
    if kw and (kw == (region or "").strip().lower() or _detect_category_key(keyword)
               or _region_term(keyword)):
        kw = ""

    try:
        lim = max(1, min(int(limit or 8), 20))
    except Exception:
        lim = 8

    # Normalise sort intent from either the explicit arg or the keyword text.
    st = f"{sort} {keyword}".lower()
    if any(w in st for w in ("price_desc", "expensive", "highest", "비싼",
                             "고가", "top", "most")):
        want_sort = "price_desc"
    elif any(w in st for w in ("price_asc", "cheap", "저렴", "lowest", "싼")):
        want_sort = "price_asc"
    else:
        want_sort = ""

    pool: dict[str, dict[str, Any]] = {}

    def add(items: list[dict[str, Any]]) -> None:
        for it in items:
            k = it.get("id") or it.get("name")
            if k and k not in pool:
                pool[k] = it

    def matches(it: dict[str, Any]) -> bool:
        if rterm and rterm not in (it.get("address") or ""):
            return False
        if cat_frags and not any(f in (it.get("category") or "") for f in cat_frags):
            return False
        if kw:
            hay = f"{it.get('name','')} {it.get('detail','')} {it.get('category','')}".lower()
            if kw not in hay:
                return False
        return True

    def n_matches() -> int:
        return sum(1 for it in pool.values() if matches(it))

    from concurrent.futures import ThreadPoolExecutor

    # 1) Active "highlight" lists in parallel — fast, current, region-diverse.
    #    These alone usually answer region/category queries.
    with ThreadPoolExecutor(max_workers=6) as ex:
        for items in ex.map(lambda op: _fetch(op, {"numOfRows": 500}), _ACTIVE_OPS):
            add(items)

    # 2) Top up from the big KAMCO catalogue ONLY if we still need more.
    #    It is slow, so fetch several pages in parallel with a short timeout.
    if n_matches() < lim and (rterm or cat_frags or kw):
        kamco_params: dict[str, Any] = {"numOfRows": 100}
        if cat_code:
            kamco_params["CTGR_HIRK_ID"] = cat_code
        pages = list(range(1, 7))
        with ThreadPoolExecutor(max_workers=6) as ex:
            for items in ex.map(
                lambda p: _fetch(_KAMCO_OP, {**kamco_params, "pageNo": p}, timeout=8),
                pages,
            ):
                add(items)

    results = [it for it in pool.values() if matches(it)]

    if want_sort == "price_desc":
        results.sort(key=lambda it: (it["_appraisal"], it["_min_bid"]), reverse=True)
    elif want_sort == "price_asc":
        results.sort(key=lambda it: (it["_appraisal"] or it["_min_bid"] or 1 << 62))

    out_items = []
    for it in results[:lim]:
        out_items.append({k: v for k, v in it.items() if not k.startswith("_")})

    note = None
    if (rterm or kw) and not out_items:
        note = (f"No current OnBid auction items matched "
                f"{('region '+region) if rterm else ''}{(' / '+keyword) if kw else ''}. "
                f"OnBid only lists assets currently up for public auction (공매), "
                f"not all properties — there may be none in that area right now.")

    return {
        "ok": True,
        "source": "OnBid (온비드 / 한국자산관리공사 공매)",
        "filters": {"region": region or None, "category": category or None,
                    "keyword": keyword or None, "sort": want_sort or None},
        "count": len(out_items),
        "total_scanned": len(pool),
        "items": out_items,
        "note": note,
    }
