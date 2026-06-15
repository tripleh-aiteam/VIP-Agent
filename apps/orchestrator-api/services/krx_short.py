"""KRX short-selling (공매도) data fetcher.

Fetches per-stock daily short-selling figures from the KRX Market Data
(MDC) JSON endpoint at data.krx.co.kr.

Design notes
------------
* KRX publishes short-selling with a **T+1 lag** — yesterday's completed
  trading day is the freshest data available. This is expected and legal.
  We therefore start from the most recent *completed* trading day and step
  back over weekends / holidays / not-yet-published days until we get rows.
* Primary dataset: ``MDCSTAT30101`` (전종목 공매도 거래 = all-tickers short
  trade, per trade date). One POST per date returns every ticker, so a whole
  watchlist is satisfied with a single request and includes the short-ratio
  field (``TRDVOL_WT``) directly — no per-ticker ISIN lookup required.
* Fallback dataset: ``MDCSTAT30102`` (개별종목 공매도 거래 개별추이 =
  per-ticker short trade trend over a date range) keyed by the KRX standard
  ISIN code, used only if the all-tickers dataset comes back empty.
* No OTP token is required for these datasets — a plain form POST with the
  correct Referer / User-Agent / X-Requested-With headers is sufficient.
  A wrong ``bld`` returns the literal text ``"LOGOUT"`` (handled).

Only ``httpx`` is used (already a project dependency). On any failure the
functions return ``None`` — they NEVER fabricate data.
"""

from __future__ import annotations

import datetime as _dt
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
_JSON_URL = "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"

# bld dataset codes (verified against pykrx source / recorded KRX responses)
_BLD_ALL_TICKERS = "dbms/MDC/STAT/srt/MDCSTAT30101"   # 전종목 공매도 거래 (per trdDd)
_BLD_BY_TICKER = "dbms/MDC/STAT/srt/MDCSTAT30102"     # 개별종목 공매도 거래 개별추이

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "http://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}

# How many calendar days to walk back looking for a completed trading day.
_MAX_LOOKBACK_DAYS = 10
_TIMEOUT = 12.0
_RETRIES = 3


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _to_int(value: object) -> Optional[int]:
    """Parse a KRX numeric string like ``"1,234,567"`` into an int."""
    if value is None:
        return None
    s = str(value).strip().replace(",", "")
    if s in ("", "-", "N/A"):
        return None
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def _to_float(value: object) -> Optional[float]:
    """Parse a KRX numeric string like ``"0.02"`` into a float (ratio %)."""
    if value is None:
        return None
    s = str(value).strip().replace(",", "")
    if s in ("", "-", "N/A"):
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _isin_from_code(code: str) -> str:
    """Build the KRX standard ISIN code from a 6-digit ticker.

    KRX equity ISINs follow ``KR7`` + 6-digit code + check pattern. The
    last 3 digits are a checksum/issue suffix; common KOSPI equities use
    ``003`` and the loader accepts the family. We use the conventional
    ``KR7<code>003`` form which matches pykrx's behaviour for equities.
    """
    code = str(code).strip().zfill(6)
    return f"KR7{code}003"


def _recent_trading_days(max_days: int = _MAX_LOOKBACK_DAYS) -> list[str]:
    """Yield candidate ``YYYYMMDD`` dates, newest first.

    Starts from *yesterday* (T+1 publishing lag) and walks back, skipping
    Saturdays and Sundays. Public holidays are handled implicitly: a date
    with no published data simply returns empty rows and we move on.
    """
    out: list[str] = []
    day = _dt.date.today() - _dt.timedelta(days=1)
    checked = 0
    while len(out) < max_days and checked < max_days * 2 + 5:
        if day.weekday() < 5:  # Mon-Fri
            out.append(day.strftime("%Y%m%d"))
        day -= _dt.timedelta(days=1)
        checked += 1
    return out


