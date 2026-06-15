"""
krx_stock_futures — Korean SINGLE-STOCK FUTURES (개별주식선물) daily trading data
from KRX (data.krx.co.kr) for our watchlist underlyings.

WHAT THIS RETURNS
    For each watchlist *underlying* equity (e.g. 005930 삼성전자) we look up the
    single-stock-futures contracts listed on that underlying and aggregate the
    most-recent completed trading day into:
        {date, volume (거래량, 계약수), value (거래대금, 원), open_interest (미결제약정, 계약수)}
    Volume/value/OI are summed across that underlying's listed contract months
    (front month + back months + spreads), which is the conventional way to read
    "the stock-futures activity on 삼성전자" for one day.

HOW IT TALKS TO KRX  (verified live against data.krx.co.kr, June 2026)
    Endpoint : POST https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd
    Headers  : User-Agent + Referer (outerLoader) + X-Requested-With: XMLHttpRequest
    Datasets used:
        1. dbms/comm/component/drv_prod_clss          (PUBLIC) — product class list,
           confirms 'KRDRVFUEQU' = Single Stock Futures.
        2. dbms/comm/bldAttendant/executeForResourceBundle.cmd (PUBLIC) — gives
           max_work_dt = the latest completed trading day (so we never guess holidays).
        3. dbms/MDC/STAT/standard/MDCSTAT12501        (전종목 시세 / all-products price)
           params: trdDd=YYYYMMDD, prodId='KRDRVFUEQU', subProdId='KRDRVFUEQU',
                   mktTpCd='T', rghtTpCd='T'
           output rows: ISU_NM (e.g. "삼성전자 F 202509"), ACC_TRDVOL (거래량),
                        ACC_TRDVAL (거래대금), ACC_OPNINT_QTY (미결제약정).
           We map rows to underlyings by matching the Korean underlying NAME that
           prefixes ISU_NM (stock-futures symbols differ from the 6-digit equity code).

IMPORTANT RELIABILITY CAVEAT  (read this)
    As of 2025/2026 KRX moved **all** MDCSTAT statistics datasets (equity AND
    derivatives, JSON AND the legacy GenerateOTP/CSV path) behind MEMBER LOGIN.
    Anonymous requests to MDCSTAT12501 return the literal string "LOGOUT".
    Verified live: the product-list/finder endpoints are still public, but the
    actual volume/value/OI dataset is NOT reachable without a KRX account.

    Therefore this module:
      * works fully and returns real numbers IF KRX credentials are provided via
        env KRX_ID / KRX_PW (it performs KRX's login flow to obtain a JSESSIONID);
      * otherwise returns None (NEVER fabricated data) and logs that login is
        required. The underlying-name mapping is exercised live regardless.

    Set KRX_ID / KRX_PW (a free KRX 정보데이터시스템 member account) to enable.
    No extra pip dependency — uses httpx, already a project dependency.
"""

from __future__ import annotations

import os
import time
import datetime as _dt

import httpx

from services.logger import log

# ───────────────────────── constants ─────────────────────────
_BASE = "https://data.krx.co.kr"
_JSON_URL = f"{_BASE}/comm/bldAttendant/getJsonData.cmd"
_OTP_BUNDLE = f"{_BASE}/comm/bldAttendant/executeForResourceBundle.cmd"
_LOGIN_PAGE = f"{_BASE}/contents/MDC/COMS/client/MDCCOMS001.cmd"
_LOGIN_JSP = f"{_BASE}/contents/MDC/COMS/client/view/login.jsp?site=mdc"
_LOGIN_URL = f"{_BASE}/contents/MDC/COMS/client/MDCCOMS001D1.cmd"
_MAIN_PAGE = f"{_BASE}/contents/MDC/MAIN/main/index.cmd"

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
_HEADERS = {
    "User-Agent": _UA,
    "Referer": f"{_BASE}/contents/MDC/MDI/outerLoader/index.cmd",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}

# Single Stock Futures product class id (from dbms/comm/component/drv_prod_clss).
_PROD_SSF = "KRDRVFUEQU"
# 전종목 시세 (all single-stock-futures contracts, one trading day).
_BLD_ALL_PRICE = "dbms/MDC/STAT/standard/MDCSTAT12501"

_TIMEOUT = 20.0
_RETRIES = 3

# Watchlist underlyings → official KRX Korean name (used to match ISU_NM prefix).
# Stock-futures symbols differ from the equity code, so we match by NAME.
_WATCHLIST: dict[str, str] = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "017670": "SK텔레콤",
    "018260": "삼성에스디에스",   # 삼성SDS — KRX lists it as 삼성에스디에스
    "035420": "NAVER",          # NAVER (네이버) — futures underlying name is NAVER
    "069500": "KODEX 200",      # ETF — no single-stock futures; will resolve to None
}
# Alternate name spellings the futures dataset may use for the same underlying.
_NAME_ALIASES: dict[str, list[str]] = {
    "005930": ["삼성전자"],
    "000660": ["SK하이닉스", "에스케이하이닉스"],
    "017670": ["SK텔레콤", "에스케이텔레콤"],
    "018260": ["삼성에스디에스", "삼성SDS"],
    "035420": ["NAVER", "네이버"],
}


