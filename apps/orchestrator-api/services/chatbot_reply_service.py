"""
chatbot_reply_service — generates the bot's reply for an incoming customer message.

Two paths based on Boss mode:

- Boss-IN (working hours, boss is reviewing):
    bot generates DRAFT → persisted to conversation.suggested_reply_json
    → boss reviews on dashboard → approves / dismisses / edits & sends
    → if boss doesn't act within a fallback window, the dashboard can
      auto-promote drafts to sent (optional; off by default).

- Boss-OUT (off-hours, autonomous):
    bot generates final reply → sent immediately via the channel client
    → message appended to conversation as author="bot", botMeta.status="auto"
    → if urgency=high, escalate via Telegram/Slack/email

The LLM call reuses the existing chatbot text pipeline (services/chatbot_talk.py)
so the agent's knowledge base + intent system feeds into voice + chat alike.
The "brain" stays single; the surface changes.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from db.models import ChatbotConversation, ChatbotCustomer, ChatbotMessage
from services import chatbot_conversation_service as conv_service
from services import chatbot_mode_detector
from services.logger import log


# ============================================================================
#  Public entry point — called from webhook handler / phone bridge
# ============================================================================

async def handle_incoming_message(
    db: Session,
    agent_id: str,
    conversation: ChatbotConversation,
    incoming_text: str,
    *,
    customer: ChatbotCustomer,
    on_send: Optional[callable] = None,        # type: ignore[type-arg]
) -> dict[str, Any]:
    """Process a customer's incoming message and either draft or send a reply.

    Args:
      conversation: the conversation the message belongs to
      incoming_text: the customer's text (already transcribed for voice msgs)
      customer: customer profile (for personalization)
      on_send: async callback that actually sends the bot's reply via the
               channel client (Kakao, SMS, phone TTS, etc.). Receives:
               on_send(reply_text: str, agent_id: str, conversation: ChatbotConversation)

    Returns a dict describing what happened:
      {"mode": "in"|"out", "action": "draft"|"sent"|"escalated", "reply": "..."}
    """
    mode, auto_detected = chatbot_mode_detector.get_mode(agent_id)
    is_urgent = chatbot_mode_detector.is_urgent_keyword(incoming_text)

    # Step 1: generate the reply text via the existing chatbot brain
    reply_text, reasoning = await _generate_reply(
        agent_id=agent_id,
        incoming_text=incoming_text,
        customer=customer,
        conversation=conversation,
    )

    if not reply_text:
        log.warning(
            f"chatbot_reply: empty reply for conv {conversation.id}",
            extra={"action": "chatbot.reply_empty"},
        )
        reply_text = "잠시 후 다시 답변드리겠습니다." if mode == "out" else ""

    # Step 2: Boss-IN → draft only. Boss-OUT → send + maybe escalate.
    if mode == "in":
        conv_service.set_suggested_reply(
            db,
            agent_id,
            conversation.id,
            text=reply_text,
            kind="text",
            reasoning=reasoning,
        )
        log.info(
            f"chatbot_reply: drafted (mode=in) for conv {conversation.id}",
            extra={"action": "chatbot.draft_created"},
        )
        return {"mode": "in", "action": "draft", "reply": reply_text}

    # Boss-OUT path
    if is_urgent:
        conv_service.escalate_conversation(
            db,
            agent_id,
            conversation.id,
            to=_resolve_escalation_target(agent_id),
            reason=f"Urgent keyword detected in customer message: '{incoming_text[:80]}'",
        )
        try:
            from services import voice_escalation
            # Reuse voice_escalation's dispatcher pattern
            _dispatch_text_escalation(
                agent_id=agent_id,
                customer_name=customer.name or "Unknown",
                incoming_text=incoming_text,
                conversation_id=str(conversation.id),
            )
        except Exception as e:
            log.warning(f"chatbot_reply: escalation dispatch failed: {e}")

    # Send the bot's reply via the channel client
    if on_send:
        try:
            result = on_send(reply_text, agent_id, conversation)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            log.warning(
                f"chatbot_reply: on_send failed: {e}",
                extra={"action": "chatbot.send_failed"},
            )

    # Append the sent reply to the conversation history
    conv_service.append_message(
        db,
        agent_id,
        conversation.id,
        author="bot",
        kind="text",
        text=reply_text,
        bot_meta={"status": "auto", "reasoning": reasoning},
    )

    # Promote the conversation back to "bot_handling" — bot is actively engaged
    new_status = "escalated" if is_urgent else "bot_handling"
    conv_service.patch_conversation(
        db, agent_id, conversation.id, status=new_status, suggested_reply_json=None
    )

    log.info(
        f"chatbot_reply: sent (mode=out, urgent={is_urgent}) for conv {conversation.id}",
        extra={"action": "chatbot.reply_sent"},
    )
    return {
        "mode": "out",
        "action": "escalated" if is_urgent else "sent",
        "reply": reply_text,
    }


async def handle_boss_approval(
    db: Session,
    agent_id: str,
    conversation: ChatbotConversation,
    *,
    edited_text: Optional[str] = None,
    on_send: Optional[callable] = None,        # type: ignore[type-arg]
) -> dict[str, Any]:
    """Boss approved the suggested reply (Boss-IN mode). Send it as-is
    or with edits, then clear the draft state.

    Args:
      edited_text: if provided, sends THIS instead of the original draft
                   (boss edited the AI's suggestion before approving)
    """
    draft = conversation.suggested_reply_json or {}
    text = edited_text or draft.get("text")
    if not text:
        return {"action": "noop", "reason": "no draft text"}

    # Send via channel
    if on_send:
        try:
            result = on_send(text, agent_id, conversation)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            log.warning(f"chatbot_reply: on_send (approve) failed: {e}")

    # Persist as bot message with status="approved" — distinguishes from autonomous sends
    bot_meta = {
        "status": "approved" if not edited_text else "approved-edited",
    }
    if draft.get("reasoning"):
        bot_meta["reasoning"] = draft["reasoning"]
    conv_service.append_message(
        db,
        agent_id,
        conversation.id,
        author="bot",
        kind="text",
        text=text,
        bot_meta=bot_meta,
    )

    # Clear draft, promote conversation
    conv_service.patch_conversation(
        db, agent_id, conversation.id,
        status="bot_handling",
        suggested_reply_json=None,
    )
    return {"action": "sent", "reply": text}


def handle_boss_dismiss(
    db: Session,
    agent_id: str,
    conversation: ChatbotConversation,
) -> dict[str, Any]:
    """Boss dismissed the draft — they'll write their own reply manually.
    Clear the draft and keep the conversation in needs_reply state."""
    conv_service.patch_conversation(
        db, agent_id, conversation.id,
        suggested_reply_json=None,
        status="needs_reply",
    )
    return {"action": "dismissed"}


# ============================================================================
#  Internals
# ============================================================================

async def _generate_reply(
    *,
    agent_id: str,
    incoming_text: str,
    customer: ChatbotCustomer,
    conversation: ChatbotConversation,
) -> tuple[str, Optional[str]]:
    """Call the existing chatbot brain (services.chatbot_talk.handle_talk)
    with this customer's message + agent's knowledge base. Returns
    (reply_text, reasoning)."""
    try:
        from db.base import SessionLocal
        from services.chatbot_talk import handle_talk
    except Exception as e:
        log.warning(f"chatbot_reply: chatbot_talk import failed: {e}")
        return "", None

    # Get a fresh session for the sync handle_talk call
    db2 = SessionLocal()
    try:
        # Build conversation history from recent messages (for pronoun resolution)
        recent_msgs = conv_service.list_messages(db2, agent_id, conversation.id, limit=10)
        history = [
            {
                "role": "user" if m.author == "customer" else "assistant",
                "text": m.text or m.voice_transcript or "",
            }
            for m in recent_msgs
            if (m.text or m.voice_transcript)
        ]

        result = await asyncio.to_thread(
            handle_talk,
            db2,
            incoming_text,
            "ko",     # KR-first; the LLM will switch if user spoke English
            agent_id,
            intents=None,           # backend has agent's intents registered
            knowledge_base=None,    # backend has agent's KB registered
            history=history,
            current_path="/chatbot",
        )
        reply = result.get("reply", "") if isinstance(result, dict) else ""
        source = result.get("source") if isinstance(result, dict) else None
        intent = result.get("intent") if isinstance(result, dict) else None
        reasoning = None
        if source and intent:
            reasoning = f"source={source}, intent={intent}"
        return reply.strip(), reasoning
    except Exception as e:
        log.warning(f"chatbot_reply: generate failed: {e}")
        return "", None
    finally:
        db2.close()


def _resolve_escalation_target(agent_id: str) -> str:
    """Reuse the voice escalation channel registry — same channel handles
    text + voice urgency events for a given agent."""
    try:
        from services.voice_escalation import get_escalation_channel
        channel = get_escalation_channel(agent_id)
        kind = channel.get("kind", "none")
        if kind == "telegram":
            return f"Telegram → {channel.get('chatId', 'unknown')}"
        if kind == "slack":
            return f"Slack → {channel.get('channel', 'unknown')}"
        if kind == "email":
            return f"Email → {channel.get('to', 'unknown')}"
        return f"Channel: {kind}"
    except Exception:
        return "none configured"


def _dispatch_text_escalation(
    *,
    agent_id: str,
    customer_name: str,
    incoming_text: str,
    conversation_id: str,
) -> None:
    """Best-effort: send a text escalation via the agent's channel.
    Uses the voice escalation registry's Telegram/Slack dispatchers."""
    try:
        from services.voice_escalation import get_escalation_channel
        from services.telegram_service import send_message
    except Exception:
        return

    channel = get_escalation_channel(agent_id)
    kind = channel.get("kind", "none")
    body = (
        f"🚨 URGENT CHATBOT MESSAGE — {agent_id.upper()}\n"
        f"Customer: {customer_name}\n"
        f"Message: {incoming_text[:200]}\n"
        f"\nReview at: /chatbot (conv {conversation_id[:8]})"
    )
    if kind == "telegram":
        chat_id = channel.get("chatId", "")
        if chat_id:
            send_message(chat_id, body, parse_mode=None)
