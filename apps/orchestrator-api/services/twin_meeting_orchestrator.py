"""
VIP AI Platform — Twin Meeting Orchestrator (Sprint 3)
Full duplex: takes an external utterance, runs it through twin_brain.think()
with meeting context, applies the authority gate, generates a spoken reply
via twin_voice_speaker.synthesize_for_twin(), and records the twin's reply
as a MeetingUtterance.

This is the "speaking" half of Sprint 3. The "listening" half is
twin_voice_listener.py (Sprint 2). Together they form the conversational
loop: external speaker -> STT -> orchestrator -> TTS -> meeting audio.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from db.models import (
    DigitalTwin, Meeting, MeetingParticipant, MeetingUtterance,
    PlatformUser, DirectMessage,
)
from services import twin_brain, twin_meeting_session, twin_voice_speaker
from services.logger import log
from services.twin_voice_listener import detect_commitment


# Default Korean stall phrase when twin must escalate mid-meeting
_DEFAULT_STALL_KR = "잠시만요, 확인 후 답변드리겠습니다."

# v4-D: real-worker fallback when the twin lacks the knowledge to answer
_FOLLOWUP_KR = "지금 확실하지 않으니 확인 후 보고서로 드리겠습니다."
_FOLLOWUP_EN = "I'm not sure right now — I'll check and send you a written report."

# Phrases the LLM tends to emit when it doesn't actually know.
_LOW_CONFIDENCE_MARKERS = [
    "i don't know", "i'm not sure", "i am not sure", "no information",
    "cannot find", "i cannot", "i can't answer", "no data",
    "잘 모르겠", "확실하지 않", "정보가 없", "잘 모릅",
]


async def respond_in_meeting(
    db: Session,
    twin_id: UUID,
    meeting_id: UUID,
    prompt: str,
    model: Optional[str] = None,
    speak_aloud: bool = True,
) -> dict:
    """Generate the twin's response to a meeting prompt. Applies authority
    gate (escalates if needed), persists the reply as a MeetingUtterance,
    and returns the rendered audio URL + text.

    Args:
      prompt: what the twin is being asked / what was just said to it
      speak_aloud: if True, also renders TTS audio (Sprint 3 default).
                   Set False to do text-only "think" in shadow mode.
    """
    participant = _get_active_participant(db, twin_id, meeting_id)
    if not participant:
        raise ValueError("No active meeting participant for this twin")

    authority = participant.meeting_authority or "answer_factual"
    if not twin_meeting_session.authority_allows(authority, "speak"):
        # listener_only — twin should not speak. Log but skip.
        log.info(
            f"twin_meeting_orchestrator: twin {twin_id} suppressed "
            f"(authority={authority})"
        )
        return {
            "spoke": False,
            "reason": "authority is listener_only",
            "text": None,
            "audio_url": None,
        }

    twin = db.query(DigitalTwin).filter(DigitalTwin.id == twin_id).first()
    if not twin:
        raise ValueError(f"Twin {twin_id} not found")

    meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
    meeting_title = meeting.title if meeting else "the meeting"

    # Build meeting-context prompt — twin_brain.think() already handles
    # personality + knowledge selection
    contextual_prompt = (
        f"You are currently attending a live meeting titled '{meeting_title}' "
        f"on behalf of your worker. Your authority level is '{authority}'.\n\n"
        f"Someone in the meeting just said:\n\"{prompt}\"\n\n"
        f"Respond briefly (1-3 sentences max, like real meeting speech). "
        f"If you cannot answer factually from your knowledge, say so. "
        f"Do NOT commit to action items unless your authority is "
        f"'answer_and_commit' or 'full_proxy'."
    )

    reply_text = twin_brain.think(db, twin_id, contextual_prompt, model=model)
    reply_text = (reply_text or "").strip()

    # v4-D: low-confidence fallback. If the twin clearly doesn't know,
    # speak the fallback phrase and create a follow-up task so the twin
    # learns + reports back later instead of bluffing in the meeting.
    if _is_low_confidence(reply_text):
        fallback_text = _FOLLOWUP_KR if _has_korean(prompt) else _FOLLOWUP_EN
        fallback_audio = None
        if speak_aloud:
            audio_meta = await twin_voice_speaker.synthesize_for_twin(
                db, twin_id, fallback_text,
            )
            fallback_audio = audio_meta.get("audio_url")

        # Record what the twin actually said (the fallback) — not the bluff
        twin_meeting_session.record_utterance(
            db,
            meeting_id=meeting_id,
            participant_id=participant.id,
            speaker_role="twin",
            speaker_label=f"{twin.name} (Twin)",
            text=fallback_text,
            text_korean=fallback_text if _has_korean(fallback_text) else None,
            audio_url=fallback_audio,
            is_commitment=False,
            requires_worker_review=True,
        )

        # Create a follow-up TwinTask so the twin researches the answer
        # afterwards and reports back to the worker.
        try:
            from db.models import TwinTask as _TwinTask
            follow_up = _TwinTask(
                twin_id=twin_id,
                title=f"[Meeting follow-up] {prompt[:160]}",
                description=(
                    f"During a meeting you couldn't answer this question:\n"
                    f"\"{prompt}\"\n\n"
                    f"Research the answer using your knowledge base, agent tools, "
                    f"and any documents the worker has shared. Produce a written "
                    f"report. The worker and meeting attendees will be notified."
                ),
                status="todo",
                priority="high",
                assigned_by="meeting_followup",
                assigned_in_meeting_id=meeting_id,
                needs_review=True,
                review_status="pending",
            )
            db.add(follow_up)
            db.flush()
        except Exception as e:
            log.warning(f"orchestrator: could not create follow-up task: {e}")

        db.commit()
        return {
            "spoke": True,
            "escalated": False,
            "low_confidence_fallback": True,
            "text": fallback_text,
            "wanted_to_say": reply_text,
            "audio_url": fallback_audio,
            "authority": authority,
        }

    # Detect commitment
    is_commitment = detect_commitment(reply_text)
    needs_escalation = is_commitment and not twin_meeting_session.authority_allows(authority, "commit")

    audio_url = None
    if needs_escalation:
        # Twin tried to commit beyond its authority — stall verbally + escalate
        stall_text = _DEFAULT_STALL_KR
        if speak_aloud:
            audio_meta = await twin_voice_speaker.synthesize_for_twin(
                db, twin_id, stall_text,
            )
            audio_url = audio_meta.get("audio_url")

        # Record the stall utterance instead of the committal one
        twin_meeting_session.record_utterance(
            db,
            meeting_id=meeting_id,
            participant_id=participant.id,
            speaker_role="twin",
            speaker_label=f"{twin.name} (Twin)",
            text=stall_text,
            text_korean=stall_text,
            audio_url=audio_url,
            is_commitment=False,
            requires_worker_review=True,
            confidence=None,
            latency_ms=None,
        )

        # Escalate to worker
        twin_meeting_session.escalate(
            db, twin_id, meeting_id,
            question=f"In meeting: \"{prompt}\" — twin wanted to say: \"{reply_text}\"",
            stall_phrase_kr=stall_text,
        )
        db.commit()
        return {
            "spoke": True,
            "escalated": True,
            "text": stall_text,
            "wanted_to_say": reply_text,
            "audio_url": audio_url,
            "authority": authority,
        }

    # Normal path: render audio + record utterance
    if speak_aloud:
        audio_meta = await twin_voice_speaker.synthesize_for_twin(
            db, twin_id, reply_text,
        )
        audio_url = audio_meta.get("audio_url")

    twin_meeting_session.record_utterance(
        db,
        meeting_id=meeting_id,
        participant_id=participant.id,
        speaker_role="twin",
        speaker_label=f"{twin.name} (Twin)",
        text=reply_text,
        text_korean=reply_text if _has_korean(reply_text) else None,
        audio_url=audio_url,
        is_commitment=is_commitment,
        requires_worker_review=is_commitment,
        confidence=None,
        latency_ms=None,
    )
    db.commit()

    return {
        "spoke": True,
        "escalated": False,
        "text": reply_text,
        "audio_url": audio_url,
        "is_commitment": is_commitment,
        "authority": authority,
    }


def _get_active_participant(
    db: Session, twin_id: UUID, meeting_id: UUID
) -> Optional[MeetingParticipant]:
    return (
        db.query(MeetingParticipant)
        .filter(
            MeetingParticipant.meeting_id == meeting_id,
            MeetingParticipant.twin_id == twin_id,
            MeetingParticipant.session_status.in_(("active", "escalated")),
        )
        .first()
    )


def _has_korean(text: str) -> bool:
    import re
    return bool(re.search(r"[가-힯]", text or ""))


def _is_low_confidence(reply: str) -> bool:
    """Heuristic: did the LLM essentially say 'I don't know'?"""
    if not reply:
        return True
    low = reply.lower()
    return any(marker in low for marker in _LOW_CONFIDENCE_MARKERS)