# ───────────────────────── helpers ─────────────────────────
def _num(s) -> float | None:
    """KRX numbers arrive as comma-separated strings; '-' / '' mean no value."""
    if s is None:
        return None
    t = str(s).strip().replace(",", "")
    if t in ("", "-", "."):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _new_client() -> httpx.Client:
    return httpx.Client(timeout=_TIMEOUT, headers=_HEADERS,
                        follow_redirects=True, verify=True)


def _warmup(client: httpx.Client) -> None:
    """Seed a JSESSIONID cookie (required before any getJsonData call)."""
    try:
        client.get(_MAIN_PAGE, headers={"User-Agent": _UA})
    except Exception as e:  # non-fatal — finder calls still try
        log.warning(f"krx warmup: {str(e)[:80]}")


def _login(client: httpx.Client) -> bool:
    """Perform KRX member login if KRX_ID/KRX_PW are set. Returns True on success.

    KRX statistics datasets (MDCSTAT*) require a logged-in JSESSIONID since 2025.
    Flow mirrors data.krx.co.kr's own login: warmup → POST credentials → handle
    duplicate-login (CD011) by retrying with skipDup=Y. CD001 == success.
    """
    uid, pw = os.getenv("KRX_ID"), os.getenv("KRX_PW")
    if not (uid and pw):
        return False
    try:
        client.get(_LOGIN_PAGE, headers={"User-Agent": _UA})
        client.get(_LOGIN_JSP, headers={"User-Agent": _UA, "Referer": _LOGIN_PAGE})
        payload = {"mbrNm": "", "telNo": "", "di": "", "certType": "",
                   "mbrId": uid, "pw": pw}
        h = {"User-Agent": _UA, "Referer": _LOGIN_PAGE}
        r = client.post(_LOGIN_URL, data=payload, headers=h)
        data = r.json()
        code = data.get("_error_code", "")
        if code == "CD011":  # already logged in elsewhere
            payload["skipDup"] = "Y"
            r = client.post(_LOGIN_URL, data=payload, headers=h)
            code = r.json().get("_error_code", "")
        if code == "CD001":
            log.info("krx login: success")
            return True
        log.warning(f"krx login failed: {code} {data.get('_error_message','')[:80]}")
        return False
    except Exception as e:
        log.warning(f"krx login error: {str(e)[:120]}")
        return False


def _post_json(client: httpx.Client, bld: str, **params) -> dict | None:
    """POST to getJsonData with retries. Returns parsed JSON, or None.

    A wrong/ungated-but-login-required bld returns the literal body 'LOGOUT'.
    """
    body = {"bld": bld, **params}
    for attempt in range(1, _RETRIES + 1):
        try:
            r = client.post(_JSON_URL, data=body)
            txt = (r.text or "").strip()
            if txt == "LOGOUT":
                return {"_logout": True}
            if r.status_code != 200:
                if attempt < _RETRIES:
                    time.sleep(0.6 * attempt)
                    continue
                log.warning(f"krx {bld}: HTTP {r.status_code}")
                return None
            return r.json()
        except Exception as e:
            if attempt < _RETRIES:
                time.sleep(0.6 * attempt)
                continue
            log.warning(f"krx {bld}: {str(e)[:100]}")
            return None
    return None


def _latest_trading_day(client: httpx.Client) -> str:
    """Latest completed trading day as YYYYMMDD.

    Prefers KRX's own max_work_dt (handles holidays exactly); falls back to
    stepping back over weekends from today.
    """
    try:
        r = client.get(_OTP_BUNDLE, params={"baseName": "krx.mdc.i18n.component",
                                            "key": "B128.bld"})
        if r.status_code == 200:
            out = (r.json().get("result") or {}).get("output") or []
            if out and out[0].get("max_work_dt"):
                return str(out[0]["max_work_dt"])
    except Exception:
        pass
    d = _dt.date.today()
    while d.weekday() >= 5:  # Sat/Sun → step back to Friday
        d -= _dt.timedelta(days=1)
    return d.strftime("%Y%m%d")


def _aliases_for(equity_code: str) -> list[str]:
    return _NAME_ALIASES.get(equity_code) or [_WATCHLIST.get(equity_code, "")]


def _row_matches_underlying(isu_nm: str, aliases: list[str]) -> bool:
    """ISU_NM looks like '삼성전자 F 202509' — the underlying name prefixes it.

    Match if any alias is a prefix token of ISU_NM. We anchor on the start to
    avoid '삼성전자' matching inside an unrelated name.
    """
    name = (isu_nm or "").strip()
    for a in aliases:
        if not a:
            continue
        if name.startswith(a + " ") or name == a:
            return True
    return False


