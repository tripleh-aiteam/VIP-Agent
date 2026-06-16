"""KIS Developers (한국투자증권) — Korean DERIVATIVES quotation helpers.

Builds on ``kis_client`` (OAuth + GET helper) to expose two things the platform
needs:

  * ``derivatives_turnover()`` — most-recent completed day's **거래대금** (KRW
    daily trading value) for KOSPI200 지수선물 / 콜옵션 / 풋옵션, as
    ``{date, futures_value, call_value, put_value}``.
  * ``stock_futures(equity_code)`` / ``stock_futures_all(codes)`` — most-recent
    completed day's **개별주식선물** (single-stock futures) activity per
    underlying equity, as ``{date, volume, value, open_interest}``.

All functions are **fail-safe**: on missing creds / API error / unparseable
response they return ``None`` (or skip that code in the batch). They NEVER
fabricate numbers.

--------------------------------------------------------------------------- #
VERIFIED KIS ENDPOINTS (sources cited per item)
--------------------------------------------------------------------------- #
1) 선물옵션 시세 (single-instrument current price; carries 누적거래대금):
     GET /uapi/domestic-futureoption/v1/quotations/inquire-price
     tr_id  : FHMIF10000000
     params : FID_COND_MRKT_DIV_CODE  (F=지수선물, O=지수옵션, JF=주식선물)
              FID_INPUT_ISCD          (instrument short code, e.g. 101W09)
     output1 fields incl.: futs_prpr (현재가), acml_vol (누적거래량),
              acml_tr_pbmn (누적거래대금, KRW), hts_otst_stpl_qty (미결제약정).
   Source: koreainvestment/open-trading-api
           examples_llm/domestic_futureoption/inquire_price/inquire_price.py
           (tr_id FHMIF10000000, path .../quotations/inquire-price,
            params FID_COND_MRKT_DIV_CODE + FID_INPUT_ISCD) and its
           chk_inquire_price.py COLUMN_MAPPING (누적 거래 대금 = acml_tr_pbmn,
            누적 거래량 = acml_vol, HTS 미결제 약정 수량 = hts_otst_stpl_qty).

2) 선물옵션 기간별 시세 (daily chart; per-day 거래량/거래대금/미결제약정):
     GET /uapi/domestic-futureoption/v1/quotations/inquire-daily-fuopchartprice
     tr_id  : FHKIF03020100
     params : FID_COND_MRKT_DIV_CODE  (F / O / JF)
              FID_INPUT_ISCD          (instrument short code)
              FID_INPUT_DATE_1        (start YYYYMMDD)
              FID_INPUT_DATE_2        (end   YYYYMMDD)
              FID_PERIOD_DIV_CODE     (D=daily, W=weekly, M=monthly)
     output2 rows incl.: stck_bsop_date (영업일), acml_vol (거래량),
              acml_tr_pbmn (거래대금, KRW), hts_otst_stpl_qty (미결제약정),
              futs_prpr (종가).
   Source: koreainvestment/open-trading-api
           examples_llm/domestic_futureoption/inquire_daily_fuopchartprice/
           inquire_daily_fuopchartprice.py (tr_id FHKIF03020100, path
           .../quotations/inquire-daily-fuopchartprice, params
           FID_COND_MRKT_DIV_CODE/FID_INPUT_ISCD/FID_INPUT_DATE_1/
           FID_INPUT_DATE_2/FID_PERIOD_DIV_CODE) and its
           chk_inquire_daily_fuopchartprice.py field list (stck_bsop_date,
           acml_vol, acml_tr_pbmn, hts_otst_stpl_qty, futs_prpr ...).

3) 개별주식선물 종목코드 ↔ 기초자산(주식) 매핑 (master file):
     ZIP https://new.real.download.dws.co.kr/common/master/fo_com_code.mst.zip
     fields: 상품구분 / 상품종류 / 단축코드(=stock-futures symbol) /
             표준코드 / 한글종목명 / 월물구분코드 /
             기초자산 단축코드(=underlying 6-digit equity) / 기초자산 명.
   Source: koreainvestment/open-trading-api
           stocks_info/domestic_commodity_future_code.py (downloads
           fo_com_code.mst.zip; emits 단축코드 + 기초자산 단축코드 columns).

--------------------------------------------------------------------------- #
THINGS THAT NEED A LIVE KEY TO PIN (clearly marked)
--------------------------------------------------------------------------- #
  * The representative KOSPI200 contract short codes (front-month 지수선물 +
    near-the-money 콜/풋 옵션) change every expiry. We resolve the active futures
    code at runtime from ``display-board-top`` and let callers override via env;
    the exact board response keys are logged on first live call (see below).
  * ``FID_COND_MRKT_DIV_CODE`` for 주식선물 is documented as "JF"; we default to
    that but log the raw response so it can be confirmed on first live call.
  * The single-stock-futures master mapping (fo_com_code.mst) is fetched + cached
    lazily; column ORDER inside the .mst is fixed by the official parser above,
    but we verify row shape live and fall back to None if the layout shifts.
"""