def _post(client: httpx.Client, payload: dict) -> Optional[list[dict]]:
    """POST to the KRX JSON endpoint with retries.

    Returns the ``OutBlock_1`` list, or ``None`` on failure / LOGOUT /
    empty payload.
    """
    last_err: Optional[Exception] = None
    for attempt in range(1, _RETRIES + 1):
        try:
            resp = client.post(_JSON_URL, data=payload, headers=_HEADERS, timeout=_TIMEOUT)
            text = resp.text or ""
            # KRX signals a rejected session/dataset with the literal body
            # "LOGOUT" — and it does so with HTTP 400, so check this BEFORE
            # treating the status code as a transient error worth retrying.
            if "LOGOUT" in text[:64].upper():
                logger.error(
                    "KRX returned LOGOUT (status %s) for bld=%s — session "
                    "rejected or dataset unavailable; not retrying.",
                    resp.status_code, payload.get("bld"),
                )
                return None
            if resp.status_code != 200:
                logger.warning("KRX HTTP %s (attempt %s)", resp.status_code, attempt)
                continue
            data = resp.json()
            rows = data.get("OutBlock_1") or data.get("output") or []
            return rows if isinstance(rows, list) else None
        except (httpx.HTTPError, ValueError) as exc:  # network or JSON decode
            last_err = exc
            logger.warning("KRX request failed (attempt %s/%s): %s", attempt, _RETRIES, exc)
    if last_err:
        logger.error("KRX request gave up after %s attempts: %s", _RETRIES, last_err)
    return None


def _normalise_all_ticker_row(row: dict, trade_date: str) -> dict:
    """Map an MDCSTAT30101 row to the public output shape."""
    return {
        "code": str(row.get("ISU_CD", "")).strip(),
        "name": str(row.get("ISU_ABBRV", "")).strip(),
        "date": trade_date,
        "short_volume": _to_int(row.get("CVSRTSELL_TRDVOL")),
        "short_value": _to_int(row.get("CVSRTSELL_TRDVAL")),
        "short_ratio": _to_float(row.get("TRDVOL_WT")),  # 공매도 거래 비중 (%)
        "total_volume": _to_int(row.get("ACC_TRDVOL")),
        "total_value": _to_int(row.get("ACC_TRDVAL")),
        "source": "KRX MDCSTAT30101",
    }


def _fetch_all_for_date(client: httpx.Client, trade_date: str) -> dict[str, dict]:
    """Fetch every ticker's short-selling for one trade date.

    Returns a ``{code: row}`` mapping (empty if the date has no data).
    """
    payload = {
        "bld": _BLD_ALL_TICKERS,
        "locale": "ko_KR",
        "mktId": "ALL",          # KOSPI + KOSDAQ + KONEX
        "trdDd": trade_date,
        "share": "1",
        "money": "1",
        "csvxls_isNo": "false",
    }
    rows = _post(client, payload)
    if not rows:
        return {}
    out: dict[str, dict] = {}
    for r in rows:
        code = str(r.get("ISU_CD", "")).strip()
        if code:
            out[code] = _normalise_all_ticker_row(r, _date_to_iso(trade_date))
    return out


def _fetch_by_ticker(client: httpx.Client, code: str) -> Optional[dict]:
    """Fallback: per-ticker trend (MDCSTAT30102), newest completed row.

    Used only when the all-tickers dataset yields nothing for a code.
    """
    days = _recent_trading_days()
    strt = days[-1] if days else _dt.date.today().strftime("%Y%m%d")
    end = days[0] if days else strt
    payload = {
        "bld": _BLD_BY_TICKER,
        "locale": "ko_KR",
        "isuCd": _isin_from_code(code),
        "strtDd": strt,
        "endDd": end,
        "share": "1",
        "money": "1",
        "csvxls_isNo": "false",
    }
    rows = _post(client, payload)
    if not rows:
        return None
    # Rows are dated; pick the most recent (largest TRD_DD).
    rows_sorted = sorted(
        rows,
        key=lambda r: str(r.get("TRD_DD", "")).replace("/", ""),
        reverse=True,
    )
    for r in rows_sorted:
        vol = _to_int(r.get("CVSRTSELL_TRDVOL"))
        if vol is None:
            continue
        total_vol = _to_int(r.get("STR_CONST_VAL1") or r.get("ACC_TRDVOL"))
        ratio = None
        if vol is not None and total_vol:
            ratio = round(vol / total_vol * 100, 4)
        return {
            "code": str(code).strip().zfill(6),
            "name": "",
            "date": _date_to_iso(str(r.get("TRD_DD", "")).replace("/", "")),
            "short_volume": vol,
            "short_value": _to_int(r.get("CVSRTSELL_TRDVAL")),
            "short_ratio": ratio,
            "total_volume": total_vol,
            "total_value": _to_int(r.get("STR_CONST_VAL2") or r.get("ACC_TRDVAL")),
            "source": "KRX MDCSTAT30102",
        }
    return None


