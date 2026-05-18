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

_FAST_REPLY_CACHE: dict[str, str] = {
    # Greetings (Korean + English) — instant replies, no LLM call.
    # Critical: Kakao i 오픈빌더's effective skill timeout is ~3 seconds on
    # our Render free-tier deployment. LLM (Haiku) takes 2-5 seconds, so
    # cached replies are the only way to guarantee delivery for common
    # conversational opens. The LLM path runs as a slower fallback for
    # KB-specific questions (property numbers, prices, etc.).
    #
    # ADD MORE ENTRIES here whenever you spot a frequent customer message
    # that doesn't get a reply. Keys are normalized (lowercase, trim).

    # === Greetings ===
    "안녕하세요": "안녕하세요! 트리플에이치 부동산 챗봇입니다. 어떤 매물 또는 상담이 필요하신가요? 🏠",
    "안녕": "안녕하세요! 트리플에이치 부동산 챗봇입니다. 무엇을 도와드릴까요?",
    "여보세요": "네, 안녕하세요! 트리플에이치 부동산입니다. 무엇을 도와드릴까요?",
    "hi": "안녕하세요! Triple H Real Estate Chatbot입니다. 어떻게 도와드릴까요? / How can I help you?",
    "hello": "안녕하세요! Triple H Real Estate Chatbot입니다. 어떻게 도와드릴까요? / How can I help you?",
    "hey": "안녕하세요! Triple H 부동산 챗봇입니다. 무엇을 도와드릴까요?",

    # === Test messages ===
    "테스트": "테스트 메시지를 잘 받았습니다. 챗봇이 정상 작동 중입니다. ✅",
    "test": "Test message received. The chatbot is working correctly. ✅",
    "test123": "테스트 메시지를 잘 받았습니다. 챗봇이 정상 작동 중입니다. ✅",
    "ping": "pong ✅ 챗봇 응답 중",

    # === Thanks / closing ===
    "감사합니다": "별말씀을요! 추가로 궁금한 점이 있으시면 언제든 말씀해 주세요. 😊",
    "감사": "별말씀을요! 추가로 궁금한 점이 있으시면 언제든 말씀해 주세요.",
    "고맙습니다": "별말씀을요! 추가로 궁금한 점이 있으시면 언제든 말씀해 주세요.",
    "고마워요": "별말씀을요! 도움이 필요하시면 언제든 다시 연락주세요. 😊",
    "thank you": "You're welcome! Let me know if there's anything else I can help with. 😊",
    "thanks": "You're welcome! Let me know if there's anything else I can help with.",
    "안녕히 계세요": "감사합니다. 좋은 하루 보내세요! 다음에 또 문의해 주세요. 👋",
    "bye": "Goodbye! Feel free to reach out anytime. Have a great day! 👋",

    # === Common casual questions ===
    "잘 지내고 있어요?": "네, 잘 지내고 있습니다! 어떤 부동산 상담이 필요하신가요? 매물 문의나 임대/매매 관련 질문 모두 환영합니다. 🏠",
    "잘 지내세요?": "네, 감사합니다! 어떤 매물 또는 상담이 필요하신가요?",
    "어디 있어요?": "트리플에이치 부동산은 서울 강남구·서초구·성동구·송파구 지역의 매물을 전문으로 관리하고 있습니다. 특정 지역의 매물을 찾으시나요?",
    "어디예요?": "트리플에이치 부동산은 서울 강남·서초·성동·송파 지역을 전문으로 합니다. 어느 지역 매물을 찾으시나요?",
    "어디인가요?": "트리플에이치 부동산은 서울 강남·서초·성동·송파 지역의 매물을 전문으로 합니다.",
    "위치": "트리플에이치 부동산은 서울 강남구·서초구·성동구·송파구 지역의 매물을 전문으로 합니다.",

    # === Language questions ===
    "can you speak in english?": "Yes, I can respond in English! Triple H Real Estate covers properties in Gangnam, Seocho, Seongdong, and Songpa districts. What kind of property are you looking for?",
    "can you speak english?": "Yes! Triple H Real Estate covers properties in Gangnam, Seocho, Seongdong, and Songpa. What can I help you find?",
    "do you speak english?": "Yes! Triple H Real Estate covers properties in Gangnam, Seocho, Seongdong, and Songpa. What can I help you find?",
    "영어 가능": "네, 영어로도 답변드릴 수 있습니다. / Yes, I can respond in English. 어떤 매물을 찾고 계신가요?",
    "영어 가능해요?": "네, 영어로도 답변드릴 수 있습니다. / Yes, I can respond in English. 어떤 매물을 찾고 계신가요?",
    "한국어": "네, 한국어로 답변드리겠습니다! 어떤 매물 또는 상담이 필요하신가요? 🏠",
    "korean": "네, 한국어로 답변드리겠습니다! Yes, I'll respond in Korean. 어떤 매물 또는 상담이 필요하신가요?",

    # === Real estate quick intent ===
    "매물": "어떤 매물을 찾고 계신가요? 임대(월세/전세) 또는 매매 중 어떤 거래를 원하시는지, 그리고 희망 지역과 예산을 알려주시면 더 자세히 안내해 드릴 수 있습니다.",
    "월세": "어느 지역, 어느 평형대를 찾고 계신가요? 강남·서초·성동·송파 지역의 다양한 월세 매물을 보유하고 있습니다.",
    "전세": "전세 매물에 대해 문의해 주셔서 감사합니다. 어느 지역, 어떤 조건을 찾고 계신가요? 담당자가 자세한 매물 정보를 안내해 드리겠습니다.",
    "매매": "매매 문의 감사합니다. 어느 지역, 어떤 유형(아파트/오피스텔)의 매물을 찾고 계신가요?",
    "방문": "방문 예약 가능합니다! 보고 싶으신 매물 번호(예: B-201호)와 가능한 날짜·시간을 알려주시면 담당자가 예약을 도와드리겠습니다. 평일 10:00-18:00, 토요일 10:00-15:00 가능합니다.",
    "방문 예약": "방문 예약 가능합니다! 보고 싶으신 매물 번호와 가능한 날짜·시간을 알려주시면 담당자가 예약을 도와드리겠습니다.",
    "상담": "네, 부동산 상담을 도와드리겠습니다. 어떤 상담이 필요하신가요? 매물 문의, 임대/매매 조건, 계약 관련 등 모두 가능합니다.",

    # === Out-of-scope / off-topic ===
    "?": "어떤 도움이 필요하신가요? 매물 문의, 임대/매매 상담, 방문 예약 등 부동산 관련 질문에 답변드릴 수 있습니다.",
    "??": "어떤 도움이 필요하신가요? 매물 문의, 임대/매매 상담, 방문 예약 등 부동산 관련 질문에 답변드릴 수 있습니다.",
    "ㅋㅋ": "😊 어떤 부동산 관련 문의가 있으신가요?",
    "ㅎㅎ": "😊 어떤 부동산 관련 문의가 있으신가요?",
}


