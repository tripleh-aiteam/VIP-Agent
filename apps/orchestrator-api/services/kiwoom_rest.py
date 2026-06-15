"""Kiwoom REST OpenAPI client — short-selling (공매도) per stock.

This module talks to Kiwoom Securities' **REST** OpenAPI (``api.kiwoom.com``),
which is a *different* product from the legacy Open API+ (OCX/COM) desktop
control. The REST API currently covers **domestic stocks only** (KOSPI /
KOSDAQ / KONEX, incl. ETF/ETN). See ``derivatives_*`` stubs at the bottom for
the futures/options situation.

What this provides
------------------
* ``_token()``      — fetch + cache the OAuth2 bearer token.
* ``short_selling(code)``      — newest completed day's short-selling for one
  ticker -> ``{date, short_volume, short_value, short_ratio}`` (or ``None``).
* ``short_selling_all(codes)`` — same, batched -> ``{code: {...}}``.
* ``derivatives_turnover()`` / ``stock_futures(code)`` — documented stubs that
  return ``None``: Kiwoom REST does NOT expose derivatives (see notes below).

Verified API facts (sources)
----------------------------
OAuth2 token  (au10001):
    POST https://api.kiwoom.com/oauth2/token
    headers: Content-Type: application/json;charset=UTF-8
    body:    {"grant_type": "client_credentials",
              "appkey": <KIWOOM_APP_KEY>, "secretkey": <KIWOOM_APP_SECRET>}
    resp:    {"expires_dt": "YYYYMMDDhhmmss", "token_type": "bearer",
              "token": "<access token>", "return_code": 0, "return_msg": "..."}
  Source: openapi.kiwoom.com/guide/apiguide; pabburi.co.kr 접근토큰 발급 page;
          younghwan91/kiwoom-rest-api  src/kiwoom_rest_api/auth.py (issue_token).

공매도추이요청  (ka10014):
    POST https://api.kiwoom.com/api/dostk/shsa
    headers: Content-Type: application/json;charset=UTF-8
             authorization: Bearer <token>
             api-id: ka10014
             cont-yn: N            (continuation flag)
             next-key:             (continuation key)
    body:    {"stk_cd": "005930",  # 종목코드 (required)
              "tm_tp":  "1",       # 시간구분 (1=일자 daily)
              "strt_dt":"YYYYMMDD", # 시작일자
              "end_dt": "YYYYMMDD"} # 종료일자
  Source: openapi.kiwoom.com api guide (path /api/dostk/shsa, api-id ka10014,
          request fields stk_cd/tm_tp/strt_dt/end_dt confirmed verbatim);
          younghwan91/kiwoom-rest-api  src/kiwoom_rest_api/domestic/short_selling.py
          (RESOURCE_URL="/api/dostk/shsa", api_id "ka10014") and base.py
          (header set + return_code/return_msg error contract).

Error contract: a successful HTTP 200 carries ``return_code == 0``. Non-zero
``return_code`` (e.g. 5 = rate limit) signals an API error; ``return_msg`` has
the text.  (Source: younghwan91 base.py BaseClient.request.)

NOTE on response field names
----------------------------
Kiwoom's per-day short-selling fields inside the ka10014 output list are only
documented on the JS-rendered docs portal (not in any static page or wrapper —
the Python wrappers pass the body through and return the raw JSON). To avoid
fabricating keys, ``_parse_short_row`` matches the response **defensively**
against Kiwoom's documented naming convention (``dt`` for date, ``shrts_*`` /
``cvsrtsell_*`` for the short columns, snake_case Korean abbreviations) and the
raw row is logged at DEBUG so the exact keys can be pinned on first live call.
This is the ONLY uncertain part; once ``KIWOOM_APP_KEY``/``KIWOOM_APP_SECRET``
are set on Render, a single live call confirms the keys (see ``__main__``).
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import threading
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
_REAL_BASE = "https://api.kiwoom.com"
_MOCK_BASE = "https://mockapi.kiwoom.com"
# Default to the real (실전) endpoint, overridable via KIWOOM_API_BASE. If the
# supplied key is for the *other* environment, _token() auto-falls back to the
# alternate base (Kiwoom error 8030 = 실전/모의 mismatch) and caches the winner.
_BASE_URL = os.getenv("KIWOOM_API_BASE", _REAL_BASE).rstrip("/")
# The base that last successfully minted a token; _request() reuses it so data
# calls hit the same environment as the token.
_active_base: Optional[str] = None
_TOKEN_PATH = "/oauth2/token"
_SHSA_PATH = "/api/dostk/shsa"        # 공매도 endpoints category
_SHORT_TREND_API_ID = "ka10014"       # 공매도추이요청

_TIMEOUT = 15.0
_RETRIES = 2
# Look back this many calendar days when picking a start date for the range.
_LOOKBACK_DAYS = 14
# Refresh the cached token this many seconds before its real expiry.
_TOKEN_SKEW_SEC = 60

# In-process token cache: (token, epoch_expiry). Guarded by a lock so concurrent
# scheduler jobs don't each mint a token.
_token_cache: tuple[Optional[str], float] = (None, 0.0)
_token_lock = threading.Lock()

# Candidate JSON keys for each output field. The FIRST present key wins.
# Ordered most-likely-first per Kiwoom's snake_case convention. The raw row is
# logged at DEBUG on the first live call so these can be trimmed to the exact
# documented keys with certainty.
_KEYS_DATE = ("dt", "stdt", "trd_dt", "date")
_KEYS_SHORT_VOL = (
    "shrts_qty", "cvsrtsell_trde_qty", "cvsrtsell_qty", "shrts_trde_qty",
    "공매도량", "short_volume",
)
_KEYS_SHORT_VAL = (
    "shrts_trde_prica", "cvsrtsell_trde_prica", "shrts_amt", "공매도거래대금",
    "short_value",
)
_KEYS_SHORT_RATIO = (
    "shrts_wght", "cvsrtsell_wght", "shrts_trde_wght", "공매도비중", "short_ratio",
)
# Possible names for the list that holds the per-day rows.
_KEYS_OUTPUT_LIST = ("shrts_trnsn", "shrts", "output", "out", "data")


# --------------------------------------------------------------------------- #
# Env / helpers
# --------------------------------------------------------------------------- #
def _creds() -> tuple[Optional[str], Optional[str]]:
    """Read Kiwoom REST credentials from env *at call time* (per CLAUDE.md)."""
    return os.getenv("KIWOOM_APP_KEY"), os.getenv("KIWOOM_APP_SECRET")


def _to_int(value: Any) -> Optional[int]:
    """Parse a Kiwoom numeric string (may have commas / sign) into an int."""
    if value is None:
        return None
    s = str(value).strip().replace(",", "").replace("+", "")
    if s in ("", "-", "N/A"):
        return None
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def _to_float(value: Any) -> Optional[float]:
    """Parse a Kiwoom numeric string into a float (e.g. ratio %)."""
    if value is None:
        return None
    s = str(value).strip().replace(",", "").replace("+", "").replace("%", "")
    if s in ("", "-", "N/A"):
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _first(row: dict, keys: tuple[str, ...]) -> Any:
    """Return the value of the first key present (and non-empty) in *row*."""
    for k in keys:
        if k in row and str(row[k]).strip() not in ("", "-"):
            return row[k]
    return None


def _date_to_iso(value: Any) -> str:
    """Normalise ``YYYYMMDD`` / ``YYYY/MM/DD`` to ``YYYY-MM-DD`` (best effort)."""
    s = str(value or "").strip().replace("/", "").replace("-", "")
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return str(value or "")


def _date_range() -> tuple[str, str]:
    """(strt_dt, end_dt) in ``YYYYMMDD`` covering the recent window.

    end_dt = today, strt_dt = today - _LOOKBACK_DAYS. We then pick the newest
    *completed* row from whatever the API returns, so an open/holiday end date
    is harmless.
    """
    today = _dt.date.today()
    start = today - _dt.timedelta(days=_LOOKBACK_DAYS)
    return start.strftime("%Y%m%d"), today.strftime("%Y%m%d")


# --------------------------------------------------------------------------- #
# OAuth2 token
# --------------------------------------------------------------------------- #
def _parse_expiry(expires_dt: Any) -> float:
    """Convert Kiwoom's ``expires_dt`` (``YYYYMMDDhhmmss``, KST) to epoch secs.

    Falls back to "now + 12h" if the field is missing/garbled — Kiwoom tokens
    live ~24h, so a conservative half-life keeps us safe without re-minting
    every call.
    """
    s = str(expires_dt or "").strip()
    if len(s) == 14 and s.isdigit():
        try:
            # Kiwoom returns KST wall-clock; treat as local naive and convert.
            dt = _dt.datetime.strptime(s, "%Y%m%d%H%M%S")
            return dt.timestamp()
        except ValueError:
            pass
    return time.time() + 12 * 3600


def _token(force: bool = False) -> Optional[str]:
    """Return a valid bearer token, minting + caching one if needed.

    Reads ``KIWOOM_APP_KEY`` / ``KIWOOM_APP_SECRET`` from env at call time.
    Returns ``None`` (never raises) if creds are missing or the request fails.
    """
    global _token_cache
    now = time.time()

    if not force:
        tok, exp = _token_cache
        if tok and now < exp - _TOKEN_SKEW_SEC:
            return tok

    app_key, app_secret = _creds()
    if not app_key or not app_secret:
        logger.warning(
            "kiwoom_rest: KIWOOM_APP_KEY/KIWOOM_APP_SECRET not set — "
            "cannot fetch token."
        )
        return None

    global _active_base
    with _token_lock:
        # Re-check under lock (another thread may have just refreshed).
        tok, exp = _token_cache
        if not force and tok and now < exp - _TOKEN_SKEW_SEC:
            return tok

        # Try the configured base first; if the key is for the *other*
        # environment (Kiwoom 8030 = 실전/모의 mismatch), fall back to the
        # alternate base. The base that works is cached in _active_base so
        # subsequent data calls hit the same environment.
        alt = _MOCK_BASE if _BASE_URL == _REAL_BASE else _REAL_BASE
        for base in (_BASE_URL, alt):
            try:
                resp = httpx.post(
                    f"{base}{_TOKEN_PATH}",
                    json={
                        "grant_type": "client_credentials",
                        "appkey": app_key,
                        "secretkey": app_secret,
                    },
                    headers={"Content-Type": "application/json;charset=UTF-8"},
                    timeout=_TIMEOUT,
                )
                resp.raise_for_status()
                data = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                logger.error("kiwoom_rest: token request failed (%s): %s",
                             base, exc)
                continue

            rc = data.get("return_code", 0)
            if rc not in (0, None):
                msg = str(data.get("return_msg") or "")
                # 8030 = environment mismatch -> try the alternate base.
                if "8030" in msg or "실전" in msg or "모의" in msg:
                    logger.warning(
                        "kiwoom_rest: env mismatch on %s (%s) — trying alternate",
                        base, msg,
                    )
                    continue
                logger.error("kiwoom_rest: token error return_code=%s msg=%s",
                             rc, msg)
                return None

            token = data.get("token")
            if not token:
                logger.error(
                    "kiwoom_rest: token response had no 'token' field: %s",
                    {k: v for k, v in data.items() if k != "token"})
                continue

            _active_base = base
            _token_cache = (token, _parse_expiry(data.get("expires_dt")))
            logger.info("kiwoom_rest: obtained bearer token via %s (expires_dt=%s)",
                        base, data.get("expires_dt"))
            return token

        return None


# --------------------------------------------------------------------------- #
# Low-level request
# --------------------------------------------------------------------------- #
def _request(api_id: str, body: dict, cont_yn: str = "N",
             next_key: str = "") -> Optional[dict]:
    """POST to a Kiwoom REST endpoint with the standard header set + retries.

    Returns the parsed JSON dict, or ``None`` on any failure. Refreshes the
    token once on a 401. Honors the ``return_code != 0`` error contract.
    """
    token = _token()
    if not token:
        return None

    url = f"{_active_base or _BASE_URL}{_SHSA_PATH}"
    for attempt in range(1, _RETRIES + 1):
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {token}",
            "api-id": api_id,
            "cont-yn": cont_yn,
            "next-key": next_key,
        }
        try:
            resp = httpx.post(url, headers=headers, json=body, timeout=_TIMEOUT)
            # Expired/invalid token -> refresh once and retry.
            if resp.status_code == 401 and attempt < _RETRIES:
                token = _token(force=True)
                if not token:
                    return None
                continue
            if resp.status_code == 429 and attempt < _RETRIES:
                time.sleep(1.0 * attempt)
                continue
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("kiwoom_rest: %s request failed (attempt %s/%s): %s",
                           api_id, attempt, _RETRIES, exc)
            continue

        rc = data.get("return_code", 0)
        if rc == 5 and attempt < _RETRIES:  # 허용된 요청 개수 초과 (rate limit)
            time.sleep(1.0 * attempt)
            continue
        if rc not in (0, None):
            logger.error("kiwoom_rest: %s return_code=%s msg=%s",
                         api_id, rc, data.get("return_msg"))
            return None
        return data

    return None


# --------------------------------------------------------------------------- #
# Response parsing
# --------------------------------------------------------------------------- #
def _extract_rows(data: dict) -> list[dict]:
    """Pull the per-day list out of a ka10014 response (defensive)."""
    for key in _KEYS_OUTPUT_LIST:
        val = data.get(key)
        if isinstance(val, list) and val and isinstance(val[0], dict):
            return val
    # Some Kiwoom TRs return a single output list under an unknown key — fall
    # back to the first list-of-dicts value in the payload.
    for val in data.values():
        if isinstance(val, list) and val and isinstance(val[0], dict):
            return val
    return []


def _parse_short_row(row: dict) -> Optional[dict]:
    """Map one ka10014 output row to the public short-selling shape."""
    vol = _to_int(_first(row, _KEYS_SHORT_VOL))
    if vol is None:
        return None
    return {
        "date": _date_to_iso(_first(row, _KEYS_DATE)),
        "short_volume": vol,
        "short_value": _to_int(_first(row, _KEYS_SHORT_VAL)),
        "short_ratio": _to_float(_first(row, _KEYS_SHORT_RATIO)),
    }


def _newest_row(rows: list[dict]) -> Optional[dict]:
    """Return the most-recent parseable row (largest date)."""
    parsed = [p for r in rows if (p := _parse_short_row(r)) is not None]
    if not parsed:
        return None
    parsed.sort(key=lambda p: p["date"], reverse=True)
    return parsed[0]


# --------------------------------------------------------------------------- #
# Public API — short selling
# --------------------------------------------------------------------------- #
def short_selling(code: str) -> Optional[dict]:
    """Most recent completed day's short-selling for one ticker.

    Parameters
    ----------
    code:
        A 6-digit KRX ticker, e.g. ``"005930"`` (삼성전자). Padded to 6 digits.

    Returns
    -------
    dict | None
        ``{date, short_volume, short_value, short_ratio}`` for the newest
        completed trading day, or ``None`` if unavailable / creds missing /
        API error. Never fabricated.
    """
    code = str(code).strip().zfill(6)
    strt_dt, end_dt = _date_range()
    body = {"stk_cd": code, "tm_tp": "1", "strt_dt": strt_dt, "end_dt": end_dt}

    data = _request(_SHORT_TREND_API_ID, body)
    if data is None:
        return None

    rows = _extract_rows(data)
    if not rows:
        logger.debug("kiwoom_rest: ka10014 returned no rows for %s. keys=%s",
                     code, list(data.keys()))
        return None

    # Log the raw shape once so the exact response keys can be confirmed live.
    logger.debug("kiwoom_rest: ka10014 sample row for %s: %s", code, rows[0])

    return _newest_row(rows)


def short_selling_all(codes: list[str]) -> dict[str, dict]:
    """Short-selling for several tickers.

    Kiwoom ka10014 is per-ticker (``stk_cd`` is required), so this loops
    ``short_selling`` per code. Failed codes are simply absent (never faked).

    Parameters
    ----------
    codes:
        6-digit KRX tickers, e.g. ``["005930", "000660"]``.

    Returns
    -------
    dict[str, dict]
        ``{code: {date, short_volume, short_value, short_ratio}}``. May be
        empty if creds are missing or Kiwoom is unreachable.
    """
    result: dict[str, dict] = {}
    # Short-circuit the whole batch if we can't even get a token.
    if _token() is None:
        return result
    for code in codes:
        c = str(code).strip().zfill(6)
        row = short_selling(c)
        if row is not None:
            result[c] = row
    return result


# --------------------------------------------------------------------------- #
# Derivatives — NOT available on Kiwoom REST (documented stubs)
# --------------------------------------------------------------------------- #
#
# Confirmed via the official guide (openapi.kiwoom.com) and the most complete
# community wrapper (younghwan91/kiwoom-rest-api, "국내주식 207개 엔드포인트"):
# the Kiwoom **REST** OpenAPI covers domestic STOCKS only (KOSPI/KOSDAQ/KONEX,
# incl. ETF/ETN). It exposes **no** 선물옵션 (futures/options) or 개별주식선물
# (single-stock futures) endpoints — there is no derivatives category under
# /api/dostk and no derivative api-ids in any wrapper. Futures/options remain
# only on the legacy Open API+ (OCX/COM) desktop control, which is out of scope
# for this REST module. (If Kiwoom ever ships REST derivatives, wire the new
# api-ids here.)
#
# These stubs return None so callers can probe without breaking.

def derivatives_turnover() -> None:
    """선물옵션 거래대금 — NOT exposed by Kiwoom REST. Always returns None.

    See module notes: Kiwoom REST is domestic-stock-only. For futures/options
    use the legacy Open API+ (OCX) or an exchange/KRX data source instead
    (this repo already has ``services/krx_derivatives.py``).
    """
    return None


def stock_futures(code: str) -> None:  # noqa: ARG001 - signature kept for callers
    """개별주식선물 — NOT exposed by Kiwoom REST. Always returns None.

    See module notes. For single-stock futures use a non-Kiwoom-REST source
    (e.g. ``services/krx_stock_futures.py`` already in this repo).
    """
    return None


# --------------------------------------------------------------------------- #
# Self-test (live Kiwoom REST) — run once creds are set:
#   KIWOOM_APP_KEY=... KIWOOM_APP_SECRET=... python services/kiwoom_rest.py
# It prints the raw ka10014 row so the exact response keys can be confirmed.
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(message)s")

    if _token() is None:
        print("No token — set KIWOOM_APP_KEY / KIWOOM_APP_SECRET to test live.")
    else:
        print("=== short_selling('005930') 삼성전자 ===")
        print(json.dumps(short_selling("005930"), ensure_ascii=False, indent=2))

        print("\n=== short_selling_all(['005930','000660']) ===")
        print(json.dumps(
            short_selling_all(["005930", "000660"]),
            ensure_ascii=False, indent=2,
        ))

    print("\n=== derivatives (expected None — not on Kiwoom REST) ===")
    print("derivatives_turnover():", derivatives_turnover())
    print("stock_futures('005930'):", stock_futures("005930"))
