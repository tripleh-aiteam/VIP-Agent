"""
VIP AI Platform — Voice Clone Service (Sprint 4)
Worker voice profile lifecycle: consent → sample upload → quality check →
MeloTTS fine-tune → ready for use by twin_voice_speaker.

This service intentionally does NOT block on the MeloTTS training step
itself — the training runs in a background task (or external worker) and
updates WorkerVoiceProfile.status when done. The endpoint flow is fast
and synchronous from the user's perspective.
"""

from __future__ import annotations

import os
import wave
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from db.models import WorkerVoiceProfile, PlatformUser
from services.logger import log


_SAMPLE_DIR = Path("uploads") / "voice_samples"
_MIN_SAMPLE_SEC = int(os.getenv("VOICE_CLONE_MIN_SAMPLE_SEC", "30"))
_MAX_SAMPLE_SEC = int(os.getenv("VOICE_CLONE_MAX_SAMPLE_SEC", "600"))   # 10 min cap


# ---------------------------------------------------------------------------
#  Consent
# ---------------------------------------------------------------------------

DEFAULT_CONSENT_TEXT_KR = (
    "본인은 디지털 트윈이 회의에서 본인의 음성으로 말할 수 있도록 "
    "음성 샘플 사용에 동의합니다. 본인의 음성 데이터는 본인의 트윈 "
    "음성 생성 목적으로만 사용되며, 언제든지 동의를 철회할 수 있습니다."
)

DEFAULT_CONSENT_TEXT_EN = (
    "I consent to my voice sample being used to create a cloned voice for my "
    "Digital Twin so it can speak on my behalf in meetings. My voice data will "
    "be used only for generating my twin's voice and I may revoke consent at "
    "any time."
)


def record_consent(
    db: Session,
    user_id: UUID,
    consent_text: Optional[str] = None,
) -> WorkerVoiceProfile:
    """Create or update the worker's voice profile with explicit consent."""
    user = db.query(PlatformUser).filter(PlatformUser.id == user_id).first()
    if not user:
        raise ValueError("User not found")

    profile = (
        db.query(WorkerVoiceProfile)
        .filter(WorkerVoiceProfile.user_id == user_id)
        .first()
    )
    if not profile:
        profile = WorkerVoiceProfile(user_id=user_id)
        db.add(profile)

    profile.consent_given = True
    profile.consent_text = consent_text or DEFAULT_CONSENT_TEXT_KR
    profile.consent_given_at = datetime.utcnow()
    profile.consent_revoked_at = None
    if profile.status == "revoked":
        profile.status = "pending"
    db.flush()
    return profile


def revoke_consent(db: Session, user_id: UUID) -> dict:
    profile = (
        db.query(WorkerVoiceProfile)
        .filter(WorkerVoiceProfile.user_id == user_id)
        .first()
    )
    if not profile:
        return {"revoked": False, "reason": "no profile"}
    profile.consent_given = False
    profile.consent_revoked_at = datetime.utcnow()
    profile.status = "revoked"
    db.flush()
    return {"revoked": True, "user_id": str(user_id)}


# ---------------------------------------------------------------------------
#  Sample upload
# ---------------------------------------------------------------------------

def store_voice_sample(
    db: Session,
    user_id: UUID,
    audio_bytes: bytes,
    filename: str = "sample.wav",
) -> dict:
    """Save the uploaded voice sample to disk, validate duration, and
    update WorkerVoiceProfile. Does NOT trigger training yet — call
    start_training() to do that.
    """
    profile = (
        db.query(WorkerVoiceProfile)
        .filter(WorkerVoiceProfile.user_id == user_id)
        .first()
    )
    if not profile:
        raise ValueError("No voice profile — record consent first")
    if not profile.consent_given:
        raise ValueError("Consent not recorded — cannot accept sample")

    _SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename).suffix.lower() or ".wav"
    out_path = _SAMPLE_DIR / f"{user_id}_{uuid4()}{suffix}"
    out_path.write_bytes(audio_bytes)

    duration_sec, quality = _evaluate_sample(out_path)

    if duration_sec < _MIN_SAMPLE_SEC:
        profile.status = "failed"
        profile.failure_reason = (
            f"Sample too short ({duration_sec}s); "
            f"need at least {_MIN_SAMPLE_SEC}s of clean speech"
        )
        db.flush()
        return {
            "accepted": False,
            "reason": profile.failure_reason,
            "duration_sec": duration_sec,
        }
    if duration_sec > _MAX_SAMPLE_SEC:
        # Truncate metadata — don't store huge files indefinitely
        log.warning(
            f"voice_clone: sample for user {user_id} is {duration_sec}s — "
            f"considered for trimming"
        )

    profile.sample_url = f"/static/voice_samples/{out_path.name}"
    profile.sample_duration_sec = duration_sec
    profile.sample_quality_score = quality
    profile.status = "pending"
    profile.failure_reason = None
    db.flush()
    return {
        "accepted": True,
        "profile_id": str(profile.id),
        "sample_url": profile.sample_url,
        "duration_sec": duration_sec,
        "quality_score": quality,
        "ready_for_training": True,
    }


