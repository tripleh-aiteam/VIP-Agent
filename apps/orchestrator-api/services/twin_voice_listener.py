"""
VIP AI Platform — Twin Voice Listener (Sprint 2)
Live-meeting STT pipeline: feed audio (uploaded WAV file or Asterisk
AudioSocket stream) → split into chunks → Whisper STT → MeetingUtterance
audit log via twin_meeting_session.record_utterance().

In Sprint 2 the twin is in "shadow / listener-only" mode — every external
speech is logged, twin itself does not speak yet. Sprint 3 adds outbound
voice (MeloTTS) and turn-taking.

Audio sources supported:
- "file"      — WAV file upload (primary test path, no Asterisk needed)
- "asterisk"  — AudioSocket bridge to a live SIP call (stub here, full
                wiring in Sprint 3 using existing services/voice_pipeline.py)
"""

from __future__ import annotations

import asyncio
import io
import os
import re
import uuid
import wave
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import UUID

from db.base import SessionLocal
from services import twin_meeting_session
from services.logger import log
from services.stt_local import transcribe as stt_transcribe


# ---------------------------------------------------------------------------
#  Commitment detector — flags utterances where speaker says "I will ..."
# ---------------------------------------------------------------------------

# Korean + English commitment patterns
_COMMITMENT_PATTERNS = [
    # English
    r"\bi(?:'ll| will)\b",
    r"\bwe(?:'ll| will)\b",
    r"\bi commit\b",
    r"\bi(?:'ll| will) (?:do|finish|send|deliver|handle|complete|prepare)\b",
    r"\bby (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|tonight|eod|end of)\b",
    # Korean
    r"하겠습니다",     # will do
    r"드리겠습니다",   # will deliver/give
    r"제출하겠",       # will submit
    r"확인하겠",       # will confirm
    r"처리하겠",       # will handle
    r"준비하겠",       # will prepare
    r"보내드리",       # will send
    r"약속드립니다",   # I promise
    r"끝내겠",         # will finish
    r"마무리하겠",     # will wrap up
]
_COMMITMENT_REGEX = re.compile("|".join(_COMMITMENT_PATTERNS), re.IGNORECASE)


def detect_commitment(text: str) -> bool:
    """Return True if the utterance contains a commitment phrase."""
    if not text or len(text) < 5:
        return False
    return bool(_COMMITMENT_REGEX.search(text))


# ---------------------------------------------------------------------------
#  Session registry — in-memory tracking of running listeners
# ---------------------------------------------------------------------------

@dataclass
class ListenSession:
    session_id: str
    twin_id: UUID
    meeting_id: UUID
    source: str                              # "file" | "asterisk"
    started_at: datetime
    task: Optional[asyncio.Task] = None
    chunks_processed: int = 0
    last_utterance_preview: str = ""
    status: str = "running"                  # running | done | stopped | error
    error_message: Optional[str] = None
    finished_at: Optional[datetime] = None
    metadata: dict = field(default_factory=dict)


_sessions: dict[str, ListenSession] = {}


def get_status(session_id: str) -> Optional[dict]:
    s = _sessions.get(session_id)
    if not s:
        return None
    return {
        "session_id": s.session_id,
        "twin_id": str(s.twin_id),
        "meeting_id": str(s.meeting_id),
        "source": s.source,
        "status": s.status,
        "chunks_processed": s.chunks_processed,
        "last_utterance_preview": s.last_utterance_preview[:200],
        "started_at": s.started_at.isoformat(),
        "finished_at": s.finished_at.isoformat() if s.finished_at else None,
        "error_message": s.error_message,
        "metadata": s.metadata,
    }


def list_active_listeners(meeting_id: Optional[UUID] = None) -> list[dict]:
    out = []
    for s in _sessions.values():
        if meeting_id and s.meeting_id != meeting_id:
            continue
        out.append(get_status(s.session_id))  # type: ignore[arg-type]
    return out


def stop_listening(session_id: str) -> dict:
    s = _sessions.get(session_id)
    if not s:
        return {"stopped": False, "reason": "session not found"}
    if s.task and not s.task.done():
        s.task.cancel()
    s.status = "stopped"
    s.finished_at = datetime.utcnow()
    return {"stopped": True, "session_id": session_id}


# ---------------------------------------------------------------------------
#  File-based listener (PRIMARY TEST PATH — no Asterisk required)
# ---------------------------------------------------------------------------

# Chunk size for transcription windows (seconds). Larger = better Whisper
# context; smaller = lower latency and more granular audit log.
_CHUNK_SECONDS = float(os.getenv("TWIN_VOICE_CHUNK_SECONDS", "8"))


def start_listening_from_file(
    twin_id: UUID,
    meeting_id: UUID,
    audio_path: str,
    speaker_label: str = "Meeting Audio",
) -> str:
    """Kick off file-based transcription as a background task.
    Returns a session_id immediately; transcription continues async.
    """
    session_id = f"listen-{uuid.uuid4()}"
    session = ListenSession(
        session_id=session_id,
        twin_id=twin_id,
        meeting_id=meeting_id,
        source="file",
        started_at=datetime.utcnow(),
        metadata={"audio_path": audio_path, "speaker_label": speaker_label},
    )
    _sessions[session_id] = session
    session.task = asyncio.create_task(
        _run_file_listener(session, audio_path, speaker_label)
    )
    return session_id


