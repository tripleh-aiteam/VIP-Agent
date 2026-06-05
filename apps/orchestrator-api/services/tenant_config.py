"""
tenant_config — the per-business "profile card" that makes the chatbot product
multi-tenant / white-label.

Backward-compatible by design: if a business has NO tenant row (or no persona),
callers fall back to the original hardcoded behaviour, so existing agents like
'aiglass' (Triple H) are completely unaffected.

Read path is cached in-process (TTL) so it never adds a DB round-trip to the
Kakao hot path that races the ~5s skill timeout.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from sqlalchemy.orm import Session

from db.base import SessionLocal
from db.models import ChatbotTenant
from services.logger import log

# agent_id -> (fetched_at, config_dict_or_None)
_CACHE: dict[str, tuple[float, Optional[dict[str, Any]]]] = {}
_TTL = 120.0  # 2 minutes — long enough for the hot path, short enough that
              # an edit in the admin UI shows up quickly.


def _serialize(t: ChatbotTenant) -> dict[str, Any]:
    return {
        "agent_id": t.agent_id,
        "app_tenant_id": t.app_tenant_id,
        "business_name": t.business_name,
        "bot_display_name": t.bot_display_name,
        "industry": t.industry,
        "persona": t.persona,
        "language_default": t.language_default or "auto",
        "service_area": t.service_area,
        "greeting": t.greeting,
        "logo_url": t.logo_url,
        "primary_color": t.primary_color,
        "features": t.features_json or {},
        "active": bool(t.active),
    }


def get_tenant_config(agent_id: str, db: Optional[Session] = None) -> Optional[dict[str, Any]]:
    """Return the tenant's config dict, or None when no row exists (→ caller
    uses legacy behaviour). Cached for _TTL seconds."""
    if not agent_id:
        return None
    hit = _CACHE.get(agent_id)
    if hit and (time.time() - hit[0]) < _TTL:
        return hit[1]
    owns = db is None
    if owns:
        db = SessionLocal()
    try:
        row = (
            db.query(ChatbotTenant)
            .filter(ChatbotTenant.agent_id == agent_id, ChatbotTenant.active.is_(True))
            .first()
        )
        cfg = _serialize(row) if row else None
    except Exception as e:
        log.warning(f"tenant_config.get failed for {agent_id}: {e}")
        cfg = None
    finally:
        if owns:
            db.close()
    _CACHE[agent_id] = (time.time(), cfg)
    return cfg


def invalidate(agent_id: str) -> None:
    _CACHE.pop(agent_id, None)


def list_tenants(db: Session) -> list[dict[str, Any]]:
    rows = db.query(ChatbotTenant).order_by(ChatbotTenant.created_at.asc()).all()
    return [_serialize(r) for r in rows]


def upsert_tenant_config(db: Session, agent_id: str, **fields: Any) -> dict[str, Any]:
    """Create or update a tenant's profile card. Only provided fields change."""
    row = db.query(ChatbotTenant).filter(ChatbotTenant.agent_id == agent_id).first()
    if not row:
        row = ChatbotTenant(agent_id=agent_id)
        db.add(row)
    allowed = {
        "business_name", "bot_display_name", "industry", "persona",
        "language_default", "service_area", "greeting", "logo_url",
        "primary_color", "active",
    }
    for k, v in fields.items():
        if k in allowed:
            setattr(row, k, v)
        elif k == "features":
            row.features_json = v
    db.commit()
    db.refresh(row)
    invalidate(agent_id)
    return _serialize(row)


def _slug_for_app_tenant(app_tenant_id: str) -> str:
    """Derive a stable chatbot agent_id from an app tenant uuid."""
    clean = "".join(c for c in (app_tenant_id or "") if c.isalnum())[:12]
    return f"t_{clean}" if clean else "t_unknown"


