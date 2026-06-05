"""
chatbot_auth — tenant isolation for the chatbot data API.

The realty app mints a short-lived signed token from the logged-in session
(server-side, holding the shared secret) and sends it as a Bearer token. The
orchestrator verifies it here and enforces that the caller may only touch THEIR
agent_id (or is a super-admin).

Token format (HMAC-signed, no extra dependency, matches the Node minter):
    base64url(json_payload) + "." + base64url(hmac_sha256(secret, payload_b64))
    payload = {"agent_id": "...", "role": "...", "exp": <unix_seconds>}

Shared secret: env CHATBOT_API_SECRET (set the SAME value on the orchestrator
and the realty app). When the secret is NOT set, the guard is DISABLED (open) —
so existing single-tenant use keeps working until you turn isolation on.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Optional

from fastapi import Depends, Header, HTTPException, Path
from sqlalchemy.orm import Session

from db.base import get_db
from services.logger import log


def _public_agents() -> set:
    return {
        a.strip().lower()
        for a in os.getenv("CHATBOT_PUBLIC_AGENTS", "vip,stock,asset,realty,aiglass").split(",")
        if a.strip()
    }


def _secret() -> str:
    return os.getenv("CHATBOT_API_SECRET", "")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def verify_token(token: str) -> Optional[dict]:
    """Return the payload dict if the token is valid + unexpired, else None."""
    secret = _secret()
    if not secret or not token or "." not in token:
        return None
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        expected = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).digest()
        got = _b64url_decode(sig_b64)
        if not hmac.compare_digest(expected, got):
            return None
        payload = json.loads(_b64url_decode(payload_b64))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


def tenant_guard(
    agent_id: str = Path(...),
    authorization: Optional[str] = Header(default=None),
    x_admin_token: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> str:
    """FastAPI dependency for /api/chatbot/{agent_id}/* data endpoints.
    Conditional + deny-by-default (mirrors the global middleware):

    - CHATBOT_API_SECRET unset → DISABLED (allow) — unaffected until turned on.
    - MANAGED tenant (linked to an app account via app_tenant_id) → require a
      valid Bearer token whose agent_id matches (or a super-admin token).
    - Known chatbot tenant not yet app-linked, OR an allowlisted standalone
      agent (CHATBOT_PUBLIC_AGENTS) → allow.
    - UNKNOWN agent id → 404 (no fail-open to arbitrary ids).
    """
    if not _secret():
        return agent_id  # isolation disabled — backward compatible

    from services import tenant_config
    cfg = tenant_config.get_tenant_config(agent_id, db=db)
    managed = bool(cfg and cfg.get("app_tenant_id"))
    if not managed:
        if cfg is not None or agent_id in _public_agents():
            return agent_id
        raise HTTPException(status_code=404, detail="Unknown or unauthorized business")

    admin = os.getenv("ADMIN_API_TOKEN", "")
    if admin and x_admin_token and hmac.compare_digest(x_admin_token, admin):
        return agent_id

    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Missing or invalid chatbot token")
    if payload.get("agent_id") != agent_id:
        log.warning(
            "tenant_guard: token agent_id mismatch",
            extra={"action": "chatbot.tenant_guard_denied", "agent_id": agent_id},
        )
        raise HTTPException(status_code=403, detail="Not authorized for this business")
    return agent_id
