"""
stt_local — Speech-to-Text wrapper for the voice pipeline.

Phase 1 (no GPU): falls back to OpenAI Whisper API (~$0.006/min).
Phase 2 (GPU ready): switch to local Whisper.cpp by setting
                     VOICE_USE_LOCAL_STT=1.

Both implementations expose the same `transcribe(pcm_bytes, sample_rate)`
contract — orchestration code in voice_pipeline.py doesn't change when
you swap backends.

Whisper handles Korean well at every model size; the local default is
`large-v3` which matches the cloud API's quality.
"""

from __future__ import annotations

import io
import os
import wave
from typing import Optional

import httpx

from services.logger import log


def _use_local() -> bool:
    return os.getenv("VOICE_USE_LOCAL_STT", "0") == "1"


async def transcribe(pcm_bytes: bytes, sample_rate: int = 8000) -> str:
    """Transcribe a PCM audio chunk to Korean/English text.

    Args:
      pcm_bytes: raw 16-bit signed PCM audio
      sample_rate: source rate (typically 8000 from Asterisk)

    Returns the transcript as a string ("" on silence or error).
    """
    if not pcm_bytes:
        return ""
    if _use_local():
        return await _transcribe_local(pcm_bytes, sample_rate)
    return await _transcribe_openai(pcm_bytes, sample_rate)


# ----------------------------------------------------------------------------
# Phase 1 — OpenAI Whisper API (cloud)
# ----------------------------------------------------------------------------

async def _transcribe_openai(pcm_bytes: bytes, sample_rate: int) -> str:
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        log.warning("stt_local: OPENAI_API_KEY not set — cannot transcribe")
        return ""

    # Model + language are env-tunable so the operator can flip to
    # whisper-large-v3 for higher Korean accuracy without code changes.
    # Note: OpenAI's hosted Whisper API only exposes 'whisper-1' (large-v2);
    # WHISPER_MODEL=whisper-large-v3 is honored when VOICE_USE_LOCAL_STT=1
    # (faster-whisper / whisper.cpp).
    model = os.getenv("WHISPER_MODEL", "whisper-1")
    language = os.getenv("WHISPER_LANGUAGE", "ko")
    # Whisper accepts wav uploads; wrap raw PCM in a minimal WAV header.
    wav_bytes = _pcm_to_wav(pcm_bytes, sample_rate)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": ("speech.wav", wav_bytes, "audio/wav")},
                data={"model": model, "language": language},
            )
            if resp.status_code != 200:
                log.warning(f"stt_local: Whisper API {resp.status_code}: {resp.text[:200]}")
                return ""
            return (resp.json().get("text") or "").strip()
    except Exception as e:
        log.warning(f"stt_local: Whisper API error: {e}")
        return ""


# ----------------------------------------------------------------------------
# Phase 2 — local Whisper.cpp (GPU)
# ----------------------------------------------------------------------------

async def _transcribe_local(pcm_bytes: bytes, sample_rate: int) -> str:
    """Local Whisper.cpp via pywhispercpp. Install: pip install pywhispercpp
    Place ggml-large-v3.bin under WHISPER_MODEL_DIR (default: ./models/).

    Wired in next session — placeholder raises so misconfiguration is loud.
    """
    raise NotImplementedError(
        "Local Whisper.cpp not yet wired — Phase 2 work. "
        "Set VOICE_USE_LOCAL_STT=0 to fall back to OpenAI Whisper API."
    )


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def _pcm_to_wav(pcm_bytes: bytes, sample_rate: int) -> bytes:
    """Wrap raw 16-bit mono PCM in a WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)            # 16-bit
        wav.setframerate(sample_rate)
        wav.writeframes(pcm_bytes)
    return buf.getvalue()
