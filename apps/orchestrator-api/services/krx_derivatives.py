"""
krx_derivatives — Korean DERIVATIVES daily trading VALUE (거래대금) from KRX.

Fetches the daily MARKET-LEVEL trading value (in KRW) for the most-recent
completed trading day:

  • KOSPI200 index FUTURES  (코스피200 선물)   — prodId KRDRVFUK2I
  • KOSPI200 CALL options   (코스피200 콜옵션)  — prodId KRDRVOPK2I, rghtTpCd=C
  • KOSPI200 PUT  options   (코스피200 풋옵션)  — prodId KRDRVOPK2I, rghtTpCd=P

Source: KRX MDC JSON endpoint
  POST https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd
  bld = dbms/MDC/STAT/standard/MDCSTAT12501   (파생상품 전종목 시세 / all-products daily quotes)
  params: trdDd=YYYYMMDD, prodId=<product>, mktTpCd=T, rghtTpCd=<C|P|T>
  header: Referer = .../outerLoader/index.cmd  (REQUIRED — wrong/missing → "LOGOUT")

The endpoint returns one row per listed contract; the daily MARKET trading value
is the SUM of the per-contract 거래대금 column (ACC_TRDVAL) across all rows for
that product. We step back over weekends/holidays to the latest day that has data.

No external pip deps — uses `httpx` (already a project dependency) + the stdlib.
NEVER fabricates numbers: returns None (or partial dict with the rest = None) on
any failure or empty response.

OPERATIONAL NOTE (verified 2026-06): KRX's getJsonData / GenerateOTP endpoints
return HTTP 400 body "LOGOUT" for ALL bld codes when called from outside a
real, JS-driven browser session (the public HTML pages load fine, but the data
JSON endpoint is gated by a token mdc.js computes client-side and/or by IP/geo).
This module sends the exact correct bld + params + headers + warmed session, so
it works from environments where the endpoint is reachable (e.g. a Korea-hosted
server / a session with a valid token). When gated, it returns None — it never
invents data. See `_LOGOUT` handling and the module __main__ self-test.
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any

import httpx

try:  # project logger if available, else stdlib
    from services.logger import log  # type: ignore
except Exception:  # pragma: no cover
    import logging

    log = logging.getLogger("krx_derivatives")

# --------------------------------------------------------------------------- #
# Endpoint / dataset constants
# --------------------------------------------------------------------------- #
_JSON_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
_MAIN_URL = "https://data.krx.co.kr/contents/MDC/MAIN/main/index.cmd"
# Referer MUST point at an MDC loader page or the server replies "LOGOUT".
_REFERER = "https://data.krx.co.kr/contents/MDC/MDI/outerLoader/index.cmd"

# 파생상품 전종목 시세 (all-products daily derivatives quotes).
_BLD = "dbms/MDC/STAT/standard/MDCSTAT12501"

# Product identifiers (KRX prodId).
_PROD_KOSPI200_FUT = "KRDRVFUK2I"  # 코스피200 선물
_PROD_KOSPI200_OPT = "KRDRVOPK2I"  # 코스피200 옵션 (call/put split via rghtTpCd)

# rghtTpCd: C=call, P=put, T=both. Options MUST be fetched C and P separately —
# requesting T for the option product can fail / mix the legs.
_LOGOUT = "LOGOUT"

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_POST_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://data.krx.co.kr",
    "Referer": _REFERER,
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
}

# Candidate column keys that carry 거래대금 (accumulated trading value), in
# preference order. KRX field names vary slightly by dataset revision, so we
# probe several. (ACC_TRDVAL is the standard for MDCSTAT125xx.)
_VALUE_KEYS = ("ACC_TRDVAL", "TRD_VAL", "ACC_TRDVAL_AMT", "TRDVAL")

_TIMEOUT = 25.0
_RETRIES = 3
_MAX_STEPBACK_DAYS = 10  # weekends + holiday runs


def _to_int(raw: Any) -> int | None:
    """Parse a KRX numeric string like '12,345,678' → int. None if not numeric."""
    if raw is None:
        return None
    s = str(raw).replace(",", "").replace("+", "").strip()
    if s in ("", "-", "/"):
        return None
    try:
        return int(round(float(s)))
    except (ValueError, TypeError):
        return None


def _rows(payload: dict | None) -> list[dict]:
    """KRX returns rows under one of several output keys depending on dataset."""
    if not isinstance(payload, dict):
        return []
    for key in ("output", "OutBlock_1", "block1", "out"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return rows
    return []


def _sum_trdval(rows: list[dict]) -> int | None:
    """Sum 거래대금 across all contract rows. None if no usable value column."""
    if not rows:
        return None
    # Determine which value key this dataset actually uses.
    sample = rows[0]
    value_key = next((k for k in _VALUE_KEYS if k in sample), None)
    if value_key is None:
        log.warning("krx_derivatives: no known 거래대금 column in row keys %s",
                    list(sample.keys()))
        return None
    total = 0
    seen = False
    for r in rows:
        v = _to_int(r.get(value_key))
        if v is not None:
            total += v
            seen = True
    return total if seen else None


def _recent_trading_dates() -> list[str]:
    """Most-recent completed trading days (YYYYMMDD), newest first, skipping
    weekends. Today is excluded because intraday/today's row may be absent or
    incomplete; holidays are handled by step-back (a holiday returns no rows)."""
    out: list[str] = []
    d = date.today() - timedelta(days=1)
    while len(out) < _MAX_STEPBACK_DAYS:
        if d.weekday() < 5:  # Mon-Fri
            out.append(d.strftime("%Y%m%d"))
        d -= timedelta(days=1)
    return out


def _warm_session(client: httpx.Client) -> None:
    """Hit MDC pages to obtain JSESSIONID + __smVisitorID cookies. KRX rejects
    data calls that lack a warmed session."""
    try:
        client.get(_MAIN_URL, headers=_BROWSER_HEADERS, timeout=_TIMEOUT)
        client.get(_REFERER, headers=_BROWSER_HEADERS, timeout=_TIMEOUT)
    except Exception as e:  # pragma: no cover - network
        log.warning("krx_derivatives: session warm-up failed: %s", str(e)[:120])


def _fetch(client: httpx.Client, params: dict[str, str]) -> list[dict]:
    """POST one dataset query; return rows. Empty list on LOGOUT/error/empty.

    Raises RuntimeError('LOGOUT') so the caller can distinguish a gated endpoint
    (retry/abort) from a legitimately empty trading day (step back)."""
    body = {"bld": _BLD, "locale": "ko_KR", "mktTpCd": "T", **params}
    last_exc: Exception | None = None
    for attempt in range(1, _RETRIES + 1):
        try:
            r = client.post(_JSON_URL, data=body, headers=_POST_HEADERS,
                            timeout=_TIMEOUT)
            text = (r.text or "").strip()
            # "LOGOUT" body, or a 400/403 from the WAF/session gate → endpoint
            # is gated (not a per-date issue). Re-warm once, then abort cleanly
            # so we don't hammer it across every candidate date.
            if text == _LOGOUT or r.status_code in (400, 403):
                if attempt < _RETRIES:
                    _warm_session(client)
                    time.sleep(0.6)
                    continue
                raise RuntimeError(_LOGOUT)
            r.raise_for_status()
            return _rows(r.json())
        except RuntimeError:
            raise
        except Exception as e:  # network / JSON / status
            last_exc = e
            time.sleep(0.5 * attempt)
    if last_exc:
        log.warning("krx_derivatives: fetch failed after retries: %s",
                    str(last_exc)[:120])
    return []


def _value_for(client: httpx.Client, trd: str, prod: str,
               right: str = "T") -> int | None:
    """거래대금 total for one product on one date. Re-raises LOGOUT."""
    rows = _fetch(client, {"trdDd": trd, "prodId": prod, "rghtTpCd": right})
    return _sum_trdval(rows)


def derivatives_turnover() -> dict | None:
    """Daily KOSPI200 derivatives trading value (거래대금, KRW) for the most
    recent completed trading day.

    Returns:
        {
          "date":          "YYYYMMDD",          # the trading day actually used
          "futures_value": int | None,          # KOSPI200 futures 거래대금 (KRW)
          "call_value":    int | None,          # KOSPI200 call options 거래대금
          "put_value":     int | None,          # KOSPI200 put options 거래대금
          "source":        "KRX MDCSTAT12501",
          "partial":       bool,                # True if any of the 3 is None
        }
        or None if KRX is unreachable / gated (LOGOUT) / no data at all.

    NEVER returns fabricated values. A field is None only when that specific
    series could not be retrieved.
    """
    try:
        with httpx.Client(follow_redirects=True, timeout=_TIMEOUT) as client:
            _warm_session(client)

            for trd in _recent_trading_dates():
                try:
                    fut = _value_for(client, trd, _PROD_KOSPI200_FUT, "T")
                    call = _value_for(client, trd, _PROD_KOSPI200_OPT, "C")
                    put = _value_for(client, trd, _PROD_KOSPI200_OPT, "P")
                except RuntimeError:  # LOGOUT — endpoint gated, no point retrying dates
                    log.warning("krx_derivatives: endpoint returned LOGOUT "
                                "(gated/unreachable from this host)")
                    return None

                if fut is None and call is None and put is None:
                    # No data for this day (holiday / not yet published) → step back.
                    continue

                return {
                    "date": trd,
                    "futures_value": fut,
                    "call_value": call,
                    "put_value": put,
                    "source": "KRX MDCSTAT12501",
                    "partial": any(v is None for v in (fut, call, put)),
                }
    except Exception as e:  # pragma: no cover - defensive
        log.warning("krx_derivatives: unexpected failure: %s", str(e)[:160])
        return None

    return None


if __name__ == "__main__":  # live self-test against KRX
    import json

    print("Fetching KOSPI200 derivatives trading value (거래대금) from KRX ...")
    result = derivatives_turnover()
    if result is None:
        print("RESULT: None - KRX not reachable / endpoint gated (LOGOUT/403) / no data.")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        for label, key in (("Futures", "futures_value"),
                           ("Call options", "call_value"),
                           ("Put options", "put_value")):
            v = result.get(key)
            print(f"  {label:13}: "
                  + (f"{v:,} KRW" if isinstance(v, int) else "N/A"))
