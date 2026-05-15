"""
chatbot_conversation_service — multi-tenant CRUD for the Chatbot Inbox.

EVERY public function takes `agent_id: str` as its first argument. The
router resolves agent_id from the URL path (REST) or the Kakao webhook
mapping (incoming), never trusts the wire payload.

Pairs with:
  - db/models.py: ChatbotConversation, ChatbotMessage, ChatbotCustomer,
                  ChatbotConversationAction, ChatbotChannelMapping
  - routers/chatbot_inbox.py: REST endpoints + WebSocket
  - services/chatbot_mode_detector.py: Boss-IN vs Boss-OUT
  - services/chatbot_reply_service.py: Boss-IN drafts + Boss-OUT autonomy
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import and_, desc, func, or_
from sqlalchemy.orm import Session

from db.models import (
    ChatbotChannelMapping,
    ChatbotConversation,
    ChatbotConversationAction,
    ChatbotCustomer,
    ChatbotMessage,
)


# ============================================================================
#  Channel mapping — webhook handler resolves agent_id via this
# ============================================================================

def resolve_agent_id_from_channel(
    db: Session, channel: str, provider_channel_id: str
) -> Optional[str]:
    """Given an incoming Kakao Channel ID (or other provider), return our
    internal agent_id. Returns None if no active mapping exists — webhook
    must reject the event (likely misconfigured or forged)."""
    row = (
        db.query(ChatbotChannelMapping)
        .filter(
            ChatbotChannelMapping.channel == channel,
            ChatbotChannelMapping.provider_channel_id == provider_channel_id,
            ChatbotChannelMapping.active.is_(True),
        )
        .first()
    )
    return row.agent_id if row else None


def register_channel_mapping(
    db: Session,
    agent_id: str,
    channel: str,
    provider_channel_id: str,
    display_name: Optional[str] = None,
    api_key_env_var: Optional[str] = None,
    webhook_secret_env_var: Optional[str] = None,
) -> ChatbotChannelMapping:
    """Idempotent registration — updates the row if it exists."""
    existing = (
        db.query(ChatbotChannelMapping)
        .filter(
            ChatbotChannelMapping.channel == channel,
            ChatbotChannelMapping.provider_channel_id == provider_channel_id,
        )
        .first()
    )
    if existing:
        existing.agent_id = agent_id
        existing.display_name = display_name or existing.display_name
        existing.api_key_env_var = api_key_env_var or existing.api_key_env_var
        existing.webhook_secret_env_var = webhook_secret_env_var or existing.webhook_secret_env_var
        existing.active = True
        db.commit()
        return existing
    row = ChatbotChannelMapping(
        agent_id=agent_id,
        channel=channel,
        provider_channel_id=provider_channel_id,
        display_name=display_name,
        api_key_env_var=api_key_env_var,
        webhook_secret_env_var=webhook_secret_env_var,
        active=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ============================================================================
#  Customer — find or create by identifier (phone or kakao_user_id)
# ============================================================================

def find_or_create_customer(
    db: Session,
    agent_id: str,
    *,
    name: Optional[str] = None,
    phone: Optional[str] = None,
    kakao_user_id: Optional[str] = None,
    avatar_url: Optional[str] = None,
) -> ChatbotCustomer:
    """Find a customer by their channel identifier (phone or kakao_user_id),
    or create a new row if not found. Updates display fields on each call
    so customer info stays fresh."""
    q = db.query(ChatbotCustomer).filter(ChatbotCustomer.agent_id == agent_id)
    if kakao_user_id:
        existing = q.filter(ChatbotCustomer.kakao_user_id == kakao_user_id).first()
        if existing:
            if name and not existing.name:
                existing.name = name
            if phone and not existing.phone:
                existing.phone = phone
            if avatar_url:
                existing.avatar_url = avatar_url
            existing.last_seen_at = datetime.utcnow()
            db.commit()
            db.refresh(existing)
            return existing
    if phone:
        existing = q.filter(ChatbotCustomer.phone == phone).first()
        if existing:
            if name and not existing.name:
                existing.name = name
            if kakao_user_id and not existing.kakao_user_id:
                existing.kakao_user_id = kakao_user_id
            if avatar_url:
                existing.avatar_url = avatar_url
            existing.last_seen_at = datetime.utcnow()
            db.commit()
            db.refresh(existing)
            return existing
    # No match — create new
    row = ChatbotCustomer(
        agent_id=agent_id,
        name=name,
        phone=phone,
        kakao_user_id=kakao_user_id,
        avatar_url=avatar_url,
        last_seen_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_customer(db: Session, agent_id: str, customer_id: UUID | str) -> Optional[ChatbotCustomer]:
    return (
        db.query(ChatbotCustomer)
        .filter(ChatbotCustomer.agent_id == agent_id, ChatbotCustomer.id == customer_id)
        .first()
    )


def update_customer_notes(
    db: Session, agent_id: str, customer_id: UUID | str, notes: str
) -> Optional[ChatbotCustomer]:
    cust = get_customer(db, agent_id, customer_id)
    if not cust:
        return None
    cust.notes = notes
    db.commit()
    db.refresh(cust)
    return cust


def update_customer_tags(
    db: Session, agent_id: str, customer_id: UUID | str, tags: list[str]
) -> Optional[ChatbotCustomer]:
    cust = get_customer(db, agent_id, customer_id)
    if not cust:
        return None
    cust.tags_json = tags
    db.commit()
    db.refresh(cust)
    return cust


# ============================================================================
#  Conversation — list / get / open / close / patch
# ============================================================================

def list_conversations(
    db: Session,
    agent_id: str,
    *,
    status: Optional[str] = None,
    channel: Optional[str] = None,
    limit: int = 100,
) -> list[ChatbotConversation]:
    """Recent conversations for an agent, newest-first."""
    q = db.query(ChatbotConversation).filter(ChatbotConversation.agent_id == agent_id)
    if status:
        q = q.filter(ChatbotConversation.status == status)
    if channel:
        q = q.filter(ChatbotConversation.channel == channel)
    return q.order_by(desc(ChatbotConversation.last_message_at)).limit(limit).all()


def get_conversation(
    db: Session, agent_id: str, conversation_id: UUID | str
) -> Optional[ChatbotConversation]:
    return (
        db.query(ChatbotConversation)
        .filter(
            ChatbotConversation.agent_id == agent_id,
            ChatbotConversation.id == conversation_id,
        )
        .first()
    )


def find_or_create_conversation(
    db: Session,
    agent_id: str,
    *,
    channel: str,
    customer_id: UUID,
    voice_call_id: Optional[UUID] = None,
) -> ChatbotConversation:
    """Find the active (non-resolved) conversation for this (agent, channel,
    customer) tuple. If none exists OR the latest is older than 24h, create
    a new one. This is how a "fresh" inquiry after a quiet period becomes a
    new conversation thread instead of resurrecting old ones."""
    cutoff = datetime.utcnow() - timedelta(hours=24)
    existing = (
        db.query(ChatbotConversation)
        .filter(
            ChatbotConversation.agent_id == agent_id,
            ChatbotConversation.channel == channel,
            ChatbotConversation.customer_id == customer_id,
            ChatbotConversation.status != "resolved",
            ChatbotConversation.last_message_at >= cutoff,
        )
        .order_by(desc(ChatbotConversation.last_message_at))
        .first()
    )
    if existing:
        return existing
    row = ChatbotConversation(
        agent_id=agent_id,
        channel=channel,
        customer_id=customer_id,
        status="needs_reply",
        last_message_at=datetime.utcnow(),
        voice_call_id=voice_call_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def patch_conversation(
    db: Session,
    agent_id: str,
    conversation_id: UUID | str,
    **fields,
) -> Optional[ChatbotConversation]:
    """Generic field patch — for status, urgency, suggested_reply_json, etc."""
    conv = get_conversation(db, agent_id, conversation_id)
    if not conv:
        return None
    for k, v in fields.items():
        if hasattr(conv, k):
            setattr(conv, k, v)
    db.commit()
    db.refresh(conv)
    return conv


def mark_conversation_read(
    db: Session, agent_id: str, conversation_id: UUID | str
) -> Optional[ChatbotConversation]:
    return patch_conversation(db, agent_id, conversation_id, unread_count=0)


def resolve_conversation(
    db: Session, agent_id: str, conversation_id: UUID | str
) -> Optional[ChatbotConversation]:
    return patch_conversation(
        db, agent_id, conversation_id,
        status="resolved",
        ended_at=datetime.utcnow(),
        unread_count=0,
        suggested_reply_json=None,
    )


def escalate_conversation(
    db: Session,
    agent_id: str,
    conversation_id: UUID | str,
    *,
    to: str,
    reason: str,
) -> Optional[ChatbotConversation]:
    """Mark a conversation as escalated + persist the escalation metadata.
    Caller is responsible for actually delivering the alert via Telegram /
    Slack / email — see services.voice_escalation as the reference."""
    return patch_conversation(
        db, agent_id, conversation_id,
        status="escalated",
        urgency="high",
        escalation_json={
            "to": to,
            "reason": reason,
            "at": int(datetime.utcnow().timestamp() * 1000),
        },
    )


def set_suggested_reply(
    db: Session,
    agent_id: str,
    conversation_id: UUID | str,
    *,
    text: Optional[str],
    kind: str = "text",
    reasoning: Optional[str] = None,
) -> Optional[ChatbotConversation]:
    """Set or clear the bot's suggested reply (Boss-IN mode).
    Pass text=None to clear (e.g. after boss approves or dismisses)."""
    payload = None
    if text is not None:
        payload = {"text": text, "kind": kind}
        if reasoning:
            payload["reasoning"] = reasoning
    return patch_conversation(
        db, agent_id, conversation_id,
        suggested_reply_json=payload,
        status="needs_review" if payload else "needs_reply",
    )


# ============================================================================
#  Messages — append + list
# ============================================================================

def list_messages(
    db: Session,
    agent_id: str,
    conversation_id: UUID | str,
    limit: int = 200,
) -> list[ChatbotMessage]:
    """All messages for a conversation, oldest-first. Caller pre-validates
    that the conversation belongs to agent_id (via get_conversation)."""
    conv = get_conversation(db, agent_id, conversation_id)
    if not conv:
        return []
    return (
        db.query(ChatbotMessage)
        .filter(ChatbotMessage.conversation_id == conv.id)
        .order_by(ChatbotMessage.at.asc())
        .limit(limit)
        .all()
    )


def append_message(
    db: Session,
    agent_id: str,
    conversation_id: UUID | str,
    *,
    author: str,
    kind: str,
    text: Optional[str] = None,
    voice_url: Optional[str] = None,
    voice_duration_sec: Optional[int] = None,
    voice_transcript: Optional[str] = None,
    confidence: Optional[float] = None,
    image_url: Optional[str] = None,
    image_caption: Optional[str] = None,
    image_width: Optional[int] = None,
    image_height: Optional[int] = None,
    file_url: Optional[str] = None,
    file_name: Optional[str] = None,
    file_size_bytes: Optional[int] = None,
    file_mime: Optional[str] = None,
    bot_meta: Optional[dict[str, Any]] = None,
    partial: bool = False,
    provider_message_id: Optional[str] = None,
    at: Optional[datetime] = None,
) -> Optional[ChatbotMessage]:
    """Append a message to a conversation + update conversation's preview
    and last_message_at. Idempotent on `provider_message_id` if provided
    (re-delivered webhooks don't duplicate the row)."""
    conv = get_conversation(db, agent_id, conversation_id)
    if not conv:
        return None
    # Idempotency check
    if provider_message_id:
        existing = (
            db.query(ChatbotMessage)
            .filter(
                ChatbotMessage.conversation_id == conv.id,
                ChatbotMessage.provider_message_id == provider_message_id,
            )
            .first()
        )
        if existing:
            return existing
    msg_at = at or datetime.utcnow()
    msg = ChatbotMessage(
        conversation_id=conv.id,
        author=author,
        kind=kind,
        at=msg_at,
        text=text,
        voice_url=voice_url,
        voice_duration_sec=voice_duration_sec,
        voice_transcript=voice_transcript,
        confidence=confidence,
        image_url=image_url,
        image_caption=image_caption,
        image_width=image_width,
        image_height=image_height,
        file_url=file_url,
        file_name=file_name,
        file_size_bytes=file_size_bytes,
        file_mime=file_mime,
        bot_meta_json=bot_meta,
        partial=partial,
        provider_message_id=provider_message_id,
    )
    db.add(msg)
    # Update parent conversation
    conv.last_message_at = msg_at
    conv.preview = _compute_preview(msg)
    if author == "customer":
        conv.unread_count = (conv.unread_count or 0) + 1
        if conv.status == "resolved":
            # Customer reached out again after resolution — reopen
            conv.status = "needs_reply"
            conv.ended_at = None
    db.commit()
    db.refresh(msg)
    return msg


def _compute_preview(msg: ChatbotMessage) -> str:
    """Short text shown in the conversation list. Channel-aware so voice
    and image previews aren't blank."""
    if msg.kind == "text" and msg.text:
        return msg.text[:120]
    if msg.kind == "voice":
        if msg.voice_transcript:
            return f"🎙️ {msg.voice_transcript[:100]}"
        return f"🎙️ Voice message ({msg.voice_duration_sec or 0}s)"
    if msg.kind == "image":
        if msg.image_caption:
            return f"📷 {msg.image_caption[:100]}"
        return "📷 Image"
    if msg.kind == "file":
        return f"📎 {msg.file_name or 'File'}"
    if msg.kind == "system":
        return (msg.text or "")[:120]
    return ""


# ============================================================================
#  Conversation actions — audit log entries
# ============================================================================

def list_actions(
    db: Session, agent_id: str, conversation_id: UUID | str
) -> list[ChatbotConversationAction]:
    conv = get_conversation(db, agent_id, conversation_id)
    if not conv:
        return []
    return (
        db.query(ChatbotConversationAction)
        .filter(ChatbotConversationAction.conversation_id == conv.id)
        .order_by(desc(ChatbotConversationAction.at))
        .all()
    )


def add_action(
    db: Session,
    agent_id: str,
    conversation_id: UUID | str,
    *,
    kind: str,
    description: str,
    ref_id: Optional[str] = None,
    created_by: Optional[UUID] = None,
) -> Optional[ChatbotConversationAction]:
    conv = get_conversation(db, agent_id, conversation_id)
    if not conv:
        return None
    row = ChatbotConversationAction(
        conversation_id=conv.id,
        kind=kind,
        description=description,
        ref_id=ref_id,
        created_by=created_by,
        at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ============================================================================
#  Daily report — aggregated summary for the dashboard card
# ============================================================================

def daily_report_summary(db: Session, agent_id: str) -> dict[str, Any]:
    """Last 24 hours summary. Mirrors the voice daily report shape."""
    since = datetime.utcnow() - timedelta(hours=24)
    convs = (
        db.query(ChatbotConversation)
        .filter(
            ChatbotConversation.agent_id == agent_id,
            ChatbotConversation.last_message_at >= since,
        )
        .all()
    )
    total = len(convs)
    handled_by_bot = sum(
        1 for c in convs if c.status in ("bot_handling", "resolved") and c.urgency != "high"
    )
    needs_review = sum(1 for c in convs if c.status == "needs_review")
    escalated = sum(1 for c in convs if c.status == "escalated")

    # Top topics — derived from action kinds aggregated over recent activity
    # (placeholder: count by kind from chatbot_conversation_actions)
    top_topics: list[dict[str, Any]] = []
    try:
        rows = (
            db.query(
                ChatbotConversationAction.kind,
                func.count(ChatbotConversationAction.id).label("count"),
            )
            .join(
                ChatbotConversation,
                ChatbotConversation.id == ChatbotConversationAction.conversation_id,
            )
            .filter(
                ChatbotConversation.agent_id == agent_id,
                ChatbotConversationAction.at >= since,
            )
            .group_by(ChatbotConversationAction.kind)
            .order_by(desc("count"))
            .limit(5)
            .all()
        )
        for kind, count in rows:
            top_topics.append({"topic": kind.replace("_", " ").title(), "count": int(count)})
    except Exception:
        pass

    # Average response time (customer → first bot/boss reply) — placeholder
    # full impl: subquery the first non-customer message after each customer message
    avg_response_sec = None

    return {
        "totalConversations": total,
        "handledByBot": handled_by_bot,
        "needsReview": needs_review,
        "escalated": escalated,
        "topTopics": top_topics,
        "averageResponseSec": avg_response_sec,
    }


# ============================================================================
#  Serializers — wire format matches packages/chatbot/src/inbox-ui/types.ts
# ============================================================================

def serialize_customer(c: ChatbotCustomer) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "name": c.name or "",
        "phone": c.phone,
        "email": getattr(c, "email", None),
        "tag": c.tag,
        "avatarUrl": c.avatar_url,
        "notes": c.notes,
        "tags": c.tags_json or [],
    }


def serialize_message(m: ChatbotMessage) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": str(m.id),
        "at": int(m.at.timestamp() * 1000) if m.at else 0,
        "author": m.author,
        "kind": m.kind,
        "text": m.text,
        "confidence": m.confidence,
        "partial": bool(m.partial),
    }
    if m.kind == "voice" and m.voice_url:
        payload["voice"] = {
            "url": m.voice_url,
            "durationSec": m.voice_duration_sec or 0,
            "transcript": m.voice_transcript,
        }
    if m.kind == "image" and m.image_url:
        payload["image"] = {
            "url": m.image_url,
            "caption": m.image_caption,
            "width": m.image_width,
            "height": m.image_height,
        }
    if m.kind == "file" and m.file_url:
        payload["file"] = {
            "url": m.file_url,
            "name": m.file_name or "",
            "mimeType": m.file_mime or "application/octet-stream",
            "sizeBytes": m.file_size_bytes or 0,
        }
    if m.bot_meta_json:
        payload["botMeta"] = m.bot_meta_json
    return payload