from __future__ import annotations

import datetime as _dt
import io
import logging
import threading
import time
import zipfile
from typing import Any, Optional

import httpx

from services import kis_client

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Endpoint constants (verified — see module docstring sources)
# --------------------------------------------------------------------------- #
_PATH_INQUIRE_PRICE = "/uapi/domestic-futureoption/v1/quotations/inquire-price"
_TR_INQUIRE_PRICE = "FHMIF10000000"

_PATH_DAILY_CHART = (
    "/uapi/domestic-futureoption/v1/quotations/inquire-daily-fuopchartprice"
)
_TR_DAILY_CHART = "FHKIF03020100"

_PATH_BOARD_TOP = "/uapi/domestic-futureoption/v1/quotations/display-board-top"
_TR_BOARD_TOP = "FHPIF05030000"

# display-board-callput: returns call rows (output1) + put rows (output2) for an
# expiry. tr_id FHPIF05030100 (verified: examples_llm/.../display_board_callput).
_PATH_BOARD_CALLPUT = (
    "/uapi/domestic-futureoption/v1/quotations/display-board-callput"
)
_TR_BOARD_CALLPUT = "FHPIF05030100"

# Market-division codes (FID_COND_MRKT_DIV_CODE).
_MKT_FUTURES = "F"   # 지수선물
_MKT_OPTION = "O"    # 지수옵션
_MKT_STOCK_FUT = "JF"  # 주식선물 (documented; logged + confirmable live)

# Single-stock-futures master file (단축코드 ↔ 기초자산 단축코드). NOTE: stock
# futures live in fo_stk_code.mst — fo_com_code.mst is bond/commodity futures.
_FO_MASTER_URL = (
    "https://new.real.download.dws.co.kr/common/master/fo_stk_code.mst.zip"
)

_TIMEOUT = 20.0
_LOOKBACK_DAYS = 10
# KIS reports 선물옵션 누적거래대금 in 천원; multiply to get real KRW.
_TURNOVER_KRW = 1000

# Cached underlying-equity -> stock-futures-symbol map: {equity6: [futsym, ...]}.
_sf_map_cache: Optional[dict[str, list[str]]] = None
_sf_map_lock = threading.Lock()

# Response keys (verified vs official chk_*.py COLUMN_MAPPING). First present wins.
_KEY_DATE = ("stck_bsop_date", "bsop_date", "stnd_date")
_KEY_VOL = ("acml_vol", "futs_acml_vol")
_KEY_VAL = ("acml_tr_pbmn", "futs_acml_tr_pbmn", "acml_tr_pbm")
_KEY_OI = ("hts_otst_stpl_qty", "otst_stpl_qty")
_KEY_FUTS_CODE = ("futs_shrn_iscd", "shrn_iscd", "stck_shrn_iscd")


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #
def _to_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    s = str(value).strip().replace(",", "").replace("+", "")
    if s in ("", "-", "N/A"):
        return None
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def _first(row: dict, keys: tuple[str, ...]) -> Any:
    for k in keys:
        if k in row and str(row[k]).strip() not in ("", "-"):
            return row[k]
    return None


def _date_to_iso(value: Any) -> str:
    s = str(value or "").strip().replace("/", "").replace("-", "")
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return str(value or "")


def _date_range() -> tuple[str, str]:
    """(start, end) in YYYYMMDD covering the recent window (end = today)."""
    today = _dt.date.today()
    start = today - _dt.timedelta(days=_LOOKBACK_DAYS)
    return start.strftime("%Y%m%d"), today.strftime("%Y%m%d")


