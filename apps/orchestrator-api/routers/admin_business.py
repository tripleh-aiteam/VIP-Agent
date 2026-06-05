"""
admin_business — super-admin endpoints to onboard & manage BUSINESSES (tenants).

This is what makes the chatbot sellable: the owner adds a new buyer here (their
business profile + Kakao channel + credentials) in one call, instead of editing
the DB by hand. Each business is one `agent_id`.

Auth: send header `X-Admin-Token` matching env `ADMIN_API_TOKEN`. If
ADMIN_API_TOKEN is not set, the endpoints are open (dev) — SET IT in production.
"""

from __future__ import annotations

import os
import secrets
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from db.base import get_db
from db.models import ChatbotAgentSetting, ChatbotChannelMapping
from services import chatbot_conversation_service as conv_service
from services import tenant_config
from services.logger import log

router = APIRouter(prefix="/api/chatbot/admin", tags=["chatbot-admin"])


def _public_base_url() -> str:
    return os.getenv("PUBLIC_BASE_URL", "https://vip-orchestrator.onrender.com").rstrip("/")


def require_admin(x_admin_token: Optional[str] = Header(default=None)) -> None:
    expected = os.getenv("ADMIN_API_TOKEN", "")
    if expected and x_admin_token != expected:
        raise HTTPException(status_code=403, detail="Admin token required")
    # No token configured → allow (dev). Production must set ADMIN_API_TOKEN.


class BusinessBody(BaseModel):
    agent_id: str
    business_name: Optional[str] = None
    bot_display_name: Optional[str] = None
    industry: Optional[str] = None
    persona: Optional[str] = None
    language_default: Optional[str] = None
    service_area: Optional[str] = None
    greeting: Optional[str] = None
    # Kakao connection (optional at create time; can be added later)
    kakao_channel_id: Optional[str] = None       # bot.id from i 오픈빌더
    kakao_access_token: Optional[str] = None      # optional (only for push)
    webhook_secret: Optional[str] = None          # auto-generated if omitted
    auto_reply: bool = True                        # Boss-OUT = bot answers customers


@router.get("/businesses")
def list_businesses(db: Session = Depends(get_db), _: None = Depends(require_admin)):
    """All businesses with their Kakao channel + connection status."""
    out: list[dict[str, Any]] = []
    for t in tenant_config.list_tenants(db):
        ch = (
            db.query(ChatbotChannelMapping)
            .filter(
                ChatbotChannelMapping.agent_id == t["agent_id"],
                ChatbotChannelMapping.channel == "kakao",
            )
            .first()
        )
        out.append({
            **t,
            "kakao_channel_id": ch.provider_channel_id if ch else None,
            "kakao_connected": bool(ch and ch.active),
            "has_access_token": bool(ch and ch.kakao_access_token),
            "webhook_secret_set": bool(ch and ch.webhook_secret),
        })
    return {"businesses": out, "count": len(out)}


@router.post("/businesses")
def upsert_business(
    body: BusinessBody,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    """Create or update a business in one shot: profile card + Kakao channel
    mapping + credentials + auto-reply mode. Returns the webhook URL + secret
    the buyer pastes into their i 오픈빌더 skill."""
    aid = (body.agent_id or "").strip().lower()
    if not aid or not aid.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(status_code=400, detail="agent_id must be a simple slug (letters/numbers/-/_)")

    # 1. Profile card (tenant config)
    tfields = {
        k: getattr(body, k)
        for k in ("business_name", "bot_display_name", "industry", "persona",
                  "language_default", "service_area", "greeting")
        if getattr(body, k) is not None
    }
    tenant_config.upsert_tenant_config(db, aid, **tfields)

    # 2. Kakao channel mapping + credentials (optional)
    channel_info: Optional[dict[str, Any]] = None
    if body.kakao_channel_id:
        webhook_secret = (body.webhook_secret or "").strip() or secrets.token_hex(16)
        row = conv_service.register_channel_mapping(
            db, agent_id=aid, channel="kakao",
            provider_channel_id=body.kakao_channel_id.strip(),
            display_name=body.business_name,
        )
        if body.kakao_access_token:
            row.kakao_access_token = body.kakao_access_token.strip()
        row.webhook_secret = webhook_secret
        db.commit()
        conv_service.invalidate_credentials(aid)
        channel_info = {
            "kakao_channel_id": body.kakao_channel_id.strip(),
            "webhook_url": f"{_public_base_url()}/api/chatbot/webhook/kakao",
            "webhook_secret": webhook_secret,
        }

    # 3. Auto-reply (Boss-OUT) so the bot answers customers
    if body.auto_reply:
        setting = (
            db.query(ChatbotAgentSetting)
            .filter(ChatbotAgentSetting.agent_id == aid)
            .first()
        )
        if not setting:
            setting = ChatbotAgentSetting(agent_id=aid, mode_override="out", auto_mode_enabled=True)
            db.add(setting)
        else:
            setting.mode_override = "out"
            setting.mode_expires_at = None
        db.commit()

    # 4. Make it take effect immediately (clear routing/mode caches)
    try:
        from routers.kakao_webhook import invalidate_business_caches
        invalidate_business_caches()
    except Exception as e:
        log.warning(f"admin_business: cache invalidation skipped: {e}")

    log.info(f"admin_business: upserted business '{aid}'", extra={"action": "admin.business_upsert"})
    return {
        "ok": True,
        "agent_id": aid,
        "channel": channel_info,
        "next_steps": (
            None if channel_info else
            "No Kakao channel yet — add the buyer's i오픈빌더 bot.id to connect KakaoTalk."
        ),
    }


@router.delete("/businesses/{agent_id}")
def deactivate_business(
    agent_id: str,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    """Soft-disable a business (stops Kakao routing + deactivates the card)."""
    aid = agent_id.strip().lower()
    tenant_config.upsert_tenant_config(db, aid, active=False)
    db.query(ChatbotChannelMapping).filter(
        ChatbotChannelMapping.agent_id == aid
    ).update({ChatbotChannelMapping.active: False})
    db.commit()
    conv_service.invalidate_credentials(aid)
    try:
        from routers.kakao_webhook import invalidate_business_caches
        invalidate_business_caches()
    except Exception:
        pass
    return {"ok": True, "agent_id": aid, "active": False}
