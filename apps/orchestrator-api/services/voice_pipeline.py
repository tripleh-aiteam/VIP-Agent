"""
voice_pipeline — self-hosted voice agent audio orchestrator.

This is the runtime that bridges Asterisk's AudioSocket protocol to our
STT → LLM → TTS pipeline. Asterisk handles SIP/RTP; we handle the brain.

Architecture (per call):

  Asterisk AudioSocket connection (TCP, raw 16-bit PCM @ 8kHz)
       ▼
  _CallSession ─ buffers caller audio frames
       │       │
       │       └─ VAD detects end-of-speech (Silero VAD)
       │
       ▼ (on end-of-speech)
  stt_local.transcribe() ─ Whisper.cpp on local GPU
       │
       ▼
  llm_local.generate_reply() ─ Ollama running EXAONE 3.5 32B
       │   (streaming tokens for low first-token latency)
       ▼
  tts_local.synthesize() ─ MeloTTS Korean, streaming chunks
       │
       ▼ (resample 22kHz → 8kHz, encode as PCM frames)
  AudioSocket write back to Asterisk → caller hears reply

PHASE 1 (current, while waiting for KT credentials + GPU):
  - STT / LLM / TTS clients can fall back to CLOUD APIs (OpenAI Whisper,
    Claude API, OpenAI TTS) by setting VOICE_USE_CLOUD_APIS=1. This lets
    us validate the pipeline end-to-end before local hardware arrives.

PHASE 2 (after GPU arrives):
  - Flip VOICE_USE_CLOUD_APIS=0 and the same code uses local Whisper.cpp +
    Ollama + MeloTTS. Zero pipeline changes.

PHASE 3 (polish):
  - Barge-in: detect caller speech while bot is talking → stop TTS playback
  - Latency tuning: pipeline LLM and TTS in parallel where possible
  - Korean voice tuning: prosody, pacing

AudioSocket wire protocol (3-byte header per frame):
  byte 0    : message type (0x00 hangup, 0x01 UUID, 0x10 audio, 0x11 error)
  bytes 1-2 : payload length (big-endian uint16)
  bytes 3+  : payload (PCM bytes for audio; UTF-8 UUID for type 0x01)

Reference: https://docs.asterisk.org/Configuration/Channel-Drivers/AudioSocket/
"""

from __future__ import annotations

import asyncio
import os
import struct
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from db.base import SessionLocal
from services import voice_service
from services.logger import log


AUDIOSOCKET_HOST = os.getenv("AUDIOSOCKET_HOST", "0.0.0.0")
AUDIOSOCKET_PORT = int(os.getenv("AUDIOSOCKET_PORT", "8765"))

# Sample rate Asterisk sends (G.711 8kHz). Local TTS typically synthesizes
# at 22-24kHz — we downsample before sending back.
ASTERISK_SAMPLE_RATE = 8000
ASTERISK_BYTES_PER_FRAME = 2     # 16-bit signed PCM
ASTERISK_FRAME_DURATION_MS = 20  # standard SIP frame size


# ============================================================================
#  Wire protocol
# ============================================================================

class AudioSocketMessageType:
    HANGUP = 0x00
    UUID = 0x01
    AUDIO = 0x10
    ERROR = 0xFF


async def _read_message(reader: asyncio.StreamReader) -> Optional[tuple[int, bytes]]:
    """Read one AudioSocket message. Returns (type, payload) or None on EOF."""
    header = await reader.readexactly(3) if False else None  # placeholder
    try:
        header = await reader.readexactly(3)
    except asyncio.IncompleteReadError:
        return None
    msg_type = header[0]
    payload_len = struct.unpack(">H", header[1:3])[0]
    payload = await reader.readexactly(payload_len) if payload_len else b""
    return msg_type, payload


async def _write_audio(writer: asyncio.StreamWriter, pcm_bytes: bytes) -> None:
    """Write one PCM audio frame back to Asterisk. AudioSocket expects raw
    PCM 16-bit @ 8kHz mono. Caller is responsible for any resampling."""
    if not pcm_bytes:
        return
    # Frame in ~20ms chunks (320 bytes = 160 samples = 20ms @ 8kHz/16-bit)
    chunk_size = 320
    for i in range(0, len(pcm_bytes), chunk_size):
        chunk = pcm_bytes[i : i + chunk_size]
        header = bytes([AudioSocketMessageType.AUDIO]) + struct.pack(">H", len(chunk))
        writer.write(header + chunk)
        await writer.drain()


# ============================================================================
#  Per-call session state
# ============================================================================

@dataclass
class _CallSession:
    """In-memory state for one ongoing call. Created on AudioSocket connect,
    destroyed on hangup. The DB row in voice_calls is the persistent twin
    of this — fields here are short-lived audio buffers + pipeline state."""

    audio_socket_uuid: str
    agent_id: str = "vip"
    call_db_id: Optional[UUID] = None
    caller_number: Optional[str] = None
    # Buffer of caller audio since the last end-of-speech (raw PCM bytes)
    speech_buffer: bytearray = field(default_factory=bytearray)
    # Number of consecutive "silent" frames detected by VAD
    silence_frames: int = 0
    # Set when we're currently speaking (bot's TTS playback) — used for barge-in
    speaking: bool = False
    started_at: datetime = field(default_factory=datetime.utcnow)