def _date_to_iso(yyyymmdd: str) -> str:
    s = str(yyyymmdd).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def short_selling_all(codes: list[str]) -> dict[str, dict]:
    """Fetch short-selling data for several tickers in one shot.

    Walks back from the most recent completed trading day until a date with
    published data is found, then returns ``{code: {...}}`` for every
    requested code that exists in that day's all-tickers dataset. Missing
    codes are retried via the per-ticker fallback. Codes that still fail are
    simply absent from the result (never faked).

    Parameters
    ----------
    codes:
        6-digit KRX tickers, e.g. ``["005930", "000660"]``.

    Returns
    -------
    dict[str, dict]
        Mapping of code -> ``{date, short_volume, short_value, short_ratio,
        total_volume, total_value, name, code, source}``. May be empty if
        KRX is unreachable.
    """
    wanted = {str(c).strip().zfill(6) for c in codes}
    result: dict[str, dict] = {}

    try:
        with httpx.Client(follow_redirects=True) as client:
            # Prime cookies (some KRX edges set a session cookie on the loader).
            try:
                client.get(_HEADERS["Referer"], headers=_HEADERS, timeout=_TIMEOUT)
            except httpx.HTTPError:
                pass

            for trade_date in _recent_trading_days():
                day_map = _fetch_all_for_date(client, trade_date)
                if not day_map:
                    continue
                for code in wanted:
                    if code in day_map and code not in result:
                        result[code] = day_map[code]
                # Got everything we asked for -> done.
                if wanted.issubset(result.keys()):
                    break

            # Per-ticker fallback for any still-missing codes.
            for code in wanted - set(result.keys()):
                row = _fetch_by_ticker(client, code)
                if row is not None:
                    result[code] = row
    except httpx.HTTPError as exc:
        logger.error("KRX short_selling_all failed: %s", exc)

    return result


def short_selling(code: str) -> Optional[dict]:
    """Fetch the latest short-selling figures for a single ticker.

    Parameters
    ----------
    code:
        A 6-digit KRX ticker, e.g. ``"005930"`` (삼성전자).

    Returns
    -------
    dict | None
        ``{date, short_volume, short_value, short_ratio, total_volume,
        total_value, name, code, source}`` for the most recent completed
        trading day, or ``None`` if unavailable. Never fabricated.
    """
    code = str(code).strip().zfill(6)
    data = short_selling_all([code])
    return data.get(code)


# --------------------------------------------------------------------------- #
# Self-test (live KRX) — run:  python services/krx_short.py
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    watchlist = {
        "005930": "삼성전자",
        "000660": "SK하이닉스",
        "017670": "SK텔레콤",
        "018260": "삼성SDS",
        "035420": "NAVER",
        "069500": "KODEX200",
    }

    print("=== single: short_selling('005930') ===")
    print(json.dumps(short_selling("005930"), ensure_ascii=False, indent=2))

    print("\n=== single: short_selling('000660') ===")
    print(json.dumps(short_selling("000660"), ensure_ascii=False, indent=2))

    print("\n=== batch: short_selling_all(watchlist) ===")
    allres = short_selling_all(list(watchlist.keys()))
    for c, nm in watchlist.items():
        row = allres.get(c)
        if row:
            print(
                f"{c} {nm:8s} {row['date']}  "
                f"공매도량={row['short_volume']:,}  "
                f"거래대금={row['short_value']:,}원  "
                f"비중={row['short_ratio']}%"
            )
        else:
            print(f"{c} {nm:8s}  (no data)")
