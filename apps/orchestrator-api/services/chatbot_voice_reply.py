"""
chatbot_voice_reply — outbound voice messages for the chatbot.

When the bot is in Boss-OUT mode and the customer reached out via voice
(or explicitly prefers voice replies), this service:

  1. Calls OpenAI TTS (or local MeloTTS via the existing tts_local pipeline)
     to generate MP3 audio of the bot's reply.
  2. Uploads the MP3 to Supabase Storage under
     /{agent_id}/chatbot/{conversation_id}/voice-{timestamp}.mp3
  3. Returns the signed/public URL the channel client can attach.

Why MP3 (not the PCM that voice_pipeline uses): KakaoTalk's image/file
template attachments expect a downloadable URL pointing to a real audio
file format. MP3 is universally supported. The voice_pipeline operates
on raw PCM because it streams to Asterisk via AudioSocket — different
medium, different format. This service is parallel to (not a replacement
for) the live-call TTS path.

Channel constraint: Kakao Business Channel basic tier doesn't support
native voice messages — only feed/list/file templates. We fall back to
sending the audio URL as a link in a text template. The user can tap
the link to listen. When the channel is upgraded to Premium with audio
attachment support, kakao_client.send_voice_message handles the native
audio path."""

from __future__ import annotations

import asyncio
import os
import uuid as _uuid
from datetime import datetime
from typing import Optional

import httpx

from services.logger import log


# ============================================================================
#  Synthesis — text → MP3 bytes
# ============================================================================

async def synthesize_mp3(text: str) -> Optional[bytes]:
    """Generate an MP3 audio clip of `text` using OpenAI TTS (or local
    MeloTTS when VOICE_USE_LOCAL_TTS=1). Returns None on failure so the
    caller can fall back to text reply gracefully."""
    if not text or not text.strip():
        return None

    if os.getenv("VOICE_USE_LOCAL_TTS", "0") == "1":
        return await _synthesize_local_mp3(text)
    return await _synthesize_openai_mp3(text)


async def _synthesize_openai_mp3(text: str) -> Optional[bytes]:
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        log.warning("chatbot_voice_reply: OPENAI_API_KEY missing")
        return None

    voice = os.getenv("VOICE_TTS_OPENAI_VOICE", "nova")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.openai.com/v1/audio/speech",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "tts-1",
                    "voice": voice,
                    "input": text,
                    "response_format": "mp3",
                },
            )
            if resp.status_code != 200:
                log.warning(
                    f"chatbot_voice_reply: OpenAI TTS {resp.status_code}: "
                    f"{resp.text[:200]}"
                )
                return None
            return resp.content
    except Exception as e:
        log.warning(f"chatbot_voice_reply: TTS error: {e}")
        return None


async def _synthesize_local_mp3(text: str) -> Optional[bytes]:
    """Local MeloTTS path — not yet wired (GPU dependency).
    Falls back to OpenAI so VOICE_USE_LOCAL_TTS=1 doesn't break in dev."""
    log.info("chatbot_voice_reply: local TTS requested but not wired — falling back to OpenAI")
    return await _synthesize_openai_mp3(text)


# ============================================================================
#  Upload — MP3 bytes → Supabase Storage URL
# ============================================================================

async def upload_voice_reply(
    *,
    agent_id: str,
    conversation_id: str,
    mp3_bytes: bytes,
) -> Optional[str]:
    """Upload MP3 to Supabase Storage and return a signed URL (24h TTL).
    Returns None when Supabase isn't configured (dev mode)."""
    from services import voice_storage

    base = voice_storage._supabase_base()
    key = voice_storage._service_key()
    if not base or not key:
        log.warning("chatbot_voice_reply: Supabase not configured — voice reply skipped")
        return None

    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    storage_path = (
        f"{agent_id}/chatbot/{conversation_id}/voice-{ts}-{_uuid.uuid4().hex[:6]}.mp3"
    )
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            upload = await client.post(
                f"{base}/storage/v1/object/{voice_storage.BUCKET}/{storage_path}",
                headers={
                    "Authorization": f"Bearer {key}",
                    "apikey": key,
                    "Content-Type": "audio/mpeg",
                    "x-upsert": "true",
                },
                content=mp3_bytes,
            )
            if upload.status_code not in (200, 201):
                log.warning(
                    f"chatbot_voice_reply: upload failed "
                    f"{upload.status_code}: {upload.text[:200]}"
                )
                return None
            sign = await client.post(
                f"{base}/storage/v1/object/sign/{voice_storage.BUCKET}/{storage_path}",
                headers={"Authorization": f"Bearer {key}", "apikey": key},
                json={"expiresIn": 60 * 60 * 24},
            )
            if sign.status_code != 200:
                # Fall back to public URL
                return (
                    f"{base}/storage/v1/object/public/"
                    f"{voice_storage.BUCKET}/{storage_path}"
                )
            signed = sign.json().get("signedURL") or sign.json().get("signedUrl") or ""
            return (
                f"{base}/storage/v1{signed}" if signed.startswith("/") else signed
            )
    except Exception as e:
        log.warning(f"chatbot_voice_reply: upload error: {e}")
        return None


# ============================================================================
#  Combined helper — synthesize + upload in one call
# ============================================================================

async def synthesize_and_upload(
    *,
    agent_id: str,
    conversation_id: str,
    text: str,
) -> Optional[str]:
    """One-shot: text → MP3 → Storage → signed URL. Returns None on any
    failure (caller falls back to text-only reply)."""
    mp3 = await synthesize_mp3(text)
    if not mp3:
        return None
    return await upload_voice_reply(
        agent_id=agent_id,
        conversation_id=conversation_id,
        mp3_bytes=mp3,
    )


def estimate_duration_sec(mp3_bytes: bytes) -> int:
    """Best-effort duration estimate (bytes ÷ avg bitrate). For tts-1
    output the bitrate is around 32 kbps; we round up to be safe."""
    if not mp3_bytes:
        return 0
    return max(1, int(len(mp3_bytes) / 4000))     # ~4 KB/s ≈ 32 kbps
