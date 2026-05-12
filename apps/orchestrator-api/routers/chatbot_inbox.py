"""
Chatbot Inbox API — REST + WebSocket surface for customer conversations.

Endpoints (all scoped per agent_id from the URL path):

  GET    /api/chatbot/{agent_id}/conversations?limit=N&status=X
  GET    /api/chatbot/{agent_id}/conversations/{conv_id}
  POST   /api/chatbot/{agent_id}/conversations/{conv_id}/read
  POST   /api/chatbot/{agent_id}/conversations/{conv_id}/resolve
  POST   /api/chatbot/{agent_id}/conversations/{conv_id}/escalate
  POST   /api/chatbot/{agent_id}/conversations/{conv_id}/take-over
  POST   /api/chatbot/{agent_id}/conversations/{conv_id}/reply
  POST   /api/chatbot/{agent_id}/conversations/{conv_id}/approve-draft
  POST   /api/chatbot/{agent_id}/conversations/{conv_id}/dismiss-draft
  GET    /api/chatbot/{agent_id}/daily-report
  GET    /api/chatbot/{agent_id}/mode
  POST   /api/chatbot/{agent_id}/mode  (set manual override)

  WS     /ws/chatbot/{agent_id}/conversations  (live updates)

Multi-tenant: path-scoped agent_id is the only source of truth. Cross-
tenant queries cannot happen because every service call filters by agent_id.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from db.base import get_db
from services import chatbot_conversation_service as conv_service
from services import chatbot_mode_detector
from services import chatbot_reply_service
from services.logger import log


router = APIRouter(prefix="/api/chatbot", tags=["chatbot"])


# ============================================================================
#  WebSocket broker — per-agent fan-out
# ============================================================================

class _ChatbotWsBroker:
    """In-memory per-agent broadcaster. Mirror of the voice WS broker.
    Swap for Redis pub/sub if/when we scale orchestrator horizontally."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, agent_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self._subscribers.setdefault(agent_id, set()).add(ws)

    async def unsubscribe(self, agent_id: str, ws: WebSocket) -> None:
        async with self._lock:
            subs = self._subscribers.get(agent_id)
            if subs:
                subs.discard(ws)
                if not subs:
                    self._subscribers.pop(agent_id, None)

    async def publish(self, agent_id: str, event: dict[str, Any]) -> None:
        subs = list(self._subscribers.get(agent_id, ()))
        if not subs:
            return
        payload = json.dumps(event, default=str)
        for ws in subs:
            try:
                await ws.send_text(payload)
            except Exception:
                await self.unsubscribe(agent_id, ws)

    def publish_sync(self, agent_id: str, event: dict[str, Any]) -> None:
        """Sync entry used by webhook handlers + service callbacks
        running outside an async context."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.publish(agent_id, event))
        except RuntimeError:
            # No event loop — drop. Clients hydrate via REST on reconnect.
            pass


_broker = _ChatbotWsBroker()


def get_broker() -> _ChatbotWsBroker:
    """Singleton accessor — webhook handlers + reply service use this
    to broadcast updates to subscribed dashboards."""
    return _broker


# ============================================================================
#  GET — reads
# ============================================================================

@router.get("/{agent_id}/conversations")
def list_conversations(
    agent_id: str,
    limit: int = 100,
    status: Optional[str] = None,
    channel: Optional[str] = None,
    db: Session = Depends(get_db),
):
    convs = conv_service.list_conversations(
        db, agent_id, status=status, channel=channel, limit=limit
    )
    out: list[dict[str, Any]] = []
    for c in convs:
        customer = conv_service.get_customer(db, agent_id, c.customer_id)
        # List view doesn't include full message thread — just metadata.
        out.append(conv_service.serialize_conversation(c, customer=customer))
    return out


@router.get("/{agent_id}/conversations/{conversation_id}")
def get_conversation(
    agent_id: str, conversation_id: UUID, db: Session = Depends(get_db)
):
    conv = conv_service.get_conversation(db, agent_id, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    customer = conv_service.get_customer(db, agent_id, conv.customer_id)
    messages = conv_service.list_messages(db, agent_id, conv.id)
    actions = conv_service.list_actions(db, agent_id, conv.id)
    return conv_service.serialize_conversation(
        conv, customer=customer, messages=messages, actions=actions
    )


@router.get("/{agent_id}/daily-report")
def daily_report(agent_id: str, db: Session = Depends(get_db)):
    return conv_service.daily_report_summary(db, agent_id)


@router.get("/{agent_id}/mode")
def get_mode(agent_id: str):
    mode, auto = chatbot_mode_detector.get_mode(agent_id)
    return {"mode": mode, "autoDetected": auto}


# ============================================================================
#  POST — write actions
# ============================================================================

class ReplyBody(BaseModel):
    text: str = Field(..., description="Reply text to send")
    kind: Optional[str] = Field("text", description="Message kind (text only for v1)")


class EscalateBody(BaseModel):
    reason: Optional[str] = "Marked urgent by operator"


class ApproveBody(BaseModel):
    edited_text: Optional[str] = Field(
        None, description="If set, send THIS instead of the original draft"
    )


class ModeBody(BaseModel):
    mode: str = Field(..., pattern="^(in|out)$")
    expires_in_hours: Optional[float] = None
    auto: Optional[bool] = Field(
        False, description="When true, clears the manual override (returns to auto-detect)"
    )


@router.post("/{agent_id}/conversations/{conversation_id}/read")
def mark_read(agent_id: str, conversation_id: UUID, db: Session = Depends(get_db)):
    conv = conv_service.mark_conversation_read(db, agent_id, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    _broadcast_conv(db, agent_id, conv)
    return {"ok": True}


@router.post("/{agent_id}/conversations/{conversation_id}/resolve")
def resolve(agent_id: str, conversation_id: UUID, db: Session = Depends(get_db)):
    conv = conv_service.resolve_conversation(db, agent_id, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    _broadcast_conv(db, agent_id, conv)
    return {"ok": True}


@router.post("/{agent_id}/conversations/{conversation_id}/escalate")
def escalate(
    agent_id: str,
    conversation_id: UUID,
    body: EscalateBody,
    db: Session = Depends(get_db),
):
    conv = conv_service.get_conversation(db, agent_id, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    customer = conv_service.get_customer(db, agent_id, conv.customer_id)
    customer_name = (customer.name if customer else None) or "Unknown"

    # Dispatch the escalation alert via the same channel registry as voice
    target = _dispatch_text_escalation(
        agent_id=agent_id,
        customer_name=customer_name,
        message=body.reason or "Operator escalation",
        conversation_id=str(conv.id),
    )
    updated = conv_service.escalate_conversation(
        db, agent_id, conv.id, to=target, reason=body.reason or "Manual escalation"
    )
    _broadcast_conv(db, agent_id, updated)
    return {"ok": True, "escalatedTo": target}


@router.post("/{agent_id}/conversations/{conversation_id}/take-over")
def take_over(agent_id: str, conversation_id: UUID, db: Session = Depends(get_db)):
    """Boss takes over a bot-handled conversation. Clears bot drafts +
    marks status=needs_reply (waiting for boss to type)."""
    conv = conv_service.patch_conversation(
        db, agent_id, conversation_id,
        status="needs_reply",
        suggested_reply_json=None,
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    _broadcast_conv(db, agent_id, conv)
    return {"ok": True}


@router.post("/{agent_id}/conversations/{conversation_id}/reply")
async def reply(
    agent_id: str,
    conversation_id: UUID,
    body: ReplyBody,
    db: Session = Depends(get_db),
):
    """Boss sends their own reply (manual, not from a draft)."""
    conv = conv_service.get_conversation(db, agent_id, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    # Persist as author="boss"
    msg = conv_service.append_message(
        db, agent_id, conv.id,
        author="boss",
        kind=body.kind or "text",
        text=body.text,
    )
    # Send via the appropriate channel client
    await _send_via_channel(db, agent_id, conv, body.text)
    # Clear suggested reply since boss took action
    conv_service.patch_conversation(
        db, agent_id, conv.id,
        suggested_reply_json=None,
        status="bot_handling",
        unread_count=0,
    )
    updated = conv_service.get_conversation(db, agent_id, conv.id)
    _broadcast_conv(db, agent_id, updated, with_message=msg)
    return {"ok": True, "messageId": str(msg.id) if msg else None}


@router.post("/{agent_id}/conversations/{conversation_id}/approve-draft")
async def approve_draft(
    agent_id: str,
    conversation_id: UUID,
    body: ApproveBody,
    db: Session = Depends(get_db),
):
    conv = conv_service.get_conversation(db, agent_id, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    async def _send(text, _agent, _conv):
        await _send_via_channel(db, agent_id, _conv, text)

    result = await chatbot_reply_service.handle_boss_approval(
        db, agent_id, conv, edited_text=body.edited_text, on_send=_send
    )
    updated = conv_service.get_conversation(db, agent_id, conv.id)
    _broadcast_conv(db, agent_id, updated)
    return {"ok": True, **result}


@router.post("/{agent_id}/conversations/{conversation_id}/dismiss-draft")
def dismiss_draft(
    agent_id: str, conversation_id: UUID, db: Session = Depends(get_db)
):
    conv = conv_service.get_conversation(db, agent_id, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    result = chatbot_reply_service.handle_boss_dismiss(db, agent_id, conv)
    updated = conv_service.get_conversation(db, agent_id, conv.id)
    _broadcast_conv(db, agent_id, updated)
    return {"ok": True, **result}


@router.post("/{agent_id}/mode")
def set_mode(agent_id: str, body: ModeBody):
    if body.auto:
        chatbot_mode_detector.clear_manual_mode(agent_id)
    else:
        chatbot_mode_detector.set_manual_mode(
            agent_id,
            body.mode,  # type: ignore[arg-type]
            expires_in_hours=body.expires_in_hours,
        )
    mode, auto = chatbot_mode_detector.get_mode(agent_id)
    # Notify subscribed dashboards
    _broker.publish_sync(
        agent_id, {"type": "mode.changed", "mode": mode, "autoDetected": auto}
    )
    return {"mode": mode, "autoDetected": auto}


# ============================================================================
#  Channel send dispatch — picks Kakao / phone / SMS / web client
# ============================================================================

async def _send_via_channel(
    db: Session,
    agent_id: str,
    conv,
    text: str,
) -> None:
    """Pick the right channel client based on conv.channel + send the text.
    Each client handles its own auth + provider API call."""
    if conv.channel == "kakao":
        try:
            from services import kakao_client
            await asyncio.to_thread(
                kakao_client.send_text,
                agent_id=agent_id,
                conversation_id=str(conv.id),
                text=text,
            )
        except Exception as e:
            log.warning(f"chatbot.send: kakao send failed: {e}")
    elif conv.channel == "phone":
        # Phone replies don't exist in real time mid-call — the voice pipeline
        # speaks the LLM output directly. This branch fires only on after-call
        # text follow-ups (which we don't do today).
        log.info("chatbot.send: phone channel — skipping (handled by voice pipeline)")
    elif conv.channel == "sms":
        log.info("chatbot.send: SMS channel not implemented yet")
    else:
        log.info(f"chatbot.send: unhandled channel {conv.channel}")


# ============================================================================
#  Broadcast helpers — push conversation updates to WS subscribers
# ============================================================================

def _broadcast_conv(db: Session, agent_id: str, conv, *, with_message=None) -> None:
    """Serialize + push the conversation to all subscribers of this agent."""
    if not conv:
        return
    customer = conv_service.get_customer(db, agent_id, conv.customer_id)
    payload = conv_service.serialize_conversation(conv, customer=customer)
    _broker.publish_sync(
        agent_id,
        {"type": "conversation.updated", "conversation": payload},
    )
    if with_message:
        _broker.publish_sync(
            agent_id,
            {
                "type": "message.added",
                "conversationId": str(conv.id),
                "message": conv_service.serialize_message(with_message),
            },
        )


def _dispatch_text_escalation(
    *,
    agent_id: str,
    customer_name: str,
    message: str,
    conversation_id: str,
) -> str:
    """Forward an escalation to Telegram/Slack via the existing channel registry.
    Returns a descriptor string for the response body + persisted record."""
    try:
        from services.voice_escalation import get_escalation_channel
        from services.telegram_service import send_message
    except Exception:
        return "escalation: registry unavailable"

    channel = get_escalation_channel(agent_id)
    kind = channel.get("kind", "none")
    if kind == "none":
        return "none configured"

    body = (
        f"🚨 URGENT CHATBOT — {agent_id.upper()}\n"
        f"Customer: {customer_name}\n"
        f"Reason: {message}\n"
        f"Open: /chatbot (conv {conversation_id[:8]})"
    )
    if kind == "telegram":
        chat_id = channel.get("chatId", "")
        if chat_id:
            ok = send_message(chat_id, body, parse_mode=None)
            return f"Telegram {chat_id}{' (sent)' if ok else ' (failed)'}"
    return f"channel kind={kind} (not yet implemented)"


# ============================================================================
#  WebSocket — live updates
# ============================================================================

ws_router = APIRouter()


@ws_router.websocket("/ws/chatbot/{agent_id}/conversations")
async def chatbot_inbox_ws(websocket: WebSocket, agent_id: str):
    """Subscribe to live conversation updates for this agent_id.

    Auth: optional shared-secret check via `?token=` query param matched
    against CHATBOT_WS_TOKEN env var (mirror of voice WS auth). When the
    env is unset, accept any connection for local dev.

    Event types pushed:
      - conversation.updated (status/urgency/escalation/suggestedReply changed)
      - message.added (new incoming or outgoing message)
      - mode.changed (Boss-IN ↔ Boss-OUT switched)
    """
    required_token = os.getenv("CHATBOT_WS_TOKEN", "")
    if required_token:
        supplied = websocket.query_params.get("token") or ""
        if not hmac.compare_digest(required_token, supplied):
            await websocket.close(code=4401, reason="unauthorized")
            return

    await websocket.accept()
    await _broker.subscribe(agent_id, websocket)
    log.info(
        f"chatbot.ws: client subscribed to {agent_id}",
        extra={"action": "chatbot.ws_subscribe", "agent_id": agent_id},
    )
    try:
        while True:
            # We don't expect client→server messages in v1; ignore receives
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await _broker.unsubscribe(agent_id, websocket)
        log.info(
            f"chatbot.ws: client unsubscribed from {agent_id}",
            extra={"action": "chatbot.ws_unsubscribe", "agent_id": agent_id},
        )