def _evaluate_sample(path: Path) -> tuple[int, float]:
    """Returns (duration_seconds, quality_score 0-1).
    Sprint 4 quality_score is a placeholder — real implementation would
    use a noise/clipping/SNR analyzer or pass to a separate evaluator.
    """
    try:
        if path.suffix.lower() == ".wav":
            with wave.open(str(path), "rb") as wav:
                n_frames = wav.getnframes()
                sample_rate = wav.getframerate()
                duration = int(n_frames / sample_rate)
                return duration, 0.85   # placeholder quality score
    except Exception as e:
        log.warning(f"voice_clone: WAV introspection failed: {e}")
    # Fallback: estimate from file size assuming 16kHz mono 16-bit
    size = path.stat().st_size
    duration = int(size / (16000 * 2))
    return duration, 0.7


# ---------------------------------------------------------------------------
#  Training (MeloTTS fine-tune)
# ---------------------------------------------------------------------------

def start_training(db: Session, user_id: UUID) -> dict:
    """Kick off MeloTTS fine-tuning for the worker's voice. Sprint 4 ships
    a stub that immediately marks the profile 'ready' (using the default
    voice). Real fine-tune is infrastructure: GPU node + MeloTTS finetune
    script + write back melotts_model_path on completion.
    """
    profile = (
        db.query(WorkerVoiceProfile)
        .filter(WorkerVoiceProfile.user_id == user_id)
        .first()
    )
    if not profile:
        raise ValueError("No voice profile")
    if not profile.consent_given:
        raise ValueError("Consent not recorded")
    if not profile.sample_url:
        raise ValueError("No voice sample uploaded yet")

    profile.status = "training"
    profile.training_started_at = datetime.utcnow()
    db.flush()

    # ---- STUB: real implementation enqueues a background MeloTTS finetune.
    # For Sprint 4, immediately mark ready so the flow is testable end-to-end.
    # The twin_voice_speaker just uses the default OpenAI/MeloTTS voice
    # until a real finetune lands.
    profile.status = "ready"
    profile.training_completed_at = datetime.utcnow()
    profile.melotts_model_path = f"models/voice/{user_id}/default.bin"  # placeholder
    db.flush()

    return {
        "user_id": str(user_id),
        "status": profile.status,
        "training_started_at": profile.training_started_at.isoformat(),
        "training_completed_at": profile.training_completed_at.isoformat(),
        "note": "Sprint 4 stub — production finetune runs on GPU node and updates this row when done.",
    }


def get_profile(db: Session, user_id: UUID) -> Optional[dict]:
    profile = (
        db.query(WorkerVoiceProfile)
        .filter(WorkerVoiceProfile.user_id == user_id)
        .first()
    )
    if not profile:
        return None
    return {
        "id": str(profile.id),
        "user_id": str(profile.user_id),
        "consent_given": profile.consent_given,
        "consent_given_at": profile.consent_given_at.isoformat() if profile.consent_given_at else None,
        "consent_revoked_at": profile.consent_revoked_at.isoformat() if profile.consent_revoked_at else None,
        "sample_url": profile.sample_url,
        "sample_duration_sec": profile.sample_duration_sec,
        "sample_quality_score": profile.sample_quality_score,
        "status": profile.status,
        "melotts_model_path": profile.melotts_model_path,
        "training_started_at": profile.training_started_at.isoformat() if profile.training_started_at else None,
        "training_completed_at": profile.training_completed_at.isoformat() if profile.training_completed_at else None,
        "failure_reason": profile.failure_reason,
        "created_at": profile.created_at.isoformat() if profile.created_at else None,
    }
