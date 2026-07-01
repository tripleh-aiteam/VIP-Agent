"""stock_resolver.py — robust KR stock name → ticker resolution (Milestone 1.1).

One place that understands a stock written ANY way: official name, English name,
common slang/abbreviation (하닉, 삼전, 현차, 엔솔…), or a typo (fuzzy match). Auto-covers
every tracked name (prediction_service.NAMES + the Wave-extra universe) plus a
hand-kept slang map, so the chatbot + tools all resolve identically.

Public API:
  resolve_one(text) -> (ticker|None, display_name|None)   # best single match in text
  find_all(text)    -> list[(ticker, display_name)]        # every stock mentioned
  display_name(ticker) -> str
"""
from __future__ import annotations

import difflib
import re
from typing import Optional

# Hand-kept slang / abbreviations / alt-spellings → 6-digit ticker. Auto-merged with
# the official names below. Keep keys lowercase.
_SLANG: dict[str, str] = {
    "하닉": "000660", "하이닉스": "000660", "스카이닉스": "000660", "sk하닉": "000660",
    "hynix": "000660", "skhynix": "000660", "skyhynix": "000660", "sk hynix": "000660",
    "sk하이닉스": "000660", "에스케이하이닉스": "000660",
    "삼전": "005930", "삼성전자": "005930", "samsung": "005930", "samsung electronics": "005930",
    "현차": "005380", "현대차": "005380", "현대자동차": "005380", "hyundai": "005380",
    "엔솔": "373220", "엘지엔솔": "373220", "lg엔솔": "373220", "lg에너지솔루션": "373220",
    "삼바": "207940", "삼성바이오": "207940", "삼성바이오로직스": "207940",
    "삼성sds": "018260", "삼성에스디에스": "018260", "삼성디에스": "018260",
    "엔씨": "036570", "엔씨소프트": "036570", "ncsoft": "036570",
    "하이브": "352820", "hybe": "352820",
    "skt": "017670", "sk텔레콤": "017670", "에스케이텔레콤": "017670", "sk telecom": "017670",
    "아모레": "090430", "아모레퍼시픽": "090430", "amorepacific": "090430",
    "포스코퓨처엠": "003670", "포스코케미칼": "003670",
    "cj제일제당": "097950", "제일제당": "097950",
    "lg생건": "051900", "엘지생활건강": "051900", "lg생활건강": "051900",
    "호텔신라": "008770",
    "카뱅": "323410", "카카오뱅크": "323410", "kakaobank": "323410",
    "대한항공": "003490", "kal": "003490", "korean air": "003490",
    "한국항공우주": "047810", "kai": "047810",
    "lig넥스원": "079550", "넥스원": "079550",
    "한화시스템": "272210",
    "한화에어로": "012450", "한화에어로스페이스": "012450", "hanwha aerospace": "012450",
    "한화오션": "042660",
    "hd현대중공업": "329180", "현대중공업": "329180",
    "hd한국조선해양": "009540", "한국조선해양": "009540",
    "삼성중공업": "010140",
    "한미반도체": "042700",
    "두산": "034020", "두산에너빌리티": "034020",
    "한전기술": "052690", "한국전력기술": "052690",
    "한전": "015760", "한국전력": "015760", "kepco": "015760",
    "에코프로비엠": "247540", "에코프로bm": "247540",
    "진에어": "272450", "하나투어": "039130",
    "에쓰오일": "010950", "에스오일": "010950", "s-oil": "010950", "soil": "010950",
    "gs": "078930",
    "메리츠": "138040", "메리츠금융지주": "138040",
    "모비스": "012330", "현대모비스": "012330",
    "포스코": "005490", "포스코홀딩스": "005490", "posco": "005490",
    "기아": "000270", "kia": "000270",
    "네이버": "035420", "naver": "035420",
    "카카오": "035720", "kakao": "035720",
    "셀트리온": "068270", "celltrion": "068270",
    "kb금융": "105560", "국민은행": "105560",
    "신한": "055550", "신한지주": "055550",
    "sk스퀘어": "402340",
    "삼성전기": "009150",
    "sk이노": "096770", "sk이노베이션": "096770",
    "현대로템": "064350",
    "lg전자": "066570", "엘지전자": "066570", "lg electronics": "066570",
    "크래프톤": "259960", "krafton": "259960",
    "kt": "030200",
}


def _official() -> dict[str, str]:
    """{ticker: display_name} for every tracked stock (39 + Wave-extra 12)."""
    out: dict[str, str] = {}
    try:
        from services.prediction_service import NAMES
        out.update({c: n for c, n in NAMES.items()})
    except Exception:
        pass
    try:
        from routers.predictions import WAVE_EXTRA_NAMES
        out.update(WAVE_EXTRA_NAMES)
    except Exception:
        pass
    return out


_TICKER_NAME: dict[str, str] = {}     # ticker -> display
_ALIAS: dict[str, str] = {}           # lowercased alias -> ticker