def resolve_or_provision_by_app_tenant(
    db: Session, app_tenant_id: str, *, email: Optional[str] = None,
    business_name: Optional[str] = None,
) -> dict[str, Any]:
    """Map a logged-in app tenant → its chatbot agent. If already linked, return
    it. Otherwise auto-provision a FRESH isolated chatbot agent for this tenant
    (so a new buyer immediately gets their own empty chatbot). Never returns
    another tenant's agent."""
    if not app_tenant_id:
        raise ValueError("app_tenant_id required")
    row = (
        db.query(ChatbotTenant)
        .filter(ChatbotTenant.app_tenant_id == app_tenant_id)
        .first()
    )
    if row:
        return _serialize(row)
    # Provision a fresh, isolated agent for this app tenant.
    agent_id = _slug_for_app_tenant(app_tenant_id)
    existing = db.query(ChatbotTenant).filter(ChatbotTenant.agent_id == agent_id).first()
    if existing:
        # agent_id slug collision with an unlinked row — link it.
        existing.app_tenant_id = app_tenant_id
        if business_name and not existing.business_name:
            existing.business_name = business_name
        db.commit()
        db.refresh(existing)
        invalidate(agent_id)
        return _serialize(existing)
    row = ChatbotTenant(
        agent_id=agent_id,
        app_tenant_id=app_tenant_id,
        business_name=business_name or (email.split("@")[0] if email else None),
        language_default="auto",
        features_json={"assistant": True, "kakao": True, "insights": True, "knowledge": True, "calls": False},
        active=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    invalidate(agent_id)
    return _serialize(row)


def link_app_tenant(db: Session, app_tenant_id: str, agent_id: str) -> dict[str, Any]:
    """Super-admin: link an app tenant to an EXISTING agent (e.g. the owner's
    app tenant → 'aiglass' so they keep their existing chatbot + data)."""
    # Clear any other row that currently claims this app_tenant_id.
    db.query(ChatbotTenant).filter(
        ChatbotTenant.app_tenant_id == app_tenant_id,
        ChatbotTenant.agent_id != agent_id,
    ).update({ChatbotTenant.app_tenant_id: None})
    row = db.query(ChatbotTenant).filter(ChatbotTenant.agent_id == agent_id).first()
    if not row:
        row = ChatbotTenant(agent_id=agent_id, active=True)
        db.add(row)
    row.app_tenant_id = app_tenant_id
    db.commit()
    db.refresh(row)
    invalidate(agent_id)
    return _serialize(row)


def has_custom_persona(cfg: Optional[dict[str, Any]]) -> bool:
    """True when this tenant should use the generic config-driven prompt
    instead of the legacy hardcoded one."""
    return bool(cfg and (cfg.get("persona") or "").strip())


def build_tenant_system_prompt(cfg: dict[str, Any], knowledge_text: str, lang: str) -> str:
    """Build a generic, white-label system prompt from a tenant's profile card
    + their retrieved knowledge. Used for NEW tenants (legacy agents keep the
    hardcoded prompt)."""
    biz = cfg.get("business_name") or cfg.get("bot_display_name") or "our business"
    bot_name = cfg.get("bot_display_name") or biz
    persona = (cfg.get("persona") or "").strip()
    area = (cfg.get("service_area") or "").strip()

    lang_rule = (
        "■ 언어 규칙 (절대 어기지 마세요)\n"
        "고객이 한국어로 질문하면 반드시 한국어로만, 영어로 질문하면 영어로만 답변하세요.\n\n"
        if lang == "ko" else
        "■ Language Rule (strict)\n"
        "Reply in the SAME language the customer used (Korean→Korean, English→English).\n\n"
    )
    parts = [lang_rule]
    parts.append(
        f"You are the friendly customer-service assistant for {biz} "
        f"(you may refer to yourself as {bot_name}). {persona}"
    )
    parts.append(
        "\nKeep replies short (1-3 sentences), warm, and natural for a chat app; "
        "use 1-2 emojis when it fits. Only state facts found in the knowledge "
        "below — never invent details. If you don't know, say a staff member "
        "will confirm and follow up. Always nudge the conversation forward."
    )
    if area:
        parts.append(f"\n■ Service area: {area}")
    if knowledge_text:
        parts.append("\n■ Business knowledge (cite only what's here):\n" + knowledge_text[:4000])
    return "\n".join(parts)
