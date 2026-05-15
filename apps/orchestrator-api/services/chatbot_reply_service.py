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

    Behavior by Boss mode:

    - Boss-IN (working hours): **boss is the primary operator.** Bot does
      NOT auto-draft anything. Customer message just sits as `needs_reply`
      so the boss can read it + reply manually (text or file/image). The
      bot watches + learns from boss's actual replies via the self-improve
      pipeline. If boss wants help, they click "AI suggestion" on the
      dashboard, which calls `generate_draft_on_demand()` separately.

    - Boss-OUT (off-hours/weekends): bot replies autonomously via `on_send`,
      escalates urgent items via Telegram.

    Args:
      conversation: the conversation the message belongs to
      incoming_text: the customer's text (already transcribed for voice msgs)
      customer: customer profile (for personalization)
      on_send: async callback that actually sends the bot's reply via the
               channel client (Kakao, SMS, phone TTS, etc.). Receives:
               on_send(reply_text: str, agent_id: str, conversation: ChatbotConversation)

    Returns a dict describing what happened:
      {"mode": "in"|"out", "action": "wait"|"sent"|"escalated", "reply": "..."}
    """
    mode, auto_detected = chatbot_mode_detector.get_mode(agent_id)
    is_urgent = chatbot_mode_detector.is_urgent_keyword(incoming_text)

    # Boss-IN: hands off. Just mark it as needing the boss's attention.
    if mode == "in":
        # If the message is urgent, ping the boss via Telegram so they
        # don't miss it even if they're heads-down on other work.
        if is_urgent:
            try:
                _dispatch_text_escalation(
                    agent_id=agent_id,
                    customer_name=customer.name or "Unknown",
                    incoming_text=incoming_text,
                    conversation_id=str(conversation.id),
                )
                conv_service.patch_conversation(
                    db, agent_id, conversation.id, urgency="high"
                )
            except Exception as e:
                log.warning(f"chatbot_reply: urgent ping failed (boss-in): {e}")
        # Make sure status is needs_reply (NOT needs_review — no draft pending)
        conv_service.patch_conversation(
            db, agent_id, conversation.id,
            status="needs_reply",
            suggested_reply_json=None,    # clear any stale draft
        )
        log.info(
            f"chatbot_reply: waiting for boss (mode=in, urgent={is_urgent}) "
            f"for conv {conversation.id}",
            extra={"action": "chatbot.boss_in_waiting"},
        )
        return {"mode": "in", "action": "wait", "reply": ""}

    # Boss-OUT path — bot is the operator
    reply_text, reasoning = await _generate_reply(
        agent_id=agent_id,
        incoming_text=incoming_text,
        customer=customer,
        conversation=conversation,
    )

    # Did the customer message in *voice*? If so, reply in voice too (when
    # configured). We detect by looking at the latest customer message kind.
    customer_used_voice = _last_customer_msg_was_voice(db, agent_id, conversation.id)
    if not reply_text:
        log.warning(
            f"chatbot_reply: empty reply for conv {conversation.id}",
            extra={"action": "chatbot.reply_empty"},
        )
        reply_text = "잠시 후 다시 답변드리겠습니다."
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

    # Autonomous voice reply — if the customer used voice (and voice
    # replies aren't disabled), generate TTS of the bot's text reply and
    # send it as an audio attachment alongside the text. Best-effort:
    # any failure falls back silently to text-only.
    voice_reply_sent = None
    if customer_used_voice and _voice_replies_enabled(agent_id):
        try:
            from services import chatbot_voice_reply
            audio_url = await chatbot_voice_reply.synthesize_and_upload(
                agent_id=agent_id,
                conversation_id=str(conversation.id),
                text=reply_text,
            )
            if audio_url:
                # Send via channel client
                if conversation.channel == "kakao":
                    try:
                        from services import kakao_client
                        cust_obj = conv_service.get_customer(db, agent_id, conversation.customer_id)
                        receiver = cust_obj.kakao_user_id if cust_obj else None
                        await asyncio.to_thread(
                            kakao_client.send_voice_message,
                            agent_id=agent_id,
                            conversation_id=str(conversation.id),
                            audio_url=audio_url,
                            duration_sec=20,
                            receiver_uuid=receiver,
                        )
                    except Exception as e:
                        log.warning(f"chatbot_reply: voice send via kakao failed: {e}")
                # Persist the voice message row regardless of channel result
                conv_service.append_message(
                    db, agent_id, conversation.id,
                    author="bot",
                    kind="voice",
                    voice_url=audio_url,
                    voice_transcript=reply_text,
                    bot_meta={"status": "auto-voice"},
                )
                voice_reply_sent = audio_url
        except Exception as e:
            log.warning(
                f"chatbot_reply: voice reply failed: {e}",
                extra={"action": "chatbot.voice_reply_failed"},
            )

    # Autonomous attachment — if the customer's message matches a keyword
    # in the agent's asset library (floor plans, contract templates, etc.),
    # the bot sends the file too. Best-effort; never blocks the text reply.
    attachment_sent = None
    try:
        from services import chatbot_attachment_dispatcher
        asset = chatbot_attachment_dispatcher.find_relevant_attachment(
            agent_id, incoming_text, db=db
        )
        if asset:
            await chatbot_attachment_dispatcher.dispatch_autonomous_attachment(
                db=db,
                agent_id=agent_id,
                conversation=conversation,
                asset=asset,
            )
            attachment_sent = {"asset_id": str(asset.id), "label": asset.label}
    except Exception as e:
        log.warning(
            f"chatbot_reply: auto-attachment failed: {e}",
            extra={"action": "chatbot.auto_attachment_failed"},
        )

    # Promote the conversation back to "bot_handling" — bot is actively engaged
    new_status = "escalated" if is_urgent else "bot_handling"
    conv_service.patch_conversation(
        db, agent_id, conversation.id, status=new_status, suggested_reply_json=None
    )

    log.info(
        f"chatbot_reply: sent (mode=out, urgent={is_urgent}, "
        f"attached={attachment_sent is not None}) for conv {conversation.id}",
        extra={"action": "chatbot.reply_sent"},
    )
    return {
        "mode": "out",
        "action": "escalated" if is_urgent else "sent",
        "reply": reply_text,
        "attachment": attachment_sent,
        "voice_reply": voice_reply_sent,
    }


async def generate_draft_on_demand(
    db: Session,
    agent_id: str,
    conversation: ChatbotConversation,
    *,
    customer: ChatbotCustomer,
    persist: bool = False,
) -> dict[str, Any]:
    """Generate a reply suggestion when the boss explicitly asks for one.

    Triggered by the dashboard's "AI suggestion" button. Looks at the
    latest customer message in the conversation + the agent's knowledge
    base, returns draft text the boss can use as a starting point.

    Args:
      persist: if True, also save the draft into conversation.suggested_reply_json
               (so it appears in the AI suggestion panel until boss approves/dismisses).
               If False (default), the draft is returned only — boss is in
               full control of whether to use it.

    Returns: {"text": str, "reasoning": str | None, "ok": bool}
    """
    # Find the latest customer message to use as the "what to reply to"
    msgs = conv_service.list_messages(db, agent_id, conversation.id, limit=20)
    latest_customer = next(
        (m for m in reversed(msgs) if m.author == "customer"), None
    )
    incoming_text = ""
    if latest_customer:
        incoming_text = latest_customer.text or latest_customer.voice_transcript or ""
    if not incoming_text:
        return {"text": "", "reasoning": "no customer message to respond to", "ok": False}

    reply_text, reasoning = await _generate_reply(
        agent_id=agent_id,
        incoming_text=incoming_text,
        customer=customer,
        conversation=conversation,
    )
    if not reply_text:
        return {"text": "", "reasoning": "LLM returned empty reply", "ok": False}

    if persist:
        conv_service.set_suggested_reply(
            db,
            agent_id,
            conversation.id,
            text=reply_text,
            kind="text",
            reasoning=reasoning,
        )

    return {"text": reply_text, "reasoning": reasoning, "ok": True}


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
    (reply_text, reasoning).

    Style mimicry: pulls the boss style hint from chatbot_boss_observer
    (built from past boss replies) and prepends it to the user message so
    the LLM matches the boss's tone + length preferences."""
    try:
        from db.base import SessionLocal
        from services.chatbot_talk import handle_talk, _triple_h_realty_knowledge_base
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

        # Pull learned style hint from observer (formal/casual/length/etc.)
        style_hint = ""
        try:
            from services import chatbot_boss_observer
            style_hint = chatbot_boss_observer.build_style_hint(db2, agent_id)
        except Exception:
            pass

        # If we have a style hint, prefix the user message with it so the LLM
        # sees it as context. The chatbot_talk pipeline forwards intent + KB
        # but doesn't expose a separate "style" slot — this is the cleanest
        # injection point without modifying the handle_talk signature.
        query_text = incoming_text
        if style_hint:
            query_text = f"{style_hint}\n\n[고객 메시지]\n{incoming_text}"

        # Customer-facing chatbot context: pass the Triple H real estate
        # knowledge base directly so the LLM grounds answers in property
        # data, not the VIP boss-platform info (which is irrelevant to
        # KakaoTalk customers).
        realty_kb = _triple_h_realty_knowledge_base()
        result = await asyncio.to_thread(
            handle_talk,
            db2,
            query_text,
            "ko",     # KR-first; the LLM will switch if user spoke English
            agent_id,
            intents=None,           # backend has agent's intents registered
            knowledge_base=realty_kb,
            history=history,
            current_path="/chatbot",
        )
        reply = result.get("reply", "") if isinstance(result, dict) else ""
        source = result.get("source") if isinstance(result, dict) else None
        intent = result.get("intent") if isinstance(result, dict) else None
        reasoning = None
        if source and intent:
            reasoning = f"source={source}, intent={intent}"
            if style_hint:
                reasoning += " + boss-style"
        return reply.strip(), reasoning
    except Exception as e:
        log.warning(f"chatbot_reply: generate failed: {e}")
        return "", None
    finally:
        db2.close()


def _last_customer_msg_was_voice(
    db: Session, agent_id: str, conversation_id
) -> bool:
    """True if the most recent customer-authored message in this conversation
    arrived as voice (kind='voice'). Used to decide whether to mirror the
    bot's reply as TTS audio."""
    try:
        msgs = conv_service.list_messages(db, agent_id, conversation_id, limit=20)
        for m in reversed(msgs):
            if m.author != "customer":
                continue
            return (m.kind or "").lower() == "voice"
    except Exception:
        pass
    return False


def _voice_replies_enabled(agent_id: str) -> bool:
    """Voice replies are opt-in via env. Default off so dev environments
    don't burn TTS credits unintentionally. Set CHATBOT_VOICE_REPLIES=1
    to enable per orchestrator (per-agent toggle is future work)."""
    import os as _os
    return _os.getenv("CHATBOT_VOICE_REPLIES", "0") == "1"


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
