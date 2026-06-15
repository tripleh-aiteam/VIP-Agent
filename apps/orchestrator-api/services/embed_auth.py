"""
VIP AI Platform — Embed token signing.

Short-lived HMAC-signed tokens that let the VIP boss dashboard open a specific
twin's portal (the /embed iframe) as an authenticated admin, WITHOUT shipping a
secret to the browser and WITHOUT trusting any client-supplied identity.

Flow:
  1. Boss is logged into the dashboard (has a verifiable login session token).
  2. Dashboard calls POST /auth/embed-token (proving admin via the session
     token). The backend mints a token here, bound to {twin_id, principal, exp}.
  3. The opaque token travels to the portal /embed and back to the backend as
     X-Embed-Token. The backend verifies the signature + expiry + twin match on
     every twin API call (see routers/twins.py:_check_twin_access).

The signing secret lives ONLY on the orchestrator (EMBED_SIGNING_SECRET). It is
never exposed to any browser. If the secret is unset, minting/verification fail
closed (no tokens are accepted), so a misconfigured deploy can't open access.

Token format (compact, dependency-free):
    base64url(json({"tid","sub","exp","nonce"})) + "." + hex(HMAC_SHA256(secret, body))
"""

import os
import json
import hmac
import time
import base64
import hashlib
import secrets as _secrets
from typing import Optional


def _secret() -> str:
    return os.getenv("EMBED_SIGNING_SECRET", "")


def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def mint_embed_token(twin_id: str, principal: str, ttl_seconds: int = 3600) -> Optional[str]:
    """Sign a token binding twin_id + principal + expiry. None if no secret set."""
    secret = _secret()
    if not secret:
        return None
    payload = {
        "tid": str(twin_id),
        "sub": principal,
        "exp": int(time.time()) + int(ttl_seconds),
        "nonce": _secrets.token_urlsafe(8),
    }
    body = _b64u_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_embed_token(token: Optional[str]) -> Optional[dict]:
    """Return the payload dict if the token is valid + unexpired, else None."""
    secret = _secret()
    if not secret or not token or "." not in token:
        return None
    body, _, sig = token.partition(".")
    expected = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        payload = json.loads(_b64u_decode(body))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    if not payload.get("tid") or not payload.get("sub"):
        return None
    return payload