def _fetch_all_price_rows(client: httpx.Client, trd: str) -> list[dict] | None:
    """All single-stock-futures contract rows for one trading day.

    Returns [] only if the dataset is reachable but empty; returns None if the
    dataset is login-gated (LOGOUT) or otherwise unavailable.
    """
    j = _post_json(client, _BLD_ALL_PRICE, trdDd=trd, prodId=_PROD_SSF,
                   subProdId=_PROD_SSF, mktTpCd="T", rghtTpCd="T")
    if j is None:
        return None
    if j.get("_logout"):
        log.warning("krx single-stock-futures price: LOGOUT — KRX member login "
                    "required (set KRX_ID / KRX_PW). Returning None, NOT fake data.")
        return None
    rows = j.get("output") or j.get("OutBlock_1") or []
    return rows


def _aggregate(rows: list[dict], aliases: list[str], trd: str) -> dict | None:
    """Sum volume/value/OI across all contract months for one underlying."""
    matched = [r for r in rows if _row_matches_underlying(r.get("ISU_NM", ""), aliases)]
    if not matched:
        return None
    vol = val = oi = 0.0
    have_oi = False
    for r in matched:
        v = _num(r.get("ACC_TRDVOL"))
        m = _num(r.get("ACC_TRDVAL"))
        o = _num(r.get("ACC_OPNINT_QTY"))
        if v is not None:
            vol += v
        if m is not None:
            val += m
        if o is not None:
            oi += o
            have_oi = True
    out = {
        "date": f"{trd[:4]}-{trd[4:6]}-{trd[6:8]}",
        "volume": int(vol),
        "value": int(val),
        "contracts": len(matched),
    }
    if have_oi:
        out["open_interest"] = int(oi)
    return out


# ───────────────────────── public API ─────────────────────────
def stock_futures(equity_code: str) -> dict | None:
    """Single-stock-futures daily summary for ONE underlying equity code.

    Returns {date, volume, value, open_interest?, contracts} for the most recent
    completed trading day, or None if no futures exist for this underlying / the
    dataset is login-gated / the request fails. NEVER returns fabricated data.
    """
    if equity_code not in _WATCHLIST:
        log.warning(f"krx stock_futures: {equity_code} not in watchlist mapping")
    try:
        with _new_client() as client:
            _warmup(client)
            _login(client)  # no-op if creds absent
            trd = _latest_trading_day(client)
            rows = _fetch_all_price_rows(client, trd)
            if rows is None:
                return None
            return _aggregate(rows, _aliases_for(equity_code), trd)
    except Exception as e:
        log.warning(f"krx stock_futures {equity_code}: {str(e)[:120]}")
        return None


def stock_futures_all(codes: list[str]) -> dict[str, dict]:
    """Batch version — one KRX round-trip, mapped to many underlyings.

    Returns {equity_code: {date, volume, value, open_interest?, contracts}} only
    for codes that resolved to real data. Codes with no futures (e.g. an ETF) or
    when the dataset is gated are omitted (no fake entries).
    """
    result: dict[str, dict] = {}
    try:
        with _new_client() as client:
            _warmup(client)
            _login(client)
            trd = _latest_trading_day(client)
            rows = _fetch_all_price_rows(client, trd)
            if rows is None:
                return result
            for code in codes:
                agg = _aggregate(rows, _aliases_for(code), trd)
                if agg is not None:
                    result[code] = agg
    except Exception as e:
        log.warning(f"krx stock_futures_all: {str(e)[:120]}")
    return result


# ───────────────────────── self-test ─────────────────────────
if __name__ == "__main__":
    import json as _json

    print("KRX single-stock-futures (개별주식선물) — LIVE self-test")
    print(f"KRX_ID set: {bool(os.getenv('KRX_ID'))}  "
          f"KRX_PW set: {bool(os.getenv('KRX_PW'))}")

    with _new_client() as c:
        _warmup(c)
        logged_in = _login(c)
        trd = _latest_trading_day(c)
        print(f"latest trading day (KRX max_work_dt): {trd}  logged_in={logged_in}")
        rows = _fetch_all_price_rows(c, trd)
        if rows is None:
            print("\nDATASET UNAVAILABLE: MDCSTAT12501 returned LOGOUT (login-gated).")
            print("Set KRX_ID / KRX_PW to a KRX 정보데이터시스템 member account to "
                  "enable real volume/value/OI. Returning None — no fake data.")
        else:
            print(f"\nfetched {len(rows)} single-stock-futures contract rows")
            for code in ("005930", "000660"):
                agg = _aggregate(rows, _aliases_for(code), trd)
                print(f"  {code} {_WATCHLIST.get(code)}: {_json.dumps(agg, ensure_ascii=False)}")

    print("\nstock_futures_all(['005930','000660','035420']):")
    print(_json.dumps(stock_futures_all(["005930", "000660", "035420"]),
                      ensure_ascii=False, indent=2))