# Substring trigger keys — match anywhere in SHORT (≤10 chars) customer
# messages only. For one-word queries like "월세" or "매물", these give
# instant useful answers. For longer natural sentences ("월세 100만원에
# 강남쪽 매물 있어요?"), the substring cache is BYPASSED so the LLM can
# handle them with the actual KB.
_FAST_REPLY_SUBSTRING_KEYS = (
    "감사", "고맙", "thanks", "thank you",
    "안녕히 계세요", "bye",
)


# Topic-based keyword patterns — fire ONLY for vague meta-questions the
# LLM can't answer better with the knowledge base. Specific questions
# (unit numbers, prices, deposits, contracts, areas, pets) are DELIBERATELY
# NOT cached so the LLM can answer them with actual KB data and warm tone.
#
# Removed in 2026-05-18 rewrite: 호/a-/b-, 얼마/price, 보증금/deposit,
# 계약/contract, 강남/서초/송파/성동, 외국인, 반려동물, 아파트/오피스 —
# these all have concrete answers in the KB and were intercepting LLM.
_TOPIC_PATTERNS: list[tuple[tuple[str, ...], str]] = [
    # Empty — all entries removed. The LLM + KB now handle every cache-miss
    # question. A bare question mark from a customer still gets a useful
    # reply via the conversational fallback if LLM times out.
]


# Conversational holding messages — fire when LLM is too slow for Kakao's
# 3-second skill timeout. Picked at random so the bot doesn't feel like a
# robot repeating the same canned line. Each one keeps the dialogue alive
# by asking a clarifying question, so customer engagement continues even
# though we couldn't answer the underlying question yet.
_CONVERSATIONAL_FALLBACKS = [
    "음, 좋은 질문이네요! 조금 더 자세히 알려주실 수 있을까요? 어떤 지역(강남·서초·성동·송파) 매물을 찾으세요?",
    "네, 도와드릴게요! 임대(월세/전세)와 매매 중 어떤 거래를 원하세요? 그리고 희망 평형대(예: 10평/20평/30평 이상)를 알려주시면 추천드리겠습니다.",
    "문의 감사합니다 🙂 정확히 어떤 정보가 필요하실까요? 예를 들어 '강남 월세 30평대 매물' 처럼 알려주시면 바로 안내드릴 수 있어요.",
    "좋아요, 도와드리겠습니다! 혹시 특정 매물 번호(예: B-201호)가 있나요? 아니면 새로 찾고 계신가요? 희망 조건을 알려주세요.",
    "네, 트리플에이치 부동산입니다 🏠 어떤 매물 또는 상담이 필요하신가요? 예산·지역·평형 중 알려주실 수 있는 정보가 있으면 더 정확하게 답변드릴 수 있어요.",
    "조금만 더 알려주실 수 있을까요? 임대인지 매매인지, 그리고 어느 지역을 보고 계신지 말씀해 주시면 맞춤 매물을 빠르게 찾아드릴게요.",
]