def _build() -> None:
    if _ALIAS:
        return
    off = _official()
    _TICKER_NAME.update(off)
    _TICKER_NAME.update({"069500": "KODEX 200"})
    # aliases: official names (both langs) + slang
    for c, n in off.items():
        if n:
            _ALIAS[n.lower()] = c
    _ALIAS.update(_SLANG)
    # ensure every slang ticker has a display name
    for a, c in _SLANG.items():
        _TICKER_NAME.setdefault(c, a)


def display_name(ticker: str) -> str:
    _build()
    return _TICKER_NAME.get(str(ticker).zfill(6), str(ticker))


_EN_NAMES = {
    "005930": "Samsung Electronics", "000660": "SK Hynix", "005380": "Hyundai Motor",
    "000270": "Kia", "035420": "NAVER", "035720": "Kakao", "009150": "Samsung Electro-Mechanics",
    "042700": "Hanmi Semiconductor", "012450": "Hanwha Aerospace", "079550": "LIG Nex1",
    "272210": "Hanwha Systems", "003490": "Korean Air", "034020": "Doosan Enerbility",
    "052690": "KEPCO E&C", "015760": "KEPCO", "066570": "LG Electronics", "017670": "SK Telecom",
    "030200": "KT", "005490": "POSCO Holdings", "006400": "Samsung SDI", "051910": "LG Chem",
    "373220": "LG Energy Solution", "207940": "Samsung Biologics", "068270": "Celltrion",
    "012330": "Hyundai Mobis", "105560": "KB Financial", "055550": "Shinhan", "402340": "SK Square",
    "096770": "SK Innovation", "064350": "Hyundai Rotem", "009540": "HD Korea Shipbuilding",
    "329180": "HD Hyundai Heavy", "010140": "Samsung Heavy", "042660": "Hanwha Ocean",
    "247540": "Ecopro BM", "272450": "Jin Air", "039130": "Hana Tour", "018260": "Samsung SDS",
    "010950": "S-OIL", "078930": "GS", "138040": "Meritz Financial", "036570": "NCSOFT",
    "352820": "HYBE", "259960": "Krafton", "323410": "KakaoBank", "003670": "POSCO Future M",
    "051900": "LG H&H", "090430": "AmorePacific", "097950": "CJ CheilJedang", "008770": "Hotel Shilla",
}


def display_name_en(ticker: str) -> str:
    """English display name (falls back to the Korean name if none)."""
    return _EN_NAMES.get(str(ticker).zfill(6)) or display_name(ticker)


_ALIAS_BY_LEN = None


def _aliases_longest_first() -> list[str]:
    global _ALIAS_BY_LEN
    if _ALIAS_BY_LEN is None:
        _build()
        _ALIAS_BY_LEN = sorted(_ALIAS.keys(), key=len, reverse=True)
    return _ALIAS_BY_LEN


def find_all(text: str) -> list[tuple[str, str]]:
    """Every distinct tracked stock mentioned in the text (by name/alias/slang/code).
    Longest alias first + span-consumption so 'SKT' doesn't also match 'KT', and short
    ascii aliases (gs/kt) need a word boundary so 'things' doesn't match 'gs'."""
    _build()
    found: dict[str, str] = {}          # ticker -> matched alias
    for code in re.findall(r"\b\d{6}\b", text or ""):
        if code in _TICKER_NAME or code in _ALIAS.values():
            found.setdefault(code, code)
    work = " " + (text or "").lower() + " "     # consumable copy
    for alias in _aliases_longest_first():
        if len(alias) < 2:
            continue
        if alias.isascii() and len(alias) <= 3:  # gs/kt/kai/skt → require word boundary
            m = re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", work)
            if not m:
                continue
            start, end = m.span()
        else:
            idx = work.find(alias)
            if idx == -1:
                continue
            start, end = idx, idx + len(alias)
        found.setdefault(_ALIAS[alias], alias)
        work = work[:start] + (" " * (end - start)) + work[end:]   # consume span
    return [(c, display_name(c)) for c in found]


def resolve_one(text: str) -> tuple[Optional[str], Optional[str]]:
    """Best single stock in the text: exact code → alias → substring → fuzzy typo."""
    _build()
    q = (text or "").strip()
    if q.isdigit() and len(q) == 6:
        return q, display_name(q)
    low = q.lower()
    if low in _ALIAS:
        c = _ALIAS[low]
        return c, display_name(c)
    hits = find_all(text)
    if hits:
        return hits[0]
    # fuzzy typo: match the whole query (and each token) against known aliases
    cands = [low] + [w for w in re.split(r"[\s,./]+", low) if len(w) >= 2]
    keys = list(_ALIAS.keys())
    for cand in cands:
        # 0.72 catches a 1-syllable Korean typo in a 4-syllable name (삼썽전자→삼성전자)
        m = difflib.get_close_matches(cand, keys, n=1, cutoff=0.72)
        if m:
            c = _ALIAS[m[0]]
            return c, display_name(c)
    return None, None