# ============================================================================
#  Pipeline glue — STT → LLM → TTS
# ============================================================================

async def _handle_speech_turn(session: _CallSession, writer: asyncio.StreamWriter) -> None:
    """Caller finished speaking. Run STT → LLM → TTS, stream reply back.

    PHASE 1: this calls cloud APIs (Whisper API, Claude API, OpenAI TTS) when
    VOICE_USE_CLOUD_APIS=1. PHASE 2 swaps for local implementations — same
    function signatures, no orchestration changes needed.
    """
    if not session.speech_buffer:
        return
    buf = bytes(session.speech_buffer)
    session.speech_buffer.clear()

    # ── STT ──────────────────────────────────────────────────────────────
    try:
        from services import stt_local
        transcript = await stt_local.transcribe(buf, sample_rate=ASTERISK_SAMPLE_RATE)
    except Exception as e:
        log.warning(f"voice_pipeline: STT failed: {e}", extra={"action": "voice.stt_failed"})
        return

    if not transcript.strip():
        return     # Caller probably coughed / background noise

    log.info(
        f"voice_pipeline[{session.audio_socket_uuid[:8]}]: caller said '{transcript[:80]}'",
        extra={"action": "voice.user_turn"},
    )

    # Persist the user turn
    if session.call_db_id:
        _persist_turn(session, role="user", text=transcript)

    # ── LLM ──────────────────────────────────────────────────────────────
    try:
        from services import llm_local
        reply = await llm_local.generate_reply(
            agent_id=session.agent_id,
            user_text=transcript,
            call_db_id=session.call_db_id,
        )
    except Exception as e:
        log.warning(f"voice_pipeline: LLM failed: {e}", extra={"action": "voice.llm_failed"})
        reply = "죄송합니다, 잠시 후 다시 말씀해 주시겠어요?"      # Korean fallback

    log.info(
        f"voice_pipeline[{session.audio_socket_uuid[:8]}]: bot says '{reply[:80]}'",
        extra={"action": "voice.bot_turn"},
    )
    if session.call_db_id:
        _persist_turn(session, role="bot", text=reply)

    # ── TTS ──────────────────────────────────────────────────────────────
    try:
        from services import tts_local
        pcm_audio = await tts_local.synthesize(
            text=reply,
            target_sample_rate=ASTERISK_SAMPLE_RATE,
        )
    except Exception as e:
        log.warning(f"voice_pipeline: TTS failed: {e}", extra={"action": "voice.tts_failed"})
        return

    # ── Speak the reply ──────────────────────────────────────────────────
    session.speaking = True
    try:
        await _write_audio(writer, pcm_audio)
    finally:
        session.speaking = False


def _persist_turn(session: _CallSession, *, role: str, text: str) -> None:
    """Sync DB write — runs in thread pool to keep the async event loop free."""
    if not session.call_db_id:
        return
    db = SessionLocal()
    try:
        voice_service.upsert_turn(
            db,
            session.agent_id,
            session.call_db_id,
            role=role,
            text=text,
            partial=False,
            provider_turn_id=f"{session.audio_socket_uuid}:{datetime.utcnow().timestamp()}",
        )
    except Exception as e:
        log.warning(f"voice_pipeline: persist_turn failed: {e}")
    finally:
        db.close()


# ============================================================================
#  Main per-connection handler
# ============================================================================

