"""
molit_lawd — 법정동 시군구 codes (LAWD_CD) for the MOLIT 실거래가 API.

The 국토교통부 real-transaction-price API is keyed by the 5-digit 시군구
법정동코드 (LAWD_CD). This maps human region names (Korean or English) to those
codes so the assistant can answer "how much is a property in 송파/Songpa".

Coverage: Seoul (all 25 자치구), the 6 metropolitan cities + Sejong, and the
main Gyeonggi 시. Add more 시군구 here as needed — `resolve_lawd()` returns the
list of matching codes (a city like 수원 maps to all its 구 codes).
"""

from __future__ import annotations

# name -> 5-digit LAWD_CD. Names are stored WITHOUT the 구/시/군 suffix where
# the suffix is obvious, plus a few English aliases. resolve_lawd() matches
# loosely (substring), so "송파", "송파구", "Songpa" all resolve to 11710.
LAWD: dict[str, str] = {
    # --- Seoul 25 구 (11xxx) ---
    "종로": "11110", "중구": "11140", "용산": "11170", "성동": "11200",
    "광진": "11215", "동대문": "11230", "중랑": "11260", "성북": "11290",
    "강북": "11305", "도봉": "11320", "노원": "11350", "은평": "11380",
    "서대문": "11410", "마포": "11440", "양천": "11470", "강서": "11500",
    "구로": "11530", "금천": "11545", "영등포": "11560", "동작": "11590",
    "관악": "11620", "서초": "11650", "강남": "11680", "송파": "11710",
    "강동": "11740",
    # --- Busan 부산 (26xxx) ---
    "부산진": "26230", "해운대": "26350", "동래": "26260", "남구부산": "26290",
    "수영": "26500", "사하": "26380", "금정": "26410", "기장": "26710",
    # --- Daegu 대구 (27xxx) ---
    "수성": "27260", "달서": "27290", "달성": "27710",
    # --- Incheon 인천 (28xxx) ---
    "미추홀": "28177", "연수": "28185", "남동": "28200", "부평": "28237",
    "계양": "28245", "강화": "28710",
    # --- Gwangju 광주 (29xxx) ---
    "광산": "29200",
    # --- Daejeon 대전 (30xxx) ---
    "유성": "30200", "대덕": "30230",
    # --- Ulsan 울산 (31xxx) ---
    "울주": "31710",
    # --- Sejong (36110) ---
    "세종": "36110",
    # --- Gyeonggi 경기 main 시 (41xxx) ---
    "수원장안": "41111", "수원권선": "41113", "수원팔달": "41115", "수원영통": "41117",
    "성남수정": "41131", "성남중원": "41133", "분당": "41135", "성남분당": "41135",
    "의정부": "41150", "안양만안": "41171", "안양동안": "41173", "부천": "41190",
    "광명": "41210", "평택": "41220", "동두천": "41250",
    "안산상록": "41271", "안산단원": "41273",
    "고양덕양": "41281", "고양일산동": "41285", "고양일산서": "41287",
    "과천": "41290", "구리": "41310", "남양주": "41360", "오산": "41370",
    "시흥": "41390", "군포": "41410", "의왕": "41430", "하남": "41450",
    "용인처인": "41461", "용인기흥": "41463", "용인수지": "41465",
    "파주": "41480", "이천": "41500", "안성": "41550", "김포": "41570",
    "화성": "41590", "광주경기": "41610", "양주": "41630", "포천": "41650",
    "여주": "41670", "연천": "41800", "가평": "41820", "양평": "41830",
}

# City -> all its sub-구 codes (for ambiguous city-level queries).
CITY_GROUPS: dict[str, list[str]] = {
    "수원": ["41111", "41113", "41115", "41117"],
    "성남": ["41131", "41133", "41135"],
    "안양": ["41171", "41173"],
    "안산": ["41271", "41273"],
    "고양": ["41281", "41285", "41287"],
    "용인": ["41461", "41463", "41465"],
}

# English aliases -> Korean key fragment.
EN_ALIASES: dict[str, str] = {
    "jongno": "종로", "yongsan": "용산", "seongdong": "성동", "gwangjin": "광진",
    "dongdaemun": "동대문", "jungnang": "중랑", "seongbuk": "성북",
    "gangbuk": "강북", "dobong": "도봉", "nowon": "노원", "eunpyeong": "은평",
    "seodaemun": "서대문", "mapo": "마포", "yangcheon": "양천", "gangseo": "강서",
    "guro": "구로", "geumcheon": "금천", "yeongdeungpo": "영등포", "dongjak": "동작",
    "gwanak": "관악", "seocho": "서초", "gangnam": "강남", "songpa": "송파",
    "gangdong": "강동", "bundang": "분당", "suwon": "수원", "seongnam": "성남",
    "goyang": "고양", "yongin": "용인", "sejong": "세종",
}


def resolve_lawd(name: str) -> list[str]:
    """Return matching 5-digit LAWD codes for a region name (KO/EN). May return
    several codes for a city made of multiple 구 (e.g. 수원, 고양)."""
    if not name:
        return []
    raw = name.strip()
    low = raw.lower()

    # English alias → korean fragment
    for en, ko in EN_ALIASES.items():
        if en in low:
            raw = ko
            break

    # City group (수원/고양/…) → all sub-codes, unless a specific 구 is named.
    for city, codes in CITY_GROUPS.items():
        if city in raw:
            # a specific district within the city?
            for key, code in LAWD.items():
                if key.startswith(city) and key != city and key[len(city):] and \
                        key[len(city):] in raw:
                    return [code]
            return list(codes)

    # Strip trailing 구/시/군 for loose matching.
    probe = raw
    for suf in ("특별시", "광역시", "특별자치시", "구", "시", "군"):
        probe = probe.replace(suf, "")
    probe = probe.strip()

    if probe in LAWD:
        return [LAWD[probe]]
    # substring match (longest key first to prefer specific districts)
    hits = [code for key, code in sorted(LAWD.items(), key=lambda kv: -len(kv[0]))
            if probe and (probe in key or key in probe)]
    # de-dupe preserving order
    seen: set[str] = set()
    out = [c for c in hits if not (c in seen or seen.add(c))]
    return out[:4]
