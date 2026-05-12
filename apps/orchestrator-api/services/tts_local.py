"""
tts_local — Text-to-Speech wrapper for the voice pipeline.

Phase 1 (no GPU): OpenAI TTS API (~$0.015/min). Decent Korean quality.
                  Voice: "nova" or "shimmer" work well for KR.

Phase 2 (GPU ready): MeloTTS Korean (free, local, ~real-time on GPU).
                     Switch with VOICE_USE_LOCAL_TTS=1.

Output is always 16-bit signed PCM at the requested sample_rate so the
audio pipeline can write it back to Asterisk via AudioSocket without
additional resampling code in voice_pipeline.py.
"""

from __future__ import annotations

import asyncio
import io
import os
import struct
import wave
from typing import Optional

import httpx

from services.logger import log


def _use_local() -> bool:
    return os.getenv("VOICE_USE_LOCAL_TTS", "0") == "1"


async def synthesize(text: str, target_sample_rate: int = 8000) -> bytes:
    """Synthesize speech and return raw 16-bit signed PCM at the target
    sample rate. Empty text → empty bytes (no audio written)."""
    if not text or not text.strip():
        return b""
    if _use_local():
        return await _synthesize_local(text, target_sample_rate)
    return await _synthesize_openai(text, target_sample_rate)


# ----------------------------------------------------------------------------
# Phase 1 — OpenAI TTS API
# ----------------------------------------------------------------------------

async def _synthesize_openai(text: str, target_sample_rate: int) -> bytes:
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        log.warning("tts_local: OPENAI_API_KEY not set — cannot synthesize")
        return b""

    voice = os.getenv("VOICE_TTS_OPENAI_VOICE", "nova")     # nova/shimmer work well for KR
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.openai.com/v1/audio/speech",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "tts-1",      # tts-1-hd is slower; tts-1 fits phone latency
                    "voice": voice,
                    "input": text,
                    "response_format": "wav",
                    # OpenAI's WAV output defaults to 24kHz; we resample below
                },
            )
            if resp.status_code != 200:
                log.warning(f"tts_local: OpenAI TTS {resp.status_code}: {resp.text[:200]}")
                return b""
            wav_bytes = resp.content
            return _wav_to_pcm(wav_bytes, target_sample_rate)
    except Exception as e:
        log.warning(f"tts_local: OpenAI TTS error: {e}")
        return b""


# ----------------------------------------------------------------------------
# Phase 2 — local MeloTTS
# ----------------------------------------------------------------------------

async def _synthesize_local(text: str, target_sample_rate: int) -> bytes:
    """MeloTTS Korean. Install: pip install melo-tts
    Loaded once globally to amortize model load cost (~3s).

    Wired in next session. Placeholder errors loudly so misconfiguration
    can't silently degrade to no audio.
    """
    raise NotImplementedError(
        "Local MeloTTS not yet wired — Phase 2 work. "
        "Set VOICE_USE_LOCAL_TTS=0 to fall back to OpenAI TTS."
    )


# ----------------------------------------------------------------------------
# Helpers — WAV → raw PCM with simple linear resampling
# ----------------------------------------------------------------------------

def _wav_to_pcm(wav_bytes: bytes, target_sample_rate: int) -> bytes:
    """Decode a WAV blob into mono 16-bit PCM at target_sample_rate.
    Uses naive nearest-sample resampling — fine for 24kHz → 8kHz."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        n_channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        source_rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())

    if sample_width != 2:
        # OpenAI TTS returns 16-bit PCM in its WAVs; if a future provider
        # returns 32-bit float, convert here. For now bail loudly.
        log.warning(f"tts_local: unexpected sample width {sample_width}")
        return b""

    # Stereo → mono mix (rare for TTS, but safe)
    if n_channels == 2:
        samples = struct.unpack(f"<{len(frames)//2}h", frames)
        mono = [(samples[i] + samples[i + 1]) // 2 for i in range(0, len(samples), 2)]
        frames = struct.pack(f"<{len(mono)}h", *mono)

    if source_rate == target_sample_rate:
        return frames

    # Nearest-sample resample. For better quality use scipy.signal.resample_poly
    # — adding it later if we hear artifacts.
    ratio = source_rate / target_sample_rate
    source_samples = struct.unpack(f"<{len(frames)//2}h", frames)
    n_out = int(len(source_samples) / ratio)
    out = [source_samples[int(i * ratio)] for i in range(n_out)]
    return struct.pack(f"<{n_out}h", *out)
