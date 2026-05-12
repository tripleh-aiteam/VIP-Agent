"""
Kakao Channel webhook — receives incoming messages from KakaoTalk customers.

Flow:
  Customer → KakaoTalk Channel → Kakao server → THIS endpoint
       ↓
  1. Verify webhook signature (HMAC against KAKAO_WEBHOOK_SECRET_<AGENT>)
  2. Resolve agent_id from Channel ID via chatbot_channel_mappings
  3. Find or create the Customer + Conversation rows
  4. Append the incoming message
  5. Run the chatbot_reply_service (Boss-IN: draft / Boss-OUT: send)
  6. Broadcast updates to dashboard WebSocket subscribers
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from db.base import get_db
from services import chatbot_conversation_service as conv_service
from services.logger import log


router = APIRouter(prefix="/api/chatbot", tags=["chatbot"])


# ============================================================================
#  HMAC signature verification — Kakao signs every webhook payload
# ============================================================================

def _verify_kakao_signature(
    agent_id: Optional[str], raw_body: bytes, signature: Optional[str]
) -> bool:
    """Verify the X-Kakao-Signature header. Returns True when no secret
    is configured (dev mode, smoke testing). Production sets:
        KAKAO_WEBHOOK_SECRET_<AGENT_UPPER>
    The webhook secret comes from the Kakao Developer Console when you
    register the webhook URL."""
    if not agent_id:
        # Can't pick the right secret without agent_id — caller is
        # responsible for resolving agent_id before calling this. For
        # the initial dispatch (before resolution), use a global fallback.
        secret = os.getenv("KAKAO_WEBHOOK_SECRET", "")
    else:
        secret = os.getenv(f"KAKAO_WEBHOOK_SECRET_{agent_id.upper()}", "") or os.getenv(
            "KAKAO_WEBHOOK_SECRET", ""
        )
    if not secret:
        return True
    if not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


# ============================================================================
#  Webhook entry point — single endpoint for ALL agents
# ============================================================================

@router.post("/webhook/kakao")
async def kakao_webhook(request: Request, db: Session = Depends(get_db)):
    """All Kakao Channel events arrive here.

    Kakao webhook payload (canonical shape varies slightly by event):
    {
      "user_request": {
        "user": { "id": "kakao_uuid", "type": "appUserId" },
        "utterance": "안녕하세요"        # text the user typed
      },
      "bot": { "id": "channel_id" },
      "channel": { "id": "channel_id", "name": "..." },
      "action": { "name": "..." },
      "params": { ... },
      ...
    }

    Custom skill servers (which we are) receive POST requests here whenever
    a user types in the Channel chat. We respond with the bot's reply
    (Boss-OUT) OR persist a draft for boss approval (Boss-IN).
    """
    raw = await request.body()
    signature = request.headers.get("x-kakao-signature") or request.headers.get(
        "X-Kakao-Signature"
    )

    try:
        payload = json.loads(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed JSON")

    # Step 1 — Resolve channel → agent_id
    channel_id = (
        (payload.get("channel") or {}).get("id")
        or (payload.get("bot") or {}).get("id")
        or ""
    )
    agent_id = conv_service.resolve_agent_id_from_channel(db, "kakao", channel_id)
    if not agent_id:
        log.warning(
            f"kakao.webhook: unknown channel {channel_id}",
            extra={"action": "kakao.webhook_unknown_channel"},
        )
        return {"ok": True, "skipped": "unknown channel"}

    # Step 2 — Verify signature now that we know the agent
    if not _verify_kakao_signature(agent_id, raw, signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # Step 3 — Extract user identity + message
    user_request = payload.get("user_request") or {}
    user = user_request.get("user") or {}
    user_id = user.get("id") or ""
    user_phone = user.get("phone")            # may be absent unless customer shared

    utterance = (user_request.get("utterance") or "").strip()
    attachment_type = (user_request.get("type") or "text").lower()

    # Step 4 — Find or create customer + conversation
    customer = conv_service.find_or_create_customer(
        db,
        agent_id,
        kakao_user_id=user_id,
        phone=user_phone,
    )
    conv = conv_service.find_or_create_conversation(
        db, agent_id, channel="kakao", customer_id=customer.id
    )

    # Step 5 — Append the incoming message (idempotent on provider_message_id if Kakao sends one)
    provider_msg_id = (
        payload.get("message_id")
        or (user_request.get("message") or {}).get("id")
    )

    # Determine message kind based on Kakao's payload type
    if attachment_type in ("audio", "voice"):
        # Voice message — see services/kakao_voice_handler.py (Phase A15)
        await _handle_voice_message(
            db, agent_id, conv, customer, payload, provider_msg_id
        )
    elif attachment_type in ("image", "photo"):
        await _handle_image_message(
            db, agent_id, conv, customer, payload, provider_msg_id
        )
    elif attachment_type in ("file", "document"):
        await _handle_file_message(
            db, agent_id, conv, customer, payload, provider_msg_id
        )
    else:
        # Default: text
        msg = conv_service.append_message(
            db, agent_id, conv.id,
            author="customer",
            kind="text",
            text=utterance,
            provider_message_id=provider_msg_id,
        )
        await _process_text_message(db, agent_id, conv, customer, utterance)

    # Step 6 — Broadcast updated conversation to dashboard subscribers
    try:
        from routers.chatbot_inbox import get_broker
        updated = conv_service.get_conversation(db, agent_id, conv.id)
        customer = conv_service.get_customer(db, agent_id, updated.customer_id)
        get_broker().publish_sync(
            agent_id,
            {
                "type": "conversation.updated",
                "conversation": conv_service.serialize_conversation(
                    updated, customer=customer
                ),
            },
        )
    except Exception as e:
        log.warning(f"kakao.webhook: broadcast failed: {e}")

    return {"ok": True}


# ============================================================================
#  Per-message-type handlers
# ============================================================================

async def _process_text_message(
    db: Session, agent_id: str, conv, customer, utterance: str
) -> None:
    """Run the reply pipeline for a text message."""
    if not utterance:
        return
    try:
        from services import chatbot_reply_service
        from services import kakao_client

        async def _send(text: str, _agent: str, _conv) -> None:
            try:
                kakao_client.send_text(
                    agent_id=agent_id,
                    conversation_id=str(_conv.id),
                    text=text,
                    receiver_uuid=customer.kakao_user_id,
                )
            except Exception as e:
                log.warning(f"kakao.send via reply_service failed: {e}")

        await chatbot_reply_service.handle_incoming_message(
            db, agent_id, conv, utterance, customer=customer, on_send=_send
        )
    except Exception as e:
        log.warning(
            f"kakao.webhook: reply pipeline error: {e}",
            extra={"action": "kakao.webhook_reply_failed"},
        )


async def _handle_voice_message(
    db: Session, agent_id: str, conv, customer, payload: dict, provider_msg_id: Optional[str]
) -> None:
    """Voice message: download audio → Whisper transcribe → reply pipeline.

    Flow:
      1. Persist the incoming voice message row immediately (empty transcript)
         so the dashboard renders the bubble even before transcription completes
      2. Download the audio file from Kakao (via the customer's auth scope)
      3. Transcribe via OpenAI Whisper API (handles mp3/m4a/wav directly,
         language="ko" hint for KR-first)
      4. Update the message row with the transcript + STT confidence
      5. Run the chatbot_reply_service with the transcript as the user's
         "utterance" — same path as text messages
    """
    media = (payload.get("user_request") or {}).get("media") or {}
    audio_url = media.get("url") or ""
    duration = media.get("duration_sec") or 0
    if not audio_url:
        log.warning("kakao.webhook: voice message missing media URL", extra={"action": "kakao.voice_no_url"})
        return

    # 1. Persist the message row first (so dashboard sees it immediately)
    msg = conv_service.append_message(
        db, agent_id, conv.id,
        author="customer",
        kind="voice",
        voice_url=audio_url,
        voice_duration_sec=int(duration) if duration else None,
        provider_message_id=provider_msg_id,
    )

    # 2. Download + transcribe (best-effort — failures don't break the inbox)
    transcript = ""
    confidence: Optional[float] = None
    try:
        from services import kakao_client
        audio_bytes = await asyncio.to_thread(
            kakao_client.download_incoming_media,
            agent_id=agent_id,
            media_url=audio_url,
        )
        transcript, confidence = await _transcribe_audio_bytes(audio_bytes)
    except Exception as e:
        log.warning(
            f"kakao.webhook: voice transcription failed: {e}",
            extra={"action": "kakao.voice_stt_failed"},
        )

    # 3. Update the persisted message with the transcript (if we got one)
    if transcript and msg:
        from db.models import ChatbotMessage as _M
        m_row = db.query(_M).filter(_M.id == msg.id).first()
        if m_row:
            m_row.voice_transcript = transcript
            if confidence is not None:
                m_row.confidence = confidence
            db.commit()

    # 4. Run the reply pipeline using the transcript as the "utterance"
    if transcript:
        await _process_text_message(db, agent_id, conv, customer, transcript)
    else:
        # Fallback: empty transcript → tell the customer we didn't catch it
        await _process_text_message(
            db, agent_id, conv, customer,
            "(음성 메시지를 받았지만 명확히 인식하지 못했습니다.)"
        )


async def _transcribe_audio_bytes(audio_bytes: bytes) -> tuple[str, Optional[float]]:
    """Transcribe arbitrary audio bytes via OpenAI Whisper API.

    Kakao voice notes are typically MP3/M4A. Whisper auto-detects format
    when uploaded via multipart — no codec conversion needed on our side.
    Language hint "ko" biases toward Korean (Whisper falls back to detection
    if the audio is actually English).

    Returns (transcript, confidence). Whisper's `transcriptions` endpoint
    doesn't return confidence directly, so we return None for it.
    """
    import os as _os
    if not audio_bytes or len(audio_bytes) < 200:
        return "", None
    api_key = _os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return "", None

    import httpx as _httpx
    try:
        async with _httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": ("kakao_voice.mp3", audio_bytes, "audio/mpeg")},
                data={"model": "whisper-1", "language": "ko"},
            )
            if resp.status_code != 200:
                log.warning(
                    f"kakao.webhook: Whisper API {resp.status_code}: {resp.text[:200]}",
                    extra={"action": "kakao.whisper_failed"},
                )
                return "", None
            text = (resp.json().get("text") or "").strip()
            return text, None
    except Exception as e:
        log.warning(f"kakao.webhook: Whisper transcribe error: {e}")
        return "", None


async def _handle_image_message(
    db: Session, agent_id: str, conv, customer, payload: dict, provider_msg_id: Optional[str]
) -> None:
    """Image message: download → Gemini Vision describes it → combine with
    customer's caption → run reply pipeline.

    The Gemini-described content + the caption together form the "utterance"
    that goes into the LLM brain. E.g. a leaking-ceiling photo with caption
    "어제부터 이래요" gives the LLM enough context to draft a maintenance
    response without the customer needing to type a detailed description.
    """
    media = (payload.get("user_request") or {}).get("media") or {}
    image_url = media.get("url") or ""
    caption = (payload.get("user_request") or {}).get("utterance", "") or None
    image_width = media.get("width") or None
    image_height = media.get("height") or None
    if not image_url:
        log.warning("kakao.webhook: image message missing media URL")
        return

    # Persist row first so dashboard renders immediately
    conv_service.append_message(
        db, agent_id, conv.id,
        author="customer",
        kind="image",
        image_url=image_url,
        image_caption=caption,
        image_width=int(image_width) if image_width else None,
        image_height=int(image_height) if image_height else None,
        provider_message_id=provider_msg_id,
    )

    # Download + Gemini Vision describe (best-effort)
    vision_description = ""
    try:
        from services import kakao_client
        from services.chatbot_perceive import perceive_image
        image_bytes = await asyncio.to_thread(
            kakao_client.download_incoming_media,
            agent_id=agent_id,
            media_url=image_url,
        )
        # Guess mime from URL extension; default to jpeg
        mime = "image/jpeg"
        url_lower = image_url.lower()
        if ".png" in url_lower:
            mime = "image/png"
        elif ".webp" in url_lower:
            mime = "image/webp"
        elif ".gif" in url_lower:
            mime = "image/gif"
        result = await perceive_image(
            image_bytes, mime, user_hint=caption or ""
        )
        vision_description = (result.get("content") or "").strip()
        log.info(
            f"kakao.webhook: image perceived ({len(vision_description)} chars)",
            extra={"action": "kakao.image_perceived"},
        )
    except Exception as e:
        log.warning(
            f"kakao.webhook: image perception failed: {e}",
            extra={"action": "kakao.image_perceive_failed"},
        )

    # Compose utterance: caption + vision description
    parts: list[str] = []
    if caption:
        parts.append(f"고객 메시지: {caption}")
    if vision_description:
        parts.append(f"[이미지 분석]\n{vision_description}")
    utterance = "\n\n".join(parts) if parts else "(이미지를 받았습니다.)"

    # Run reply pipeline
    await _process_text_message(db, agent_id, conv, customer, utterance)


async def _handle_file_message(
    db: Session, agent_id: str, conv, customer, payload: dict, provider_msg_id: Optional[str]
) -> None:
    """File attachment: persist metadata; downstream processing in Phase A16."""
    media = (payload.get("user_request") or {}).get("media") or {}
    file_url = media.get("url") or ""
    file_name = media.get("name") or "file"
    file_mime = media.get("mime_type") or "application/octet-stream"
    file_size = media.get("size_bytes") or 0
    conv_service.append_message(
        db, agent_id, conv.id,
        author="customer",
        kind="file",
        file_url=file_url,
        file_name=file_name,
        file_mime=file_mime,
        file_size_bytes=int(file_size) if file_size else None,
        provider_message_id=provider_msg_id,
    )