def _pick_conversational_fallback() -> str:
    """Pick a random conversational holding message. Varies across messages
    so the bot doesn't feel like it's stuck on one canned line."""
    import random
    return random.choice(_CONVERSATIONAL_FALLBACKS)


# Backward-compat alias (referenced in tests / older call sites)
_GENERIC_HELPFUL_FALLBACK = _CONVERSATIONAL_FALLBACKS[0]


def _check_topic_pattern(text: str) -> Optional[str]:
    """Match the message against topic-based keyword patterns. Returns
    a helpful topic-specific reply if any keyword matches, else None."""
    if not text:
        return None
    lower = text.lower()
    for keywords, reply in _TOPIC_PATTERNS:
        for kw in keywords:
            if kw in lower:
                return reply
    return None


def _check_fast_reply(text: str) -> Optional[str]:
    """Hybrid fast-path lookup. Returns a template reply IF the message
    matches a specific known pattern (greetings, FAQs, topic keywords).
    Returns None for novel/complex questions, signalling the caller to
    consult the LLM for a smarter answer.

    Three stages (local, ~50ms total):
      1. Exact match (greetings + common phrases)
      2. Substring trigger (keyword anywhere in short message)
      3. Topic-pattern matching (real-estate FAQs)

    Cache miss → returns None → caller falls through to LLM (smart path).
    If LLM also times out, caller uses `_GENERIC_HELPFUL_FALLBACK` as
    the last-resort reply (so bot still always responds)."""
    if not text:
        return None
    normalized = text.strip().lower()
    normalized = normalized.strip('"').strip("'").strip("`")
    normalized_full = normalized.rstrip("!?.~ ")
    if not normalized_full:
        return None
    # Stage 1: exact match — greetings, thanks, tests, fillers only.
    hit = _FAST_REPLY_CACHE.get(normalized_full)
    if hit:
        return hit
    # Stage 2: substring trigger — only for very SHORT messages (≤10 chars).
    # Anything longer is a real question; let LLM handle it.
    if len(normalized_full) <= 10:
        for key in _FAST_REPLY_SUBSTRING_KEYS:
            if key in normalized_full:
                cached = _FAST_REPLY_CACHE.get(key)
                if cached:
                    return cached
    # Stage 3: topic-pattern matching (currently empty — KB+LLM handles all).
    topic = _check_topic_pattern(text)
    if topic:
        return topic
    # Cache miss — signal caller to try LLM (smart path)
    return None


async def _generate_reply(
    *,
    agent_id: str,
    incoming_text: str,
    customer: ChatbotCustomer,
    conversation: ChatbotConversation,
) -> tuple[str, Optional[str]]:
    """Hybrid reply generation. Three layers, descending speed but ascending
    intelligence:

      Layer 1 (~50ms): Template cache — greetings, FAQs, topic patterns.
      Layer 2 (~1-3s): LLM (OpenAI gpt-4o-mini) for novel/complex questions.
      Layer 3 (instant): Generic helpful template if LLM times out / errors.

    The bot ALWAYS replies. Fast cases use templates; complex cases get
    LLM smartness within Kakao's timeout; if LLM is too slow, a friendly
    generic reply still goes out (better than no reply at all)."""

    # Layer 1: Try template cache first (greetings, FAQs, common topics)
    cached = _check_fast_reply(incoming_text)
    if cached:
        return cached, "template"

    # Layer 2: Cache miss — try the LLM for a smarter answer
    try:
        from db.base import SessionLocal
        from services.chatbot_talk import handle_talk, _triple_h_realty_knowledge_base
    except Exception as e:
        log.warning(f"chatbot_reply: chatbot_talk import failed: {e}")
        # Layer 3 fallback: import failure means LLM is broken — use conversational template
        return _pick_conversational_fallback(), "template-fallback-import-error"

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

        # Hard timeout: Kakao's effective skill timeout is ~3 seconds on
        # Render free tier (network latency + processing). Cap LLM at
        # 1.5s so we have a full second of buffer to wrap and ship the
        # response within Kakao's window. If the LLM is slower than 1.5s,
        # a conversational holding-message fires below — customer still
        # gets a useful reply, just not LLM-smart on this turn.
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    handle_talk,
                    db2,
                    query_text,
                    "ko",
                    agent_id,
                    intents=None,
                    knowledge_base=realty_kb,
                    history=history,
                    current_path="/chatbot",
                ),
                timeout=1.5,
            )
        except asyncio.TimeoutError:
            log.warning(
                "chatbot_reply: LLM timed out (>1.5s) — conversational fallback",
                extra={"action": "chatbot.llm_timeout"},
            )
            # Layer 3: LLM was too slow for Kakao's window. Return a
            # conversational holding message so the customer feels heard
            # and the dialogue keeps flowing (instead of dead-ending on
            # a generic menu).
            return _pick_conversational_fallback(), "template-fallback-llm-timeout"

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