def serialize_action(a: ChatbotConversationAction) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "at": int(a.at.timestamp() * 1000) if a.at else 0,
        "kind": a.kind,
        "description": a.description,
        "refId": a.ref_id,
    }


# ============================================================================
#  Voice-call bridge — phone calls show up in the chatbot inbox too
# ============================================================================

def bridge_voice_call_to_inbox(
    db: Session,
    agent_id: str,
    voice_call_id: UUID | str,
) -> Optional[ChatbotConversation]:
    """When a voice call ends, mirror it into the chatbot inbox so the
    boss sees calls + Kakao messages in ONE unified view.

    Called from voice_pipeline._finalize_call AFTER the summary has been
    generated. Idempotent — re-calling for the same voice_call_id returns
    the existing conversation."""
    from db.models import VoiceCall, VoiceCallTurn

    call = (
        db.query(VoiceCall)
        .filter(VoiceCall.agent_id == agent_id, VoiceCall.id == voice_call_id)
        .first()
    )
    if not call:
        return None

    # Idempotency: if a conversation already exists for this voice_call_id, return it
    existing = (
        db.query(ChatbotConversation)
        .filter(
            ChatbotConversation.agent_id == agent_id,
            ChatbotConversation.voice_call_id == call.id,
        )
        .first()
    )
    if existing:
        return existing

    # Find or create the customer based on caller number
    customer = find_or_create_customer(
        db,
        agent_id,
        name=call.caller_name,
        phone=call.caller_number,
    )

    # Create a new conversation (channel="phone") linked to this call
    conv = ChatbotConversation(
        agent_id=agent_id,
        channel="phone",
        customer_id=customer.id,
        status="resolved" if call.status in ("completed", "missed") else (
            "escalated" if call.status == "escalated" else "needs_review"
        ),
        urgency=call.urgency,
        voice_call_id=call.id,
        started_at=call.started_at or datetime.utcnow(),
        ended_at=call.ended_at,
        escalation_json=call.escalation_json,
    )
    db.add(conv)
    db.flush()    # need conv.id

    # Mirror the transcript turns as chatbot messages
    turns = (
        db.query(VoiceCallTurn)
        .filter(VoiceCallTurn.call_id == call.id)
        .order_by(VoiceCallTurn.at.asc())
        .all()
    )
    last_text: Optional[str] = None
    for t in turns:
        if not t.text:
            continue
        author = "bot" if t.role == "bot" else "customer"
        msg = ChatbotMessage(
            conversation_id=conv.id,
            author=author,
            kind="text",          # transcript turns rendered as plain text bubbles
            text=t.text,
            at=t.at or datetime.utcnow(),
            confidence=t.confidence,
            bot_meta_json={"status": "auto", "source": "voice-pipeline"} if author == "bot" else None,
            provider_message_id=str(t.id),
        )
        db.add(msg)
        last_text = t.text

    # Add a system message capturing the call metadata + summary
    if call.summary:
        sys_msg = ChatbotMessage(
            conversation_id=conv.id,
            author="bot",
            kind="system",
            text=f"📞 {'Inbound' if call.direction == 'inbound' else 'Outbound'} call ({call.duration_sec or 0}s)\n{call.summary}",
            at=call.ended_at or datetime.utcnow(),
        )
        db.add(sys_msg)
        last_text = call.summary

    # Update conversation preview + last_message_at from the latest turn
    conv.last_message_at = call.ended_at or datetime.utcnow()
    if last_text:
        conv.preview = f"📞 {last_text[:100]}"

    # Audit log entry — call shows up as an action in the right panel
    action = ChatbotConversationAction(
        conversation_id=conv.id,
        at=call.ended_at or datetime.utcnow(),
        kind="call_received" if call.direction == "inbound" else "call_placed",
        description=(
            f"{'Inbound' if call.direction == 'inbound' else 'Outbound'} call "
            f"({call.duration_sec or 0}s)"
            + (f" — {call.summary}" if call.summary else "")
        ),
        ref_id=str(call.id),
    )
    db.add(action)

    db.commit()
    db.refresh(conv)
    return conv


def serialize_conversation(
    conv: ChatbotConversation,
    customer: Optional[ChatbotCustomer] = None,
    messages: Optional[list[ChatbotMessage]] = None,
    actions: Optional[list[ChatbotConversationAction]] = None,
) -> dict[str, Any]:
    return {
        "id": str(conv.id),
        "channel": conv.channel,
        "status": conv.status,
        "urgency": conv.urgency,
        "customer": serialize_customer(customer) if customer else None,
        "messages": [serialize_message(m) for m in (messages or [])],
        "preview": conv.preview or "",
        "lastMessageAt": int(conv.last_message_at.timestamp() * 1000) if conv.last_message_at else 0,
        "unreadCount": conv.unread_count or 0,
        "escalation": conv.escalation_json,
        "suggestedReply": conv.suggested_reply_json,
        "history": [serialize_action(a) for a in (actions or [])],
    }
