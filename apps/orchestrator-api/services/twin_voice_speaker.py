"""
VIP AI Platform — Twin Voice Speaker (Sprint 3)
Outbound speech for digital twins in live meetings. Wraps the existing
tts_local.synthesize() pipeline and writes the rendered audio to a file
that the AudioSocket bridge (Sprint 3) or browser preview (Sprint 5) can
play back.

Sprint 3 uses the same TTS voice for every twin (OpenAI 'nova' or local
MeloTTS default speaker). Sprint 4 swaps in a per-worker cloned voice
profile via WorkerVoiceProfile lookup.
"""

from __future__ import annotations

import os
import struct
import wave
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from db.models import DigitalTwin, PlatformUser
from services.logger import log
from services.tts_local import synthesize as tts_synthesize


# Sample rate for rendered twin audio. 16kHz matches WAV-friendly defaults
# and is what the dashboard <audio> tag can play back without resampling.
_SAMPLE_RATE = int(os.getenv("TWIN_VOICE_SAMPLE_RATE", "16000"))
_AUDIO_DIR = Path("uploads") / "twin_voice"


async def synthesize_for_twin(
    db: Session,
    twin_id: UUID,
    text: str,
    sample_rate: int = _SAMPLE_RATE,
) -> dict:
    """Render the given text to a WAV file using the twin's voice profile
    (Sprint 4) or the default TTS voice (Sprint 3). Returns metadata
    suitable for storing on a MeetingUtterance row.

    Output: dict with keys: audio_path, audio_url, sample_rate, byte_count.
    Empty text returns audio_path=None.
    """
    if not text or not text.strip():
        return {"audio_path": None, "audio_url": None, "sample_rate": sample_rate, "byte_count": 0}

    twin = db.query(DigitalTwin).filter(DigitalTwin.id == twin_id).first()
    if not twin:
        raise ValueError(f"Twin {twin_id} not found")

    # Sprint 4 hook — look up worker voice profile if available
    voice_profile = _lookup_voice_profile(db, twin_id)
    if voice_profile and voice_profile.get("ready"):
        # Future: switch tts_local to use worker's cloned voice
        log.info(
            f"twin_voice_speaker: using worker-cloned voice for twin {twin.name} "
            f"(profile_id={voice_profile.get('profile_id')})"
        )

    # Render speech to PCM
    pcm = await tts_synthesize(text, target_sample_rate=sample_rate)
    if not pcm:
        return {"audio_path": None, "audio_url": None, "sample_rate": sample_rate, "byte_count": 0}

    # Persist as WAV so browsers can play it directly
    _AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{twin_id}_{uuid4()}.wav"
    out_path = _AUDIO_DIR / filename
    _pcm_to_wav_file(pcm, sample_rate, str(out_path))

    audio_url = f"/static/twin_voice/{filename}"
    return {
        "audio_path": str(out_path),
        "audio_url": audio_url,
        "sample_rate": sample_rate,
        "byte_count": len(pcm),
    }


def _lookup_voice_profile(db: Session, twin_id: UUID) -> Optional[dict]:
    """Sprint 4 dependency: returns the worker's cloned voice profile if
    ready. Soft-imports so Sprint 3 works before Sprint 4's table exists.
    """
    try:
        from db.models import WorkerVoiceProfile
    except ImportError:
        return None
    try:
        # Find the worker who owns this twin
        owner = (
            db.query(PlatformUser)
            .filter(PlatformUser.twin_id == twin_id)
            .first()
        )
        if not owner:
            return None
        profile = (
            db.query(WorkerVoiceProfile)
            .filter(
                WorkerVoiceProfile.user_id == owner.id,
                WorkerVoiceProfile.status == "ready",
            )
            .first()
        )
        if not profile:
            return None
        return {
            "profile_id": str(profile.id),
            "ready": True,
            "melotts_model_path": profile.melotts_model_path,
        }
    except Exception as e:
        log.warning(f"twin_voice_speaker: voice profile lookup failed: {e}")
        return None


def _pcm_to_wav_file(pcm_bytes: bytes, sample_rate: int, out_path: str) -> None:
    """Wrap raw 16-bit signed mono PCM in a WAV container."""
    with wave.open(out_path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm_bytes)
