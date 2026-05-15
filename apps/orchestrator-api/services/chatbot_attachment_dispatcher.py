"""
chatbot_attachment_dispatcher — Boss-OUT autonomous file sending.

When the bot is operating autonomously (Boss-OUT) and a customer asks
something like "도면 보여주세요" (show me the floor plan) or "계약서
양식 좀 보내주세요" (send the contract template), the bot should not
just reply in text — it should *attach the actual file*.

Pipeline:

1. Boss uploads reusable files (floor plans, brochures, contract templates,
   business cards) via the dashboard. Each is stored in `chatbot_agent_assets`
   with `keywords_json` listing trigger words.

2. After generating a text reply in Boss-OUT mode, the reply service calls
   `find_relevant_attachment(agent_id, customer_text)`. We score each
   enabled asset by keyword overlap with the customer's message and return
   the best match if it crosses a confidence threshold.

3. `dispatch_autonomous_attachment(...)` sends the file via the channel
   client (Kakao image / text-with-link fallback for files), appends a
   `bot_meta.status="auto-attachment"` message row, and bumps
   `send_count` + `last_sent_at` on the asset for analytics.

Why keyword matching first (not LLM): keyword match is deterministic,
near-zero latency, near-zero cost, and the boss explicitly authored the
keywords — so false positives are bounded by what the boss configured.
An LLM-based fallback can be added later but isn't necessary for MVP."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from db.base import SessionLocal
from db.models import ChatbotAgentAsset, ChatbotConversation
from services import chatbot_conversation_service as conv_service
from services.logger import log


# Minimum number of matched keyword tokens to trigger an autonomous send.
# 1 keyword is enough — the boss-authored keywords are specific by design
# (e.g. "평면도" is not going to false-match casually).
MIN_KEYWORD_MATCHES = 1


# ============================================================================
#  Match — pick the best asset for a customer message
# ============================================================================

def find_relevant_attachment(
    agent_id: str,
    customer_text: str,
    *,
    db: Optional[Session] = None,
) -> Optional[ChatbotAgentAsset]:
    """Return the highest-scoring enabled asset for this agent that matches
    keywords in `customer_text`, or None if no asset crosses the threshold.

    Scoring: count of asset keywords appearing as substrings (case-insensitive)
    in the customer message. Ties broken by `send_count DESC` so the more
    commonly-used asset wins — that's usually the right answer."""
    if not customer_text or not customer_text.strip():
        return None

    owned_db = False
    if db is None:
        db = SessionLocal()
        owned_db = True
    try:
        assets = (
            db.query(ChatbotAgentAsset)
            .filter(
                ChatbotAgentAsset.agent_id == agent_id,
                ChatbotAgentAsset.enabled.is_(True),
            )
            .all()
        )
        if not assets:
            return None

        text_lower = customer_text.lower()
        scored: list[tuple[int, int, ChatbotAgentAsset]] = []
        for asset in assets:
            keywords = asset.keywords_json or []
            if not isinstance(keywords, list):
                continue
            matches = sum(
                1 for kw in keywords
                if isinstance(kw, str) and kw.strip() and kw.lower() in text_lower
            )
            if matches >= MIN_KEYWORD_MATCHES:
                scored.append((matches, asset.send_count or 0, asset))

        if not scored:
            return None
        # Best match: highest match-count, then highest send_count
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return scored[0][2]
    finally:
        if owned_db:
            db.close()


# ============================================================================
#  Dispatch — send the asset through the conversation's channel
# ============================================================================

async def dispatch_autonomous_attachment(
    *,
    db: Session,
    agent_id: str,
    conversation: ChatbotConversation,
    asset: ChatbotAgentAsset,
    caption: Optional[str] = None,
) -> dict[str, Any]:
    """Send `asset` via the conversation's channel client and persist a
    matching outgoing message. Bumps the asset's `send_count` for analytics.

    Returns: {"ok": bool, "asset_id": str, "kind": str, "url": str}"""
    kind = (asset.file_kind or "file").lower()
    url = asset.file_url
    caption = caption or asset.label

    # Persist outgoing message FIRST so the dashboard shows it even if the
    # downstream channel send hiccups (caller can retry).
    msg_kwargs: dict[str, Any] = {
        "author": "bot",
        "kind": kind,
        "text": caption,
        "bot_meta": {
            "status": "auto-attachment",
            "assetId": str(asset.id),
            "assetLabel": asset.label,
        },
    }
    if kind == "image":
        msg_kwargs["image_url"] = url
        msg_kwargs["image_caption"] = caption
    elif kind == "voice":
        msg_kwargs["voice_url"] = url
    else:
        msg_kwargs["file_url"] = url
        msg_kwargs["file_name"] = asset.label
        msg_kwargs["file_mime"] = asset.file_mime

    msg = conv_service.append_message(db, agent_id, conversation.id, **msg_kwargs)

    # Bump usage counters
    asset.send_count = (asset.send_count or 0) + 1
    asset.last_sent_at = datetime.utcnow()
    db.commit()

    # Channel dispatch
    await _send_via_channel(
        db=db,
        agent_id=agent_id,
        conversation=conversation,
        kind=kind,
        url=url,
        caption=caption,
        filename=asset.label,
    )

    log.info(
        f"chatbot_attachment: auto-sent asset '{asset.label}' for conv {conversation.id}",
        extra={"action": "chatbot.auto_attachment_sent"},
    )
    return {
        "ok": True,
        "asset_id": str(asset.id),
        "kind": kind,
        "url": url,
        "message_id": str(msg.id) if msg else None,
    }