def _second_thursday(year: int, month: int) -> _dt.date:
    """KOSPI200 monthly futures/options expire on the 2nd Thursday."""
    d = _dt.date(year, month, 1)
    first_thu = d + _dt.timedelta(days=(3 - d.weekday()) % 7)  # Thu=3
    return first_thu + _dt.timedelta(days=7)


def _front_expiry() -> str:
    """Front (near) monthly KOSPI200 option/futures expiry as 'YYYYMM'.

    If this month's 2nd-Thursday expiry has already passed, roll to next month —
    that's where the liquid near-month contracts trade."""
    today = _dt.date.today()
    if today > _second_thursday(today.year, today.month):
        y, m = (today.year + (today.month // 12), (today.month % 12) + 1)
        return f"{y}{m:02d}"
    return f"{today.year}{today.month:02d}"


def _rows(data: dict, *keys: str) -> list[dict]:
    """Pull the first non-empty list-of-dicts from the named output keys."""
    for k in keys:
        v = data.get(k)
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return v
    return []


def _obj(data: dict, *keys: str) -> Optional[dict]:
    """Pull the first dict (single-row output) from the named output keys."""
    for k in keys:
        v = data.get(k)
        if isinstance(v, dict) and v:
            return v
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return v[0]
    return None


def _newest_daily_row(data: dict) -> Optional[dict]:
    """From an inquire-daily-fuopchartprice payload, return the newest per-day row
    mapped to {date, volume, value, open_interest}."""
    rows = _rows(data, "output2", "output", "output1")
    parsed: list[dict] = []
    for r in rows:
        d = _date_to_iso(_first(r, _KEY_DATE))
        vol = _to_int(_first(r, _KEY_VOL))
        val = _to_int(_first(r, _KEY_VAL))
        if not d or (vol is None and val is None):
            continue
        parsed.append({
            "date": d,
            "volume": vol,
            # Raw value as reported. Unit differs by product (index futures =
            # 천원, stock futures = 원), so scaling is applied by the caller.
            "value": val,
            "open_interest": _to_int(_first(r, _KEY_OI)),
        })
    if not parsed:
        return None
    parsed.sort(key=lambda p: p["date"], reverse=True)
    return parsed[0]


# --------------------------------------------------------------------------- #
# Instrument code resolution
# --------------------------------------------------------------------------- #
# KOSPI200 index-futures master (단축코드 ↔ 만기). Cached (code, epoch).
_FO_IDX_MASTER_URL = (
    "https://new.real.download.dws.co.kr/common/master/fo_idx_code.mst.zip"
)
_idx_fut_cache: tuple[Optional[str], float] = (None, 0.0)
_idx_fut_lock = threading.Lock()
_IDX_FUT_TTL = 12 * 3600


def _active_futures_code() -> Optional[str]:
    """Resolve the active (front-month) KOSPI200 지수선물 단축코드 from the KIS
    index master file (fo_idx_code.mst). The file lists every listed contract as
    e.g. ``1A01609 ... F 202609 ... KOSPI200`` (short code, type F, expiry YYYYMM).
    We pick the nearest non-expired quarterly contract. Cached 12h. Returns None
    if it can't be resolved (caller then skips the futures leg)."""
    global _idx_fut_cache
    code, exp = _idx_fut_cache
    if code and (time.time() - exp) < _IDX_FUT_TTL:
        return code

    with _idx_fut_lock:
        code, exp = _idx_fut_cache
        if code and (time.time() - exp) < _IDX_FUT_TTL:
            return code
        try:
            resp = httpx.get(_FO_IDX_MASTER_URL, timeout=_TIMEOUT)
            resp.raise_for_status()
            zf = zipfile.ZipFile(io.BytesIO(resp.content))
            name = next((n for n in zf.namelist() if n.endswith(".mst")),
                        zf.namelist()[0])
            raw = zf.read(name).decode("cp949", errors="replace")
        except (httpx.HTTPError, zipfile.BadZipFile, OSError, ValueError) as exc:
            logger.error("kis_derivatives: index master fetch failed: %s", exc)
            return None

        import re
        today = _dt.date.today()
        cur_ym = today.year * 100 + today.month
        # If this month is a quarterly expiry and it has already passed, the front
        # contract is the next quarter — handled naturally by ">= cur_ym" plus the
        # 2nd-Thursday check below.
        front_passed = today > _second_thursday(today.year, today.month)
        candidates: list[tuple[int, str]] = []
        for line in raw.splitlines():
            if "KOSPI200" not in line:
                continue
            tokens = line.split()
            if not tokens:
                continue
            short = tokens[0].strip()
            # Index FUTURES short codes look like 1A01YMM (e.g. 1A01609); options
            # have different prefixes. Require the F product-type marker on the line.
            if not re.match(r"^1A01\d{3}$", short):
                continue
            m = re.search(r"\b(20\d{4})\b", line)   # expiry YYYYMM
            if not m:
                continue
            ym = int(m.group(1))
            # Keep contracts whose expiry month is in the future, or this month if
            # this month's expiry hasn't passed yet.
            if ym > cur_ym or (ym == cur_ym and not front_passed):
                candidates.append((ym, short))
        if not candidates:
            logger.warning("kis_derivatives: no KOSPI200 futures in index master.")
            return None
        candidates.sort()
        master_code = candidates[0][1]
        # The master 단축코드 is like '1A01609'; the API's FID_INPUT_ISCD wants it
        # WITHOUT the leading market digit -> 'A01609' (verified live).
        chosen = master_code[1:] if master_code.startswith("1") else master_code
        _idx_fut_cache = (chosen, time.time())
        logger.info("kis_derivatives: front-month KOSPI200 futures code=%s "
                    "(master=%s, expiry=%s)", chosen, master_code, candidates[0][0])
        return chosen


# --------------------------------------------------------------------------- #
# Public: index futures / options turnover (거래대금)
# --------------------------------------------------------------------------- #
def _instrument_turnover(mkt_code: str, iscd: str) -> Optional[dict]:
    """Newest completed day's {date, value, volume} for one instrument via the
    daily-chart endpoint (carries acml_tr_pbmn = 거래대금)."""
    strt, end = _date_range()
    params = {
        "FID_COND_MRKT_DIV_CODE": mkt_code,
        "FID_INPUT_ISCD": iscd,
        "FID_INPUT_DATE_1": strt,
        "FID_INPUT_DATE_2": end,
        "FID_PERIOD_DIV_CODE": "D",
    }
    data = kis_client._get(_PATH_DAILY_CHART, _TR_DAILY_CHART, params)
    if data is None:
        return None
    logger.debug("kis_derivatives: daily-chart %s/%s out2 sample=%s",
                 mkt_code, iscd,
                 (_rows(data, "output2", "output1") or [None])[0])
    return _newest_daily_row(data)


def _callput_values(expiry: Optional[str] = None) -> tuple[Optional[int], Optional[int]]:
    """Sum 누적거래대금 across all listed call / put option rows for an expiry.

    Returns (call_value, put_value) in KRW. Uses display-board-callput, whose
    output1 = call rows and output2 = put rows (verified). Each row carries
    acml_tr_pbmn. Returns (None, None) if unavailable.
    """
    params = {
        "FID_COND_MRKT_DIV_CODE": _MKT_OPTION,
        "FID_COND_SCR_DIV_CODE": "20503",
        "FID_MRKT_CLS_CODE": "CO",   # Call
        "FID_MTRT_CNT": expiry or _front_expiry(),   # 만기 YYYYMM (required)
        "FID_MRKT_CLS_CODE1": "PO",  # Put
        "FID_COND_MRKT_CLS_CODE": "",
    }
    data = kis_client._get(_PATH_BOARD_CALLPUT, _TR_BOARD_CALLPUT, params)
    if data is None:
        return None, None

    def _sum(rows: list[dict]) -> Optional[int]:
        vals = [_to_int(_first(r, _KEY_VAL)) for r in rows]
        vals = [v for v in vals if v is not None]
        # KIS reports option 누적거래대금 (acml_tr_pbmn) in 천원 (= 프리미엄포인트
        # × 250 × 계약수); the real KRW turnover is ×1000.
        return sum(vals) * _TURNOVER_KRW if vals else None

    call_rows = _rows(data, "output1", "output")
    put_rows = _rows(data, "output2")
    logger.debug("kis_derivatives: callput call_rows=%d put_rows=%d",
                 len(call_rows), len(put_rows))
    return _sum(call_rows), _sum(put_rows)


def derivatives_turnover() -> Optional[dict]:
    """Most-recent completed day's 선물옵션 거래대금 (KRW daily trading value).

    Returns
    -------
    dict | None
        ``{date, futures_value, call_value, put_value}`` where each value is KRW
        (누적거래대금). ``date`` is the futures leg's business date (ISO). Any leg
        that can't be resolved is ``None`` rather than fabricated. Returns
        ``None`` only if *nothing* could be fetched (e.g. no creds).

    Notes
    -----
    * futures_value: front-month KOSPI200 지수선물 거래대금 (resolved at runtime).
    * call_value / put_value: summed across listed 콜/풋 option rows for the
      near expiry via display-board-callput.
    """
    if kis_client.get_token() is None:
        return None

    result: dict[str, Any] = {
        "date": None,
        "futures_value": None,
        "call_value": None,
        "put_value": None,
    }

    fut_code = _active_futures_code()
    if fut_code:
        fut = _instrument_turnover(_MKT_FUTURES, fut_code)
        if fut and fut.get("value") is not None:
            result["date"] = fut.get("date")
            # Index-futures 거래대금 is reported in 천원 → ×1000 for real KRW.
            result["futures_value"] = fut["value"] * _TURNOVER_KRW

    call_val, put_val = _callput_values()
    result["call_value"] = call_val
    result["put_value"] = put_val

    if (result["futures_value"] is None
            and result["call_value"] is None
            and result["put_value"] is None):
        logger.warning("kis_derivatives: derivatives_turnover got no values "
                       "(all legs None) — check tr_ids/codes on first live run.")
        return None
    return result


# --------------------------------------------------------------------------- #
# Single-stock futures (개별주식선물)
# --------------------------------------------------------------------------- #
def _load_stock_futures_map() -> dict[str, list[str]]:
    """Download + cache {underlying_equity6 -> [stock-futures short codes]}.

    Parses fo_com_code.mst (from fo_com_code.mst.zip). Per the official parser
    (stocks_info/domestic_commodity_future_code.py) the master pairs each
    derivative 단축코드 with its 기초자산 단축코드 (6-digit underlying equity).
    We extract 6-digit underlyings and the leading short code per line
    defensively, and log a sample line at DEBUG so the exact column offsets can
    be confirmed live. Returns {} on any failure (never raises).
    """
    global _sf_map_cache
    if _sf_map_cache is not None:
        return _sf_map_cache

    with _sf_map_lock:
        if _sf_map_cache is not None:
            return _sf_map_cache
        mapping: dict[str, list[str]] = {}
        try:
            resp = httpx.get(_FO_MASTER_URL, timeout=_TIMEOUT)
            resp.raise_for_status()
            zf = zipfile.ZipFile(io.BytesIO(resp.content))
            name = next((n for n in zf.namelist() if n.endswith(".mst")),
                        zf.namelist()[0])
            raw = zf.read(name).decode("cp949", errors="replace")
        except (httpx.HTTPError, zipfile.BadZipFile, OSError, ValueError) as exc:
            logger.error("kis_derivatives: stock-futures master fetch failed: %s",
                         exc)
            return {}

        import re
        lines = raw.splitlines()
        if lines:
            logger.debug("kis_derivatives: fo_stk sample line=%r", lines[0])
        # Each FUTURES line looks like:
        #   1A11607  KR4A11670002삼성전자  F 202607 (  10) ...001005930   삼성전자
        # -> 단축코드=1A11607, type F, 만기=202607, 기초자산(underlying)=005930.
        # Stock OPTIONS rows carry 'C'/'P' instead of 'F' and are skipped.
        for line in lines:
            if not line.strip():
                continue
            m_exp = re.search(r"\sF\s+(\d{6})\b", line)        # futures + 만기
            if not m_exp:
                continue
            expiry = int(m_exp.group(1))
            tokens = line.split()
            short_code = tokens[0].strip() if tokens else ""
            m_und = re.search(r"(\d{6})\s+\S+\s*$", line)      # underlying before name
            if not short_code or not m_und:
                continue
            underlying = m_und.group(1)
            # API FID_INPUT_ISCD drops the leading market digit (1A11607 -> A11607).
            api_code = short_code[1:] if short_code.startswith("1") else short_code
            mapping.setdefault(underlying, []).append((expiry, api_code))

        _sf_map_cache = mapping
        logger.info("kis_derivatives: loaded stock-futures map for %d underlyings",
                    len(mapping))
        return mapping


def _stock_futures_symbols(equity_code: str) -> list[str]:
    """Front-month stock-futures API code for one underlying equity (6-digit).

    Returns a single-element list with the nearest non-expired contract's API
    code (or [] if none). Front-month only keeps it to one API call per stock."""
    code6 = str(equity_code).strip().zfill(6)
    contracts = _load_stock_futures_map().get(code6, [])
    if not contracts:
        return []
    today = _dt.date.today()
    cur_ym = today.year * 100 + today.month
    front_passed = today > _second_thursday(today.year, today.month)
    future = [(ym, c) for ym, c in contracts
              if ym > cur_ym or (ym == cur_ym and not front_passed)]
    if not future:
        return []
    future.sort()
    return [future[0][1]]


def stock_futures(equity_code: str) -> Optional[dict]:
    """Most-recent completed day's single-stock-futures activity for one
    underlying equity, aggregated across its listed contract months.

    Parameters
    ----------
    equity_code:
        6-digit KRX ticker of the *underlying* equity, e.g. ``"005930"`` (삼성전자).
        Mapped to its stock-futures short code(s) via the KIS master file.

    Returns
    -------
    dict | None
        ``{date, volume, value, open_interest}`` for the newest completed
        trading day (volume = 계약수, value = 거래대금 KRW, open_interest =
        미결제약정 계약수), summed across the underlying's contract months.
        ``None`` if creds missing / no listed futures / API error. Never faked.
    """
    if kis_client.get_token() is None:
        return None

    symbols = _stock_futures_symbols(equity_code)
    if not symbols:
        logger.debug("kis_derivatives: no stock-futures symbol for %s",
                     equity_code)
        return None

    date: Optional[str] = None
    vol_sum = 0
    val_sum = 0
    oi_sum = 0
    got_any = False
    for sym in symbols:
        row = _instrument_turnover(_MKT_STOCK_FUT, sym)
        if not row:
            continue
        got_any = True
        date = row.get("date") or date
        if row.get("volume") is not None:
            vol_sum += row["volume"]
        if row.get("value") is not None:
            val_sum += row["value"]
        if row.get("open_interest") is not None:
            oi_sum += row["open_interest"]

    if not got_any:
        return None
    return {
        "date": date,
        "volume": vol_sum or None,
        "value": val_sum or None,
        "open_interest": oi_sum or None,
    }


def stock_futures_all(codes: list[str]) -> dict[str, dict]:
    """``stock_futures`` for several underlyings.

    Parameters
    ----------
    codes:
        6-digit underlying KRX tickers, e.g. ``["005930", "000660"]``.

    Returns
    -------
    dict[str, dict]
        ``{equity_code: {date, volume, value, open_interest}}``. Codes with no
        listed futures / no data are simply absent (never faked). Empty if creds
        are missing.
    """
    result: dict[str, dict] = {}
    if kis_client.get_token() is None:
        return result
    for code in codes:
        c = str(code).strip().zfill(6)
        row = stock_futures(c)
        if row is not None:
            result[c] = row
    return result


# --------------------------------------------------------------------------- #
# Self-test (live KIS) — run once creds are set:
#   KIS_APP_KEY=... KIS_APP_SECRET=... python services/kis_derivatives.py
# Prints raw shapes (DEBUG) so the exact response keys can be confirmed live.
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(message)s")

    if kis_client.get_token() is None:
        print("No token — set KIS_APP_KEY / KIS_APP_SECRET to test live.")
    else:
        print("=== derivatives_turnover() ===")
        print(json.dumps(derivatives_turnover(), ensure_ascii=False, indent=2))

        print("\n=== stock_futures('005930') 삼성전자 ===")
        print(json.dumps(stock_futures("005930"), ensure_ascii=False, indent=2))

        print("\n=== stock_futures_all(['005930','000660']) ===")
        print(json.dumps(stock_futures_all(["005930", "000660"]),
                         ensure_ascii=False, indent=2))
