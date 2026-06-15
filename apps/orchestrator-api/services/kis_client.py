"""KIS Developers (한국투자증권) REST OpenAPI — OAuth client + request helper.

This module talks to Korea Investment & Securities' **KIS Developers** REST API
(``openapi.koreainvestment.com:9443``). Unlike Kiwoom REST (domestic stocks
only — see ``kiwoom_rest.py``), KIS exposes a full domestic **선물옵션**
(futures/options) quotation surface, which ``kis_derivatives.py`` builds on.

What this provides
------------------
* ``get_token()`` — fetch + cache the OAuth2 ``access_token`` (thread-safe,
  cached until ~expiry). Reads ``KIS_APP_KEY`` / ``KIS_APP_SECRET`` from env at
  call time. Returns ``None`` (never raises) if creds are missing / call fails.
* ``_get(path, tr_id, params)`` — GET helper that attaches the full KIS header
  set (authorization / appkey / appsecret / tr_id / custtype) and returns parsed
  JSON or ``None``.

Verified API facts (sources cited inline)
-----------------------------------------
OAuth2 token issuance:
    POST https://openapi.koreainvestment.com:9443/oauth2/tokenP
    headers: content-type: application/json
    body:    {"grant_type": "client_credentials",
              "appkey": <KIS_APP_KEY>, "appsecret": <KIS_APP_SECRET>}
    resp:    {"access_token": "<token>",
              "token_type": "Bearer",
              "expires_in": 86400,                       # seconds (~24h)
              "access_token_token_expired": "YYYY-MM-DD HH:MM:SS"}  # KST
  Source: koreainvestment/open-trading-api ``examples_user/kis_auth.py``
          (token path ``/oauth2/tokenP``, body grant_type/appkey/appsecret,
          response access_token + access_token_token_expired + expires_in);
          KIS Developers portal apiportal.koreainvestment.com/apiservice
          (접근토큰 발급 / "OAuth 인증").

Common request headers for every quotation call (verified, kis_auth.py):
    content-type : application/json; charset=UTF-8
    authorization: Bearer <access_token>
    appkey       : <KIS_APP_KEY>
    appsecret    : <KIS_APP_SECRET>
    tr_id        : <per-endpoint transaction id, e.g. FHMIF10000000>
    custtype     : P            # P=개인 (individual)
    tr_cont      : ""           # continuation flag ("" first page, "N" next)
  hashkey is NOT required for GET quotation calls (only POST order calls), per
  kis_auth.py ("hashkey ... can be omitted").

Error contract: a successful HTTP 200 carries ``rt_cd == "0"``. Non-"0"
``rt_cd`` is an API error; ``msg1`` / ``msg_cd`` carry the text.
  Source: kis_auth.py response handling + KIS Developers common spec.
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
# Real-trading (실전) domain; paper/모의투자 lives at ...:29443. We default to
# real but auto-fall back to the mock domain if the supplied key is a 모의투자
# key (KIS rejects it with EGW00105 'invalid AppSecret' on the wrong domain).
_REAL_BASE = "https://openapi.koreainvestment.com:9443"
_MOCK_BASE = "https://openapivts.koreainvestment.com:29443"
_BASE_URL = (os.getenv("KIS_API_BASE") or _REAL_BASE).rstrip("/")
# The base that last successfully minted a token; _get() reuses it so data calls
# hit the same environment as the token.
_active_base: Optional[str] = None
_TOKEN_PATH = "/oauth2/tokenP"

_TIMEOUT = 15.0
_RETRIES = 2
# Refresh the cached token this many seconds before its real expiry.
_TOKEN_SKEW_SEC = 300  # 5 min; KIS tokens live ~24h so this is conservative.

# In-process token cache: (token, epoch_expiry). Guarded by a lock so concurrent
# scheduler jobs don't each mint a token (KIS rate-limits token issuance ~1/min).
_token_cache: tuple[Optional[str], float] = (None, 0.0)
_token_lock = threading.Lock()


# --------------------------------------------------------------------------- #
# Env / helpers
# --------------------------------------------------------------------------- #
def _creds() -> tuple[Optional[str], Optional[str]]:
    """Read KIS credentials from env *at call time* (per CLAUDE.md)."""
    return os.getenv("KIS_APP_KEY"), os.getenv("KIS_APP_SECRET")


def _parse_expiry(data: dict) -> float:
    """Best-effort epoch expiry from a KIS token response.

    Prefers ``expires_in`` (seconds). Falls back to parsing
    ``access_token_token_expired`` ("YYYY-MM-DD HH:MM:SS", KST wall-clock), then
    to a safe "now + 12h" (KIS tokens live ~24h).
    """
    exp_in = data.get("expires_in")
    if exp_in is not None:
        try:
            return time.time() + float(exp_in)
        except (ValueError, TypeError):
            pass

    expired_at = str(data.get("access_token_token_expired") or "").strip()
    if expired_at:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M%S"):
            try:
                return _dt.datetime.strptime(expired_at, fmt).timestamp()
            except ValueError:
                continue

    return time.time() + 12 * 3600


# --------------------------------------------------------------------------- #
# OAuth2 token
# --------------------------------------------------------------------------- #
def get_token(force: bool = False) -> Optional[str]:
    """Return a valid KIS ``access_token``, minting + caching one if needed.

    Reads ``KIS_APP_KEY`` / ``KIS_APP_SECRET`` from env at call time. Thread-safe
    (double-checked locking). Returns ``None`` (never raises) if creds are
    missing or the request fails.
    """
    global _token_cache, _active_base
    now = time.time()

    if not force:
        tok, exp = _token_cache
        if tok and now < exp - _TOKEN_SKEW_SEC:
            return tok

    app_key, app_secret = _creds()
    if not app_key or not app_secret:
        logger.warning(
            "kis_client: KIS_APP_KEY/KIS_APP_SECRET not set — cannot fetch token."
        )
        return None

    with _token_lock:
        # Re-check under lock (another thread may have just refreshed).
        tok, exp = _token_cache
        if not force and tok and now < exp - _TOKEN_SKEW_SEC:
            return tok

        # Try the configured base first; if the key belongs to the *other*
        # environment (EGW00105 invalid AppSecret on the wrong domain), fall back
        # to the alternate base. The base that works is cached in _active_base.
        alt = _MOCK_BASE if _BASE_URL == _REAL_BASE else _REAL_BASE
        for base in (_BASE_URL, alt):
            try:
                resp = httpx.post(
                    f"{base}{_TOKEN_PATH}",
                    json={
                        "grant_type": "client_credentials",
                        "appkey": app_key,
                        "appsecret": app_secret,
                    },
                    headers={"content-type": "application/json"},
                    timeout=_TIMEOUT,
                )
                data = resp.json() if resp.headers.get(
                    "content-type", "").startswith("application/json") else {}
            except (httpx.HTTPError, ValueError) as exc:
                logger.error("kis_client: token request failed (%s): %s", base, exc)
                continue

            token = data.get("access_token")
            if token:
                _active_base = base
                _token_cache = (token, _parse_expiry(data))
                logger.info(
                    "kis_client: obtained access_token via %s (expires_in=%s)",
                    base, data.get("expires_in"),
                )
                return token

            # No token — log the sanitized error and try the alternate domain.
            ecode = str(data.get("error_code") or data.get("msg_cd") or "")
            logger.warning(
                "kis_client: no token from %s (error_code=%s) — trying alternate",
                base, ecode,
            )
        return None


# --------------------------------------------------------------------------- #
# Low-level GET request
# --------------------------------------------------------------------------- #
def _headers(token: str, tr_id: str, tr_cont: str = "") -> dict:
    """Build the standard KIS request header set (verified vs kis_auth.py)."""
    app_key, app_secret = _creds()
    return {
        "content-type": "application/json; charset=UTF-8",
        "authorization": f"Bearer {token}",
        "appkey": app_key or "",
        "appsecret": app_secret or "",
        "tr_id": tr_id,
        "custtype": "P",  # 개인 (individual)
        "tr_cont": tr_cont,
    }


def _get(path: str, tr_id: str, params: dict,
         tr_cont: str = "") -> Optional[dict]:
    """GET a KIS quotation endpoint with the full header set + retries.

    Parameters
    ----------
    path:
        Endpoint path, e.g. ``/uapi/domestic-futureoption/v1/quotations/inquire-price``.
    tr_id:
        Transaction id for the endpoint, e.g. ``FHMIF10000000``.
    params:
        Query params (the ``FID_*`` keys).
    tr_cont:
        Continuation flag ("" first page, "N" for next page).

    Returns
    -------
    dict | None
        Parsed JSON body, or ``None`` on any failure. Refreshes the token once
        on a 401/EGW token error. Honors the ``rt_cd != "0"`` error contract.
    """
    token = get_token()
    if not token:
        return None

    url = f"{_active_base or _BASE_URL}{path}"
    for attempt in range(1, _RETRIES + 1):
        try:
            resp = httpx.get(
                url,
                headers=_headers(token, tr_id, tr_cont),
                params=params,
                timeout=_TIMEOUT,
            )
            # Expired/invalid token -> refresh once and retry.
            if resp.status_code in (401, 403) and attempt < _RETRIES:
                token = get_token(force=True)
                if not token:
                    return None
                continue
            if resp.status_code == 429 and attempt < _RETRIES:
                time.sleep(1.0 * attempt)
                continue
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "kis_client: %s GET failed (attempt %s/%s): %s",
                tr_id, attempt, _RETRIES, exc,
            )
            continue

        rt_cd = str(data.get("rt_cd", "0"))
        # Some token-expiry conditions arrive as a 200 with an EGW error code.
        if rt_cd != "0":
            msg_cd = str(data.get("msg_cd", ""))
            if msg_cd.startswith("EGW") and attempt < _RETRIES:
                token = get_token(force=True)
                if not token:
                    return None
                continue
            logger.error(
                "kis_client: %s rt_cd=%s msg_cd=%s msg1=%s",
                tr_id, rt_cd, data.get("msg_cd"), data.get("msg1"),
            )
            return None
        return data

    return None


# --------------------------------------------------------------------------- #
# Self-test (live KIS) — run once creds are set:
#   KIS_APP_KEY=... KIS_APP_SECRET=... python services/kis_client.py
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(message)s")
    tok = get_token()
    if tok:
        print("OK: got token (len=%d)" % len(tok))
    else:
        print("No token — set KIS_APP_KEY / KIS_APP_SECRET to test live.")
