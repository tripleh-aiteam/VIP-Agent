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
_RETRIES = 4   # extra attempts so a 모의(mock) rate-limit burst eventually clears

# Short-selling result cache: {code: (epoch, data)}. 공매도 is T-1 daily data that
# only changes once a day, so caching avoids re-hitting the rate-limited mock API
# for every report/chatbot call, AND lets a transient failure fall back to the
# last good value instead of a blank column.
_short_cache: dict[str, tuple[float, dict]] = {}
_SHORT_TTL = 6 * 3600
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
    "shrts_wght", "cvsrtsell_wght", "shrts_trde_wght", "vol_rt", "trde_wght",
    "shrts_vol_rt", "공매도비중", "short_ratio",
)
# Total trading volume for the day — used to COMPUTE 공매도 비중 when the API
# does not return a ratio field directly (공매도량 / 거래량 × 100).
_KEYS_TOTAL_VOL = (
    "trde_qty", "tot_trde_qty", "acc_trde_qty", "trqu", "거래량",
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
             next_key: str = "", path: Optional[str] = None) -> Optional[dict]:
    """POST to a Kiwoom REST endpoint with the standard header set + retries.

    Returns the parsed JSON dict, or ``None`` on any failure. Refreshes the
    token once on a 401. Honors the ``return_code != 0`` error contract.
    ``path`` defaults to the short-selling endpoint; pass another (e.g.
    ``/api/dostk/stkinfo`` for ka10001 current-price) to reuse this transport.
    """
    token = _token()
    if not token:
        return None

    url = f"{_active_base or _BASE_URL}{path or _SHSA_PATH}"
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
    ratio = _to_float(_first(row, _KEYS_SHORT_RATIO))
    total_vol = _to_int(_first(row, _KEYS_TOTAL_VOL))
    # Compute 공매도 비중 (= 공매도량 / 거래량 × 100) if the API gave no ratio field.
    if ratio is None and total_vol and total_vol > 0:
        ratio = round(vol / total_vol * 100, 2)
    return {
        "date": _date_to_iso(_first(row, _KEYS_DATE)),
        "short_volume": vol,
        "short_value": _to_int(_first(row, _KEYS_SHORT_VAL)),
        "short_ratio": ratio,
        "total_volume": total_vol,
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
_CP_PRICE_KEYS = ("cur_prc", "stck_prpr", "prpr")
_CP_PREV_KEYS = ("base_pric", "stck_sdpr", "pred_close_pric")
_CP_NAME_KEYS = ("stk_nm", "hts_kor_isnm", "isnm")
_CP_OPEN_KEYS = ("open_pric", "opngpric", "stck_oprc", "oprc")
_CP_HIGH_KEYS = ("high_pric", "hgpr", "stck_hgpr")
_CP_LOW_KEYS = ("low_pric", "lwpr", "stck_lwpr")
_CP_VOL_KEYS = ("trde_qty", "acc_trde_qty", "tot_trde_qty", "trqu", "거래량")


def minute_bars(code: str, tic: str = "1", count: int = 120) -> Optional[list[dict]]:
    """Intraday per-minute OHLCV candles (주식분봉차트, ka10080). tic = minutes per bar
    ('1','3','5'...). Returns up to `count` most-recent bars, OLDEST→NEWEST:
    [{ts:'YYYY-MM-DD HH:MM', open, high, low, close, volume}]. None on failure.
    Kiwoom returns the day's bars incl. pre/after-market ticks where available."""
    code = str(code).strip().zfill(6)

    def _f():
        d = _request("ka10080", {"stk_cd": code, "tic_scope": str(tic),
                                  "upd_stkpc_tp": "1"}, path="/api/dostk/chart")
        if not isinstance(d, dict):
            return None
        rows = d.get("stk_min_pole_chart_qry") or []
        out = []
        for r in rows:
            tm = str(r.get("cntr_tm") or "")            # YYYYMMDDHHMMSS
            if len(tm) < 12:
                continue
            ts = f"{tm[0:4]}-{tm[4:6]}-{tm[6:8]} {tm[8:10]}:{tm[10:12]}"
            o, h = _to_int(r.get("open_pric")), _to_int(r.get("high_pric"))
            lo, c = _to_int(r.get("low_pric")), _to_int(r.get("cur_prc"))
            v = _to_int(r.get("trde_qty"))
            if c is None:
                continue
            out.append({"ts": ts, "open": abs(o) if o else None, "high": abs(h) if h else None,
                        "low": abs(lo) if lo else None, "close": abs(c), "volume": abs(v) if v else 0})
        out = list(reversed(out))[-count:]              # API is newest-first → oldest→newest
        return out or None
    return _rt_cached(f"min:{code}:{tic}", _f)


def current_price(code: str) -> Optional[dict]:
    """LIVE current price for a KR 6-digit code via Kiwoom REST (ka10001,
    /api/dostk/stkinfo). Returns ``{price, prev_close, change_pct, name}`` or
    ``None`` on any failure (caller falls back to Naver). Reads creds from env."""
    code = (code or "").strip()
    if not code.isdigit():
        return None
    data = _request("ka10001", {"stk_cd": code}, path="/api/dostk/stkinfo")
    if not isinstance(data, dict):
        return None
    price = _to_float(_first(data, _CP_PRICE_KEYS))
    if price is None:
        return None
    price = abs(price)
    prev = _to_float(_first(data, _CP_PREV_KEYS))
    prev = abs(prev) if prev is not None else None
    change_pct = round((price - prev) / prev * 100, 2) if prev else None
    name = _first(data, _CP_NAME_KEYS)
    _o = _to_float(_first(data, _CP_OPEN_KEYS))
    _h = _to_float(_first(data, _CP_HIGH_KEYS))
    _l = _to_float(_first(data, _CP_LOW_KEYS))
    _v = _to_float(_first(data, _CP_VOL_KEYS))
    return {"price": price, "prev_close": prev, "change_pct": change_pct,
            "name": (str(name).strip() if name else None),
            "open": abs(_o) if _o is not None else None,
            "high": abs(_h) if _h is not None else None,
            "low": abs(_l) if _l is not None else None,
            "volume": abs(_v) if _v is not None else None}


# --------------------------------------------------------------------------- #
# Real-time signals (the day-trading layer) — order book, 수급, program trades.
# Verified live (api-id + path + field names) against the Kiwoom REST API.
# Short TTL cache so enriching a brief doesn't hammer the rate-limited endpoint.
# --------------------------------------------------------------------------- #
_rt_cache: dict[str, tuple[float, Any]] = {}
_RT_TTL = 20.0   # seconds — intraday signals refresh fast but not per-call


def _rt_cached(key: str, fn, ttl: float | None = None):
    hit = _rt_cache.get(key)
    if hit and (time.time() - hit[0]) < (ttl if ttl is not None else _RT_TTL):
        return hit[1]
    val = fn()
    if val is not None:
        _rt_cache[key] = (time.time(), val)
    return val


def _ob_field(side: str, level: int, kind: str) -> str:
    """Kiwoom ka10004 호가 field name. side='sel'(ask)/'buy'(bid); level 1-10;
    kind='bid'(price)/'req'(qty). Level 1 uses '_fpr_', levels 2-10 use '_Nth_pre_'."""
    seg = "fpr" if level == 1 else f"{level}th_pre"
    return f"{side}_{seg}_{kind}"


def order_book(code: str, ttl: float | None = None) -> Optional[dict]:
    """LIVE order book (호가, ka10004) -> ALL 10 bid + 10 ask levels + imbalance.
    The microstructure signal the day-trader reads: tot_buy_req >> tot_sel_req =
    buying pressure. The exchange only publishes 10 levels — the deeper 'memory' of
    scrolled-out levels is assembled OVER TIME by the collector (orderbook_memory).
    Returns {best_bid, best_ask, bid_qty, ask_qty, tot_bid, tot_ask, imbalance,
             levels:[{side:'ask'/'bid', level:1-10, price, qty}]}."""
    code = str(code).strip().zfill(6)

    def _f():
        d = _request("ka10004", {"stk_cd": code}, path="/api/dostk/mrkcond")
        if not isinstance(d, dict):
            return None
        tot_bid = _to_int(d.get("tot_buy_req"))
        tot_ask = _to_int(d.get("tot_sel_req"))
        imb = None
        if tot_bid is not None and tot_ask is not None and (tot_bid + tot_ask) > 0:
            imb = round((tot_bid - tot_ask) / (tot_bid + tot_ask), 3)
        levels = []
        for lv in range(1, 11):                       # 10-deep, both sides
            for kside, oside in (("sel", "ask"), ("buy", "bid")):
                p = _to_int(d.get(_ob_field(kside, lv, "bid")))
                q = _to_int(d.get(_ob_field(kside, lv, "req")))
                if p:
                    levels.append({"side": oside, "level": lv, "price": abs(p),
                                   "qty": abs(q) if q is not None else 0})
        bb, ba = _to_int(d.get("buy_fpr_bid")), _to_int(d.get("sel_fpr_bid"))
        return {
            "best_bid": abs(bb) if bb is not None else None,
            "best_ask": abs(ba) if ba is not None else None,
            "bid_qty": _to_int(d.get("buy_fpr_req")),
            "ask_qty": _to_int(d.get("sel_fpr_req")),
            "tot_bid": tot_bid, "tot_ask": tot_ask, "imbalance": imb,
            "levels": levels,
        }
    return _rt_cached(f"ob:{code}", _f, ttl=ttl)


def executions(code: str, ttl: float = 1.0) -> Optional[list[dict]]:
    """LIVE tick executions (체결정보, ka10003) — the DEALS actually happening, not the
    resting book. Returns newest-first [{time:'HH:MM:SS', price, qty, dir:+1/-1/0,
    acc_volume}] (~30 rows). dir = aggressor side read from the signed trade qty
    (+ = buyer-initiated 매수체결, − = seller-initiated 매도체결)."""
    code = str(code).strip().zfill(6)

    def _f():
        d = _request("ka10003", {"stk_cd": code}, path="/api/dostk/stkinfo")
        if not isinstance(d, dict):
            return None
        out = []
        for r in (d.get("cntr_infr") or []):
            tm = str(r.get("tm") or "")
            raw_q = str(r.get("cntr_trde_qty") or "0")
            q = _to_int(raw_q)
            p = _to_int(r.get("cur_prc"))
            if not p or q is None:
                continue
            out.append({
                "time": f"{tm[0:2]}:{tm[2:4]}:{tm[4:6]}" if len(tm) >= 6 else tm,
                "price": abs(p), "qty": abs(q),
                "dir": 1 if raw_q.strip().startswith("+") else -1 if raw_q.strip().startswith("-") else 0,
                "acc_volume": _to_int(r.get("acc_trde_qty")),
                "acc_amount": _to_int(r.get("acc_trde_prica")),
            })
        return out or None
    return _rt_cached(f"exe:{code}", _f, ttl=ttl)


def investor_flows(code: str) -> Optional[dict]:
    """LIVE intraday 수급 (종목별 투자자/기관, ka10059) -> net buying by investor
    type INCLUDING 금투 (financial-investment) — richer than Naver's foreign/inst.
    Returns the latest row {date, individual, foreign, institution, fin_invest, price}.
    Net values are buy-minus-sell (negative = net selling)."""
    code = str(code).strip().zfill(6)

    def _f():
        today = _dt.date.today().strftime("%Y%m%d")
        d = _request("ka10059", {"stk_cd": code, "dt": today, "amt_qty_tp": "2",
                                 "trde_tp": "0", "unit_tp": "1000"},
                     path="/api/dostk/stkinfo")
        if not isinstance(d, dict):
            return None
        rows = d.get("stk_invsr_orgn") or []
        if not rows:
            return None
        r = rows[0]                                   # most recent day/point
        cp = _to_int(r.get("cur_prc"))
        return {
            "date": _date_to_iso(r.get("dt")),
            "individual": _to_int(r.get("ind_invsr")),
            "foreign": _to_int(r.get("frgnr_invsr")),
            "institution": _to_int(r.get("orgn")),
            "fin_invest": _to_int(r.get("fnnc_invt")),   # 금투 — the analyst's signal
            "price": abs(cp) if cp is not None else None,
        }
    return _rt_cached(f"inv:{code}", _f)


def program_trade(code: str) -> Optional[dict]:
    """LIVE program-trading net (종목별 프로그램매매, ka90013) -> {date, net_amt,
    net_qty}. Positive = program net buying (index/basket inflow)."""
    code = str(code).strip().zfill(6)

    def _f():
        d = _request("ka90013", {"stk_cd": code}, path="/api/dostk/mrkcond")
        if not isinstance(d, dict):
            return None
        rows = d.get("stk_daly_prm_trde_trnsn") or []
        if not rows:
            return None
        r = rows[0]
        return {"date": _date_to_iso(r.get("dt")),
                "net_amt": _to_int(r.get("prm_netprps_amt")),
                "net_qty": _to_int(r.get("prm_netprps_qty"))}
    return _rt_cached(f"prm:{code}", _f)


def realtime_signals(code: str) -> dict:
    """Bundle the live day-trading signals for one stock (order book + 수급 +
    program). Each piece is None if unavailable; never raises. Reads creds at
    call time, so it no-ops cleanly without Kiwoom keys."""
    return {
        "order_book": order_book(code),
        "flows": investor_flows(code),
        "program": program_trade(code),
    }


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

    # Serve a fresh cached value if we have one (avoids hammering the mock API).
    cached = _short_cache.get(code)
    if cached and (time.time() - cached[0]) < _SHORT_TTL:
        return cached[1]

    strt_dt, end_dt = _date_range()
    body = {"stk_cd": code, "tm_tp": "1", "strt_dt": strt_dt, "end_dt": end_dt}

    data = _request(_SHORT_TREND_API_ID, body)
    if data is None:
        # Transient failure (rate limit / timeout) — fall back to the last good
        # value if we have one, rather than returning a blank.
        return cached[1] if cached else None

    rows = _extract_rows(data)
    if not rows:
        logger.debug("kiwoom_rest: ka10014 returned no rows for %s. keys=%s",
                     code, list(data.keys()))
        return cached[1] if cached else None

    # Log the raw shape once so the exact response keys can be confirmed live.
    logger.debug("kiwoom_rest: ka10014 sample row for %s: %s", code, rows[0])

    parsed = _newest_row(rows)
    if parsed:
        _short_cache[code] = (time.time(), parsed)
        return parsed
    return cached[1] if cached else None


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