async def _send_via_channel(
    *,
    db: Session,
    agent_id: str,
    conversation: ChatbotConversation,
    kind: str,
    url: str,
    caption: Optional[str],
    filename: str,
) -> None:
    """Route to the right channel client. Mirrors the dispatcher used by
    the manual attachment endpoint."""
    if conversation.channel == "kakao":
        try:
            from services import kakao_client
            customer = conv_service.get_customer(db, agent_id, conversation.customer_id)
            receiver_uuid = customer.kakao_user_id if customer else None
            if kind == "image":
                await asyncio.to_thread(
                    kakao_client.send_image,
                    agent_id=agent_id,
                    conversation_id=str(conversation.id),
                    image_url=url,
                    caption=caption,
                    receiver_uuid=receiver_uuid,
                )
            else:
                fallback_text = (
                    f"{filename}\n{url}"
                    if not caption
                    else f"{caption}\n{filename}\n{url}"
                )
                await asyncio.to_thread(
                    kakao_client.send_text,
                    agent_id=agent_id,
                    conversation_id=str(conversation.id),
                    text=fallback_text,
                    receiver_uuid=receiver_uuid,
                )
        except Exception as e:
            log.warning(f"chatbot_attachment: kakao dispatch failed: {e}")
    elif conversation.channel == "phone":
        log.info(
            "chatbot_attachment: phone channel doesn't support attachments — skipped"
        )
    else:
        log.info(f"chatbot_attachment: unhandled channel {conversation.channel}")


# ============================================================================
#  CRUD — dashboard hooks for managing the asset library
# ============================================================================

def list_assets(
    agent_id: str,
    *,
    enabled_only: bool = False,
    db: Optional[Session] = None,
) -> list[ChatbotAgentAsset]:
    owned_db = False
    if db is None:
        db = SessionLocal()
        owned_db = True
    try:
        q = db.query(ChatbotAgentAsset).filter(ChatbotAgentAsset.agent_id == agent_id)
        if enabled_only:
            q = q.filter(ChatbotAgentAsset.enabled.is_(True))
        return q.order_by(ChatbotAgentAsset.created_at.desc()).all()
    finally:
        if owned_db:
            db.close()


def create_asset(
    *,
    agent_id: str,
    label: str,
    file_url: str,
    file_kind: str = "file",
    file_mime: Optional[str] = None,
    description: Optional[str] = None,
    keywords: Optional[list[str]] = None,
    enabled: bool = True,
    created_by=None,
    db: Optional[Session] = None,
) -> ChatbotAgentAsset:
    """Register a new asset. Keywords should be the trigger words the boss
    wants the bot to listen for (e.g. ["도면", "평면도", "floor plan"])."""
    owned_db = False
    if db is None:
        db = SessionLocal()
        owned_db = True
    try:
        cleaned_keywords = _clean_keywords(keywords or [])
        asset = ChatbotAgentAsset(
            agent_id=agent_id,
            label=label,
            description=description,
            file_url=file_url,
            file_kind=file_kind,
            file_mime=file_mime,
            keywords_json=cleaned_keywords,
            enabled=enabled,
            created_by=created_by,
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)
        log.info(
            f"chatbot_attachment: created asset '{label}' for {agent_id} "
            f"(keywords={cleaned_keywords})",
            extra={"action": "chatbot.asset_created"},
        )
        return asset
    finally:
        if owned_db:
            db.close()


def delete_asset(
    agent_id: str,
    asset_id,
    *,
    db: Optional[Session] = None,
) -> bool:
    owned_db = False
    if db is None:
        db = SessionLocal()
        owned_db = True
    try:
        asset = (
            db.query(ChatbotAgentAsset)
            .filter(
                ChatbotAgentAsset.id == asset_id,
                ChatbotAgentAsset.agent_id == agent_id,
            )
            .first()
        )
        if not asset:
            return False
        db.delete(asset)
        db.commit()
        return True
    finally:
        if owned_db:
            db.close()


def _clean_keywords(raw: list[str]) -> list[str]:
    """Strip whitespace, drop empties, dedupe case-insensitively while
    preserving the first-occurrence casing."""
    seen: set[str] = set()
    out: list[str] = []
    for kw in raw:
        if not isinstance(kw, str):
            continue
        s = re.sub(r"\s+", " ", kw).strip()
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out
