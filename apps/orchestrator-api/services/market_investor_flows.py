"""market_investor_flows.py — market-wide 투자자별 매매 (net buying by investor type).

The image-1 table: who is net buying/selling across 코스피 / 코스닥 — 개인 / 외국인 / 기관계
and the 기관 sub-types (금융투자 / 보험 / 투신 / 기타금융 / 은행 / 연기금 / 사모펀드 / 기타법인).

Source: Naver Finance 투자자별 매매동향 (works in- and after-market, no Kiwoom IP needed),
the reliable free feed. (The 선물/콜·풋옵션/주식선물/달러선물 derivative rows in the user's
screenshot are KRX-only and not yet included — noted in the payload.)

Unit: 백만원 (millions of KRW). Render-safe (httpx).
"""
from __future__ import annotations

import re
from typing import Any

_HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
# Naver column order after the date, left→right:
_COLS = ["개인", "외국인", "기관계", "금융투자", "보험", "투신",
         "기타금융", "은행", "연기금등", "사모펀드", "국가", "기타법인"]
_COLS_EN = ["Individual", "Foreign", "Institution", "Fin.Invest", "Insurance", "Trust",
            "Other Fin", "Bank", "Pension", "Private Fund", "Govt", "Other Corp"]
_MARKETS = [("01", "코스피", "KOSPI"), ("02", "코스닥", "KOSDAQ")]
_CACHE: dict[str, Any] = {"ts": None, "data": None}


def _to_int(s: str):
    s = (s or "").replace(",", "").strip()
    if not s or s in ("-",):
        return None
    try:
        return int(s)
    except Exception:
        return None


def _fetch_market(sosok: str, bizdate: str) -> dict | None:
    import httpx
    url = f"https://finance.naver.com/sise/investorDealTrendDay.naver?bizdate={bizdate}&sosok={sosok}"
    try:
        r = httpx.get(url, headers=_HDR, timeout=12)
        r.encoding = "euc-kr"
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", r.text, re.S):
            cells = [re.sub(r"<[^>]+>", "", c).replace("\xa0", "").strip()
                     for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            cells = [c for c in cells if c != ""]
            if cells and re.match(r"\d{2}\.\d{2}\.\d{2}", cells[0]):   # a date row
                vals = cells[1:]
                flows = {_COLS[i]: _to_int(vals[i]) for i in range(min(len(_COLS), len(vals)))}
                return {"date": cells[0], "flows": flows}
    except Exception:
        return None
    return None


def market_flows(bizdate: str | None = None) -> dict[str, Any]:
    """Today's (or `bizdate` YYYYMMDD) net-buying table for KOSPI + KOSDAQ by investor type."""
    import time
    if not bizdate:
        from services.kst import kst_now
        bizdate = kst_now().strftime("%Y%m%d")        # Naver needs an explicit date
    bd = bizdate
    now = time.monotonic()                            # 5-min cache (avoid hammering Naver)
    if _CACHE["data"] and _CACHE["ts"] and (now - _CACHE["ts"] < 300) and _CACHE["data"].get("_bd") == bd:
        return _CACHE["data"]
    out = {"unit": "백만원", "source": "NAVER 투자자별 매매동향",
           "columns": _COLS, "columns_en": _COLS_EN, "markets": [],
           "note": "선물·옵션·주식선물·달러선물은 미포함 (KRX 전용) — 추후 추가"}
    for sosok, ko, en in _MARKETS:
        m = _fetch_market(sosok, bd)
        out["markets"].append({"market": ko, "market_en": en,
                               "date": (m or {}).get("date"),
                               "flows": (m or {}).get("flows") or {}})
    out["_bd"] = bd
    _CACHE.update(ts=now, data=out)
    return out