async def _handle_connection(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    """One Asterisk AudioSocket connection = one call."""
    peer = writer.get_extra_info("peername")
    log.info(
        f"voice_pipeline: AudioSocket connection from {peer}",
        extra={"action": "voice.audiosocket_connect"},
    )

    session: Optional[_CallSession] = None

    try:
        while True:
            msg = await _read_message(reader)
            if msg is None:
                break
            msg_type, payload = msg

            if msg_type == AudioSocketMessageType.UUID:
                # First message — carries the call UUID Asterisk assigned.
                # Use it to create the voice_calls row.
                audio_socket_uuid = payload.decode("utf-8", errors="ignore")
                session = _CallSession(audio_socket_uuid=audio_socket_uuid)
                session.call_db_id = _create_call_row(session)
                log.info(
                    f"voice_pipeline: call started, uuid={audio_socket_uuid[:8]}",
                    extra={"action": "voice.audiosocket_uuid"},
                )

            elif msg_type == AudioSocketMessageType.AUDIO and session:
                # Caller audio frame (16-bit PCM @ 8kHz, ~20ms).
                # Append to speech buffer; VAD will tell us when caller stops.
                session.speech_buffer.extend(payload)

                # VAD check (every ~200ms of audio to amortize cost)
                if len(session.speech_buffer) % (ASTERISK_SAMPLE_RATE // 5 * ASTERISK_BYTES_PER_FRAME) == 0:
                    from services import voice_vad
                    is_speech = voice_vad.is_speech_frame(
                        payload, sample_rate=ASTERISK_SAMPLE_RATE
                    )
                    if is_speech:
                        session.silence_frames = 0
                    else:
                        session.silence_frames += 1
                        # ~600ms of silence = caller is done with this turn
                        if session.silence_frames >= 30 and len(session.speech_buffer) > 0:
                            session.silence_frames = 0
                            asyncio.create_task(_handle_speech_turn(session, writer))

            elif msg_type == AudioSocketMessageType.HANGUP:
                log.info(
                    f"voice_pipeline: hangup for {session.audio_socket_uuid[:8] if session else '?'}",
                    extra={"action": "voice.audiosocket_hangup"},
                )
                break

            elif msg_type == AudioSocketMessageType.ERROR:
                log.warning(
                    f"voice_pipeline: AudioSocket error: {payload!r}",
                    extra={"action": "voice.audiosocket_error"},
                )
                break

    except Exception as e:
        log.warning(
            f"voice_pipeline: connection error: {e}",
            extra={"action": "voice.audiosocket_exception"},
        )
    finally:
        if session:
            _finalize_call(session)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


# ============================================================================
#  DB integration — create/finalize voice_calls row
# ============================================================================

def _create_call_row(session: _CallSession) -> Optional[UUID]:
    """Create a voice_calls row in status=ringing. Returns the row's id."""
    db = SessionLocal()
    try:
        call = voice_service.start_call(
            db,
            session.agent_id,
            provider="selfhosted",
            provider_call_id=session.audio_socket_uuid,
            direction="inbound",     # AudioSocket inbound = SIP inbound (KT → Asterisk → us)
            caller_number=session.caller_number or "unknown",
            caller_name=None,
            started_at=session.started_at,
        )
        # Broadcast call.started so the dashboard sees it appear
        try:
            from routers.voice import get_broker
            get_broker().publish_sync(
                session.agent_id,
                {"type": "call.started", "call": voice_service.serialize_call(call, [])},
            )
        except Exception:
            pass
        return call.id
    finally:
        db.close()


def _finalize_call(session: _CallSession) -> None:
    """End the call row, trigger summary + escalation, broadcast call.ended."""
    if not session.call_db_id:
        return
    db = SessionLocal()
    try:
        call = voice_service.end_call(
            db, session.agent_id, session.call_db_id, status="completed"
        )
        # LLM summary + urgency classification
        try:
            from services import voice_summary
            voice_summary.generate_and_store_summary(db, session.agent_id, session.call_db_id)
            call = voice_service.get_call(db, session.agent_id, session.call_db_id)
        except Exception as e:
            log.warning(f"voice_pipeline: summary failed: {e}")
        # Escalate if high urgency
        try:
            if call and call.urgency == "high" and not call.escalation_json:
                from services import voice_escalation
                voice_escalation.escalate(db, call, reason="High urgency on call end")
                call = voice_service.get_call(db, session.agent_id, session.call_db_id)
        except Exception as e:
            log.warning(f"voice_pipeline: escalation failed: {e}")
        # Bridge to chatbot inbox so the call shows up in the same /chatbot view
        try:
            from services import chatbot_conversation_service as _conv_svc
            _conv_svc.bridge_voice_call_to_inbox(
                db, session.agent_id, session.call_db_id
            )
        except Exception as e:
            log.warning(f"voice_pipeline: chatbot bridge failed: {e}")
        # Broadcast to dashboard
        try:
            turns = voice_service.list_turns(db, session.agent_id, session.call_db_id)
            from routers.voice import get_broker
            get_broker().publish_sync(
                session.agent_id,
                {"type": "call.ended", "call": voice_service.serialize_call(call, turns)},
            )
            # Also push to the chatbot inbox subscribers
            from routers.chatbot_inbox import get_broker as get_chatbot_broker
            from db.models import ChatbotConversation
            chatbot_conv = (
                db.query(ChatbotConversation)
                .filter(
                    ChatbotConversation.agent_id == session.agent_id,
                    ChatbotConversation.voice_call_id == session.call_db_id,
                )
                .first()
            )
            if chatbot_conv:
                customer = _conv_svc.get_customer(db, session.agent_id, chatbot_conv.customer_id)
                get_chatbot_broker().publish_sync(
                    session.agent_id,
                    {
                        "type": "conversation.updated",
                        "conversation": _conv_svc.serialize_conversation(
                            chatbot_conv, customer=customer
                        ),
                    },
                )
        except Exception:
            pass
    finally:
        db.close()


# ============================================================================
#  Server bootstrap
# ============================================================================

async def start_audiosocket_server() -> None:
    """Start the AudioSocket TCP server. Called from main.py lifespan."""
    server = await asyncio.start_server(
        _handle_connection, AUDIOSOCKET_HOST, AUDIOSOCKET_PORT
    )
    addrs = ", ".join(str(s.getsockname()) for s in server.sockets)
    log.info(
        f"voice_pipeline: AudioSocket server listening on {addrs}",
        extra={"action": "voice.audiosocket_listen"},
    )
    async with server:
        await server.serve_forever()
