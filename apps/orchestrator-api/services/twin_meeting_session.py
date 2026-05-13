"""
VIP AI Platform — Twin Meeting Session Service (Sprint 1)
Live-meeting attendance: a digital twin joins a Meeting on behalf of an absent
worker, listens, answers within its authority, escalates to the worker when
needed, and produces a post-meeting summary.

Sprint 1 scope: DB lifecycle + authority gate + utterance audit.
Sprint 2 wires in the actual Asterisk/Whisper/EXAONE/MeloTTS voice IO.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from db.models import (
    DigitalTwin, Meeting, MeetingParticipant, MeetingUtterance,
    MeetingMinutes, PlatformUser, DirectMessage, TwinTask,
)
from services import twin_service


# ---------------------------------------------------------------------------
#  Authority gate — what can the twin do in this meeting?
# ---------------------------------------------------------------------------

# Authorities, ordered weakest -> strongest
_AUTHORITY_RANK = {
    "listener_only": 0,
    "answer_factual": 1,
    "answer_and_commit": 2,
    "full_proxy": 3,
}


def authority_allows(authority: str, action: str) -> bool:
    """Check if a given authority level permits a given action.
    Actions: 'speak', 'answer_factual', 'commit', 'decide_unilateral'.
    """
    rank = _AUTHORITY_RANK.get(authority, 1)
    if action == "speak":
        return rank >= 1
    if action == "answer_factual":
        return rank >= 1
    if action == "commit":
        return rank >= 2
    if action == "decide_unilateral":
        return rank >= 3
    return False


# ---------------------------------------------------------------------------
#  Session lifecycle
# ---------------------------------------------------------------------------

def join_meeting(
    db: Session,
    twin_id: UUID,
    meeting_id: UUID,
    for_user_id: Optional[UUID],
    authority: str,
    authorized_by_user_id: Optional[UUID],
    reason: Optional[str] = None,
) -> MeetingParticipant:
    """Create a MeetingParticipant row representing the twin's attendance.
    Marks the twin's status as in_meeting. Logs activity.
    """
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        raise ValueError(f"Twin {twin_id} not found")

    meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
    if not meeting:
        raise ValueError(f"Meeting {meeting_id} not found")

    # Sprint 7 fix — can't join an already-ended meeting
    if meeting.status == "ended":
        raise ValueError(
            f"Meeting '{meeting.title}' has already ended on "
            f"{meeting.ended_at.isoformat() if meeting.ended_at else 'an earlier date'}. "
            f"Create a new meeting first."
        )

    # Prevent duplicate active participation for the same twin
    existing = (
        db.query(MeetingParticipant)
        .filter(
            MeetingParticipant.meeting_id == meeting_id,
            MeetingParticipant.twin_id == twin_id,
            MeetingParticipant.session_status.in_(("active", "escalated")),
        )
        .first()
    )
    if existing:
        raise ValueError(
            f"Twin '{twin.name}' is already in this meeting "
            f"(participant {existing.id})"
        )

    # If for_user_id wasn't passed, infer the twin's primary owner
    if for_user_id is None:
        owner = (
            db.query(PlatformUser)
            .filter(PlatformUser.twin_id == twin_id)
            .first()
        )
        if owner:
            for_user_id = owner.id

    participant = MeetingParticipant(
        meeting_id=meeting_id,
        twin_id=twin_id,
        participant_type="twin_proxy" if for_user_id else "twin",
        for_user_id=for_user_id,
        meeting_authority=authority,
        authorized_by_user_id=authorized_by_user_id,
        authorized_at=datetime.utcnow() if authorized_by_user_id else None,
        session_status="active",
        joined_at=datetime.utcnow(),
    )
    db.add(participant)

    # Mark twin as in_meeting
    twin_service.set_status(db, twin_id, "in_meeting")

    twin_service.log_activity(
        db, twin_id, "meeting_joined",
        f"Joined meeting '{meeting.title}' (authority={authority})",
        {
            "meeting_id": str(meeting_id),
            "authority": authority,
            "for_user_id": str(for_user_id) if for_user_id else None,
            "reason": reason,
        },
    )
    db.flush()
    return participant


def leave_meeting(
    db: Session,
    twin_id: UUID,
    meeting_id: UUID,
    generate_summary: bool = True,
    reason: Optional[str] = None,
) -> dict:
    """End the twin's participation. Optionally generates a meeting summary
    and DMs the worker. Promotes commitments into TwinTask rows for review.
    """
    participant = (
        db.query(MeetingParticipant)
        .filter(
            MeetingParticipant.meeting_id == meeting_id,
            MeetingParticipant.twin_id == twin_id,
            MeetingParticipant.session_status == "active",
        )
        .first()
    )
    if not participant:
        raise ValueError("No active participant row for this twin/meeting")

    participant.session_status = "ended"
    participant.left_at = datetime.utcnow()

    # Reset twin status
    twin_service.set_status(db, twin_id, "idle")

    twin_service.log_activity(
        db, twin_id, "meeting_left",
        f"Left meeting (reason={reason or 'normal'}, commitments={participant.commitment_count})",
        {"meeting_id": str(meeting_id), "reason": reason},
    )

    summary_data = None
    review_tasks_created = 0

    if generate_summary:
        summary_data = _generate_post_meeting_summary(db, meeting_id, participant)
        review_tasks_created = _promote_commitments_to_tasks(db, participant)
        _notify_worker(db, participant, summary_data, review_tasks_created)

    db.flush()
    return {
        "participant_id": str(participant.id),
        "meeting_id": str(meeting_id),
        "session_status": participant.session_status,
        "left_at": participant.left_at.isoformat() if participant.left_at else None,
        "commitments_logged": participant.commitment_count,
        "review_tasks_created": review_tasks_created,
        "summary": summary_data,
    }


def list_active_sessions(db: Session, twin_id: UUID) -> list[dict]:
    """All meetings this twin is currently attending."""
    rows = (
        db.query(MeetingParticipant, Meeting)
        .join(Meeting, Meeting.id == MeetingParticipant.meeting_id)
        .filter(
            MeetingParticipant.twin_id == twin_id,
            MeetingParticipant.session_status == "active",
        )
        .all()
    )
    return [
        {
            "participant_id": str(p.id),
            "meeting_id": str(m.id),
            "meeting_title": m.title,
            "twin_id": str(p.twin_id),
            "authority": p.meeting_authority,
            "session_status": p.session_status,
            "joined_at": p.joined_at.isoformat() if p.joined_at else None,
            "commitment_count": p.commitment_count,
            "escalation_count": p.escalation_count,
            "is_voice": m.is_voice,
        }
        for p, m in rows
    ]


def escalate(
    db: Session,
    twin_id: UUID,
    meeting_id: UUID,
    question: str,
    stall_phrase_kr: Optional[str] = None,
) -> dict:
    """Twin is unsure mid-meeting. DM the worker. Increment escalation counter.
    Sprint 2 will also push the stall phrase to MeloTTS so the twin says
    "Let me check with [worker]" aloud while waiting.
    """
    participant = (
        db.query(MeetingParticipant)
        .filter(
            MeetingParticipant.meeting_id == meeting_id,
            MeetingParticipant.twin_id == twin_id,
            MeetingParticipant.session_status == "active",
        )
        .first()
    )
    if not participant:
        raise ValueError("No active participant row")

    participant.escalation_count = (participant.escalation_count or 0) + 1
    participant.session_status = "escalated"

    # DM the worker
    if participant.for_user_id:
        owner = db.query(PlatformUser).filter(PlatformUser.id == participant.for_user_id).first()
        if owner and owner.twin_id:
            dm = DirectMessage(
                twin_id=owner.twin_id,
                sender_type="worker",  # twin -> worker channel reuse
                content=f"[Mid-meeting escalation] {question}",
            )
            db.add(dm)

    twin_service.log_activity(
        db, twin_id, "meeting_escalation",
        f"Twin escalated mid-meeting: {question[:120]}",
        {
            "meeting_id": str(meeting_id),
            "question": question,
            "stall_phrase_kr": stall_phrase_kr,
        },
    )
    db.flush()

    return {
        "escalated": True,
        "participant_id": str(participant.id),
        "escalation_count": participant.escalation_count,
        "stall_phrase_kr": stall_phrase_kr or "잠시만요, 확인 후 답변드리겠습니다.",
    }


# ---------------------------------------------------------------------------
#  Utterance recording (called by voice pipeline in Sprint 2)
# ---------------------------------------------------------------------------

def record_utterance(
    db: Session,
    meeting_id: UUID,
    participant_id: Optional[UUID],
    speaker_role: str,
    text: str,
    speaker_label: Optional[str] = None,
    text_korean: Optional[str] = None,
    audio_url: Optional[str] = None,
    is_commitment: bool = False,
    requires_worker_review: bool = False,
    confidence: Optional[float] = None,
    latency_ms: Optional[int] = None,
) -> MeetingUtterance:
    """Append one utterance to the meeting audit trail. Called per-turn
    by the voice pipeline (or by text-meeting endpoints for non-voice).
    """
    utterance = MeetingUtterance(
        meeting_id=meeting_id,
        participant_id=participant_id,
        speaker_role=speaker_role,
        speaker_label=speaker_label,
        text=text,
        text_korean=text_korean,
        audio_url=audio_url,
        spoken_at=datetime.utcnow(),
        is_commitment=is_commitment,
        requires_worker_review=requires_worker_review,
        confidence=confidence,
        latency_ms=latency_ms,
    )
    db.add(utterance)

    # Bump participant counters
    if participant_id and is_commitment:
        participant = db.query(MeetingParticipant).filter(MeetingParticipant.id == participant_id).first()
        if participant:
            participant.commitment_count = (participant.commitment_count or 0) + 1

    db.flush()
    return utterance


# ---------------------------------------------------------------------------
#  Internal: post-meeting flow
# ---------------------------------------------------------------------------

def _generate_post_meeting_summary(
    db: Session, meeting_id: UUID, participant: MeetingParticipant
) -> Optional[dict]:
    """Reuse meeting_recorder.generate_meeting_summary on the utterance log."""
    utterances = (
        db.query(MeetingUtterance)
        .filter(MeetingUtterance.meeting_id == meeting_id)
        .order_by(MeetingUtterance.spoken_at.asc())
        .all()
    )
    if not utterances:
        return None

    transcript_lines = []
    speakers_seen = set()
    for u in utterances:
        label = u.speaker_label or u.speaker_role
        speakers_seen.add(label)
        transcript_lines.append(f"{label}: {u.text}")
    transcript = "\n".join(transcript_lines)

    meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
    title = meeting.title if meeting else "Meeting"

    try:
        from services.meeting_recorder import generate_meeting_summary
        return generate_meeting_summary(
            db, transcript, title, meeting_id, list(speakers_seen),
        )
    except Exception as e:
        return {"error": f"Summary generation failed: {e}"}


def _promote_commitments_to_tasks(db: Session, participant: MeetingParticipant) -> int:
    """Turn every is_commitment=True utterance into a TwinTask flagged for
    worker review. Returns the count created.
    """
    commitments = (
        db.query(MeetingUtterance)
        .filter(
            MeetingUtterance.participant_id == participant.id,
            MeetingUtterance.is_commitment == True,  # noqa: E712 (SQLAlchemy needs ==)
        )
        .all()
    )
    created = 0
    for c in commitments:
        task = TwinTask(
            twin_id=participant.twin_id,
            title=f"[Meeting commitment] {c.text[:200]}",
            description=(
                f"Your twin agreed to this during a meeting on "
                f"{c.spoken_at.strftime('%Y-%m-%d %H:%M') if c.spoken_at else 'an earlier date'}. "
                f"Review and accept or correct."
            ),
            status="todo",
            priority="high",
            assigned_by="twin_meeting",
            assigned_in_meeting_id=participant.meeting_id,
            needs_review=True,
            review_status="pending",
        )
        db.add(task)
        created += 1
    return created


def _notify_worker(
    db: Session,
    participant: MeetingParticipant,
    summary_data: Optional[dict],
    review_tasks_created: int,
) -> None:
    """DM the worker that their twin attended a meeting on their behalf."""
    if not participant.for_user_id:
        return
    owner = db.query(PlatformUser).filter(PlatformUser.id == participant.for_user_id).first()
    if not owner or not owner.twin_id:
        return

    meeting = db.query(Meeting).filter(Meeting.id == participant.meeting_id).first()
    title = meeting.title if meeting else "a meeting"

    body = (
        f"Your twin attended '{title}' on your behalf. "
        f"Commitments made: {participant.commitment_count}. "
        f"{review_tasks_created} item(s) need your review."
    )
    if summary_data and summary_data.get("korean_summary"):
        body += f"\n\n요약: {summary_data['korean_summary'][:500]}"

    dm = DirectMessage(
        twin_id=owner.twin_id,
        sender_type="worker",
        content=body,
    )
    db.add(dm)