async def _run_file_listener(
    session: ListenSession, audio_path: str, speaker_label: str
) -> None:
    """Background coroutine: read WAV, chunk it, STT each chunk, log."""
    try:
        if not Path(audio_path).exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        with wave.open(audio_path, "rb") as wav:
            sample_rate = wav.getframerate()
            sample_width = wav.getsampwidth()
            n_channels = wav.getnchannels()
            n_frames = wav.getnframes()
            total_duration_sec = n_frames / sample_rate

            log.info(
                f"twin_voice_listener: opened {audio_path} "
                f"({sample_rate}Hz, {n_channels}ch, {total_duration_sec:.1f}s)"
            )

            session.metadata["sample_rate"] = sample_rate
            session.metadata["duration_sec"] = round(total_duration_sec, 1)

            frames_per_chunk = int(sample_rate * _CHUNK_SECONDS)
            chunk_idx = 0
            cumulative_offset_ms = 0

            while True:
                pcm = wav.readframes(frames_per_chunk)
                if not pcm:
                    break

                # Mix stereo to mono if needed (Whisper expects mono)
                if n_channels == 2 and sample_width == 2:
                    pcm = _stereo_to_mono(pcm)

                # Transcribe this chunk
                t0 = datetime.utcnow()
                text = await stt_transcribe(pcm, sample_rate)
                latency_ms = int((datetime.utcnow() - t0).total_seconds() * 1000)

                if text and text.strip():
                    is_commit = detect_commitment(text)
                    _persist_utterance(
                        twin_id=session.twin_id,
                        meeting_id=session.meeting_id,
                        speaker_role="external",   # Sprint 2: no diarization yet
                        speaker_label=speaker_label,
                        text=text.strip(),
                        text_korean=text.strip() if _has_korean(text) else None,
                        is_commitment=is_commit,
                        requires_worker_review=is_commit,
                        confidence=None,
                        latency_ms=latency_ms,
                    )
                    session.chunks_processed += 1
                    session.last_utterance_preview = text.strip()
                    log.info(
                        f"twin_voice_listener[{session.session_id}] "
                        f"chunk {chunk_idx} @ {cumulative_offset_ms/1000:.1f}s: "
                        f"{text[:80]}{' [COMMIT]' if is_commit else ''}"
                    )

                chunk_idx += 1
                cumulative_offset_ms += int(_CHUNK_SECONDS * 1000)
                # Yield control so other tasks (incl. status reads) can run
                await asyncio.sleep(0)

        session.status = "done"
        session.finished_at = datetime.utcnow()

    except asyncio.CancelledError:
        session.status = "stopped"
        session.finished_at = datetime.utcnow()
        raise
    except Exception as e:
        log.warning(f"twin_voice_listener: file listener failed: {e}")
        session.status = "error"
        session.error_message = str(e)[:300]
        session.finished_at = datetime.utcnow()


# ---------------------------------------------------------------------------
#  Asterisk listener (Sprint 3 — stubbed here so the endpoint exists)
# ---------------------------------------------------------------------------

def start_listening_from_asterisk(
    twin_id: UUID,
    meeting_id: UUID,
    asterisk_channel_id: str,
    speaker_label: str = "SIP Caller",
) -> str:
    """Attach to a live Asterisk channel via AudioSocket and stream audio
    into the same STT pipeline used by start_listening_from_file. The full
    AudioSocket bridge lives in services/voice_pipeline.py — Sprint 3
    wires it to write MeetingUtterance rows instead of VoiceCallTurn.
    """
    session_id = f"listen-{uuid.uuid4()}"
    session = ListenSession(
        session_id=session_id,
        twin_id=twin_id,
        meeting_id=meeting_id,
        source="asterisk",
        started_at=datetime.utcnow(),
        status="error",
        error_message=(
            "Asterisk bridge wiring lands in Sprint 3 — see "
            "services/voice_pipeline.py for the AudioSocket runtime. "
            "For Sprint 2 use the /listen/upload file path to validate STT."
        ),
        finished_at=datetime.utcnow(),
        metadata={"asterisk_channel_id": asterisk_channel_id},
    )
    _sessions[session_id] = session
    return session_id


# ---------------------------------------------------------------------------
#  Internal helpers
# ---------------------------------------------------------------------------

def _persist_utterance(
    twin_id: UUID,
    meeting_id: UUID,
    speaker_role: str,
    speaker_label: str,
    text: str,
    text_korean: Optional[str],
    is_commitment: bool,
    requires_worker_review: bool,
    confidence: Optional[float],
    latency_ms: Optional[int],
) -> None:
    """Write one utterance row using a short-lived DB session.
    Background task can't reuse the request's session, so we make our own.
    """
    db = SessionLocal()
    try:
        twin_meeting_session.record_utterance(
            db,
            meeting_id=meeting_id,
            participant_id=None,        # external speaker — no participant row
            speaker_role=speaker_role,
            speaker_label=speaker_label,
            text=text,
            text_korean=text_korean,
            is_commitment=is_commitment,
            requires_worker_review=requires_worker_review,
            confidence=confidence,
            latency_ms=latency_ms,
        )
        db.commit()
    except Exception as e:
        db.rollback()
        log.warning(f"twin_voice_listener: persist failed: {e}")
    finally:
        db.close()


def _stereo_to_mono(pcm_bytes: bytes) -> bytes:
    """Average left+right channels into mono. Assumes 16-bit signed PCM."""
    import struct
    samples = struct.unpack(f"<{len(pcm_bytes)//2}h", pcm_bytes)
    mono = []
    for i in range(0, len(samples), 2):
        left = samples[i]
        right = samples[i + 1] if i + 1 < len(samples) else left
        mono.append((left + right) // 2)
    return struct.pack(f"<{len(mono)}h", *mono)


_KOREAN_RE = re.compile(r"[가-힯ᄀ-ᇿ㄰-㆏]")


def _has_korean(text: str) -> bool:
    return bool(_KOREAN_RE.search(text))
