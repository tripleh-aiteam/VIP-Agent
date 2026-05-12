"""
voice_vad — Voice Activity Detection for the audio pipeline.

Decides per-frame whether the caller is currently speaking. The
orchestrator uses this to know when the caller has finished a turn so
we can flush the speech buffer to STT.

Phase 1: webrtcvad (lightweight, CPU-only, no model download).
Phase 2: swap for Silero VAD when GPU is available — better tolerance
         for background noise. Install: pip install silero-vad.

Both expose the same `is_speech_frame(pcm_bytes, sample_rate)` contract.
"""

from __future__ import annotations

import os
from typing import Optional

from services.logger import log


# Lazy-loaded global VAD instance (loading is fast for webrtcvad)
_vad: Optional[object] = None


def _load_vad() -> Optional[object]:
    global _vad
    if _vad is not None:
        return _vad
    try:
        import webrtcvad
    except ImportError:
        log.warning(
            "voice_vad: webrtcvad not installed — falling back to amplitude threshold. "
            "Install with: pip install webrtcvad"
        )
        return None
    # Aggressiveness 0-3 (3 = most aggressive at filtering non-speech).
    # 2 is a good balance for phone audio with some background noise.
    aggressiveness = int(os.getenv("VOICE_VAD_AGGRESSIVENESS", "2"))
    _vad = webrtcvad.Vad(aggressiveness)
    return _vad


def is_speech_frame(pcm_bytes: bytes, sample_rate: int = 8000) -> bool:
    """Returns True if the given audio frame contains speech.

    Args:
      pcm_bytes: raw 16-bit signed PCM mono audio. webrtcvad requires
                 exact frame sizes: 10/20/30ms @ 8/16/32/48kHz. We pad
                 or truncate to 20ms (320 bytes @ 8kHz) to match the
                 AudioSocket frame size.
      sample_rate: must be 8000, 16000, 32000, or 48000 for webrtcvad.

    Conservative on error: returns True (assume speech) so we don't
    accidentally cut off a caller mid-sentence due to a VAD glitch.
    """
    vad = _load_vad()
    if vad is None:
        # Fallback: amplitude threshold check (any non-zero variance = "speech")
        return _amplitude_above_threshold(pcm_bytes)

    if sample_rate not in (8000, 16000, 32000, 48000):
        return True   # webrtcvad doesn't support this rate — assume speech

    # Pad / truncate to exactly 20ms (320 bytes @ 8kHz, 640 @ 16kHz, etc.)
    frame_size = int(sample_rate * 0.02) * 2     # 2 bytes per sample (16-bit)
    if len(pcm_bytes) < frame_size:
        pcm_bytes = pcm_bytes + b"\x00" * (frame_size - len(pcm_bytes))
    elif len(pcm_bytes) > frame_size:
        pcm_bytes = pcm_bytes[:frame_size]

    try:
        return vad.is_speech(pcm_bytes, sample_rate)         # type: ignore[attr-defined]
    except Exception:
        return True


def _amplitude_above_threshold(pcm_bytes: bytes, threshold: int = 500) -> bool:
    """Cheap fallback when webrtcvad isn't installed. Checks RMS amplitude.
    Phone signals typically run 1000-10000 in 16-bit space; threshold 500
    is below ambient hum but above true silence."""
    if not pcm_bytes:
        return False
    import audioop
    return audioop.rms(pcm_bytes, 2) > threshold
