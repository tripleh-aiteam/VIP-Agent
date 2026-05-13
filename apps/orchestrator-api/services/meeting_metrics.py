"""
VIP AI Platform — Meeting Metrics (Sprint 6)
Read-only aggregation queries over meeting_participants + meeting_utterances
to feed an ops dashboard. No state of its own; everything is computed on
demand from the existing audit tables.

Surfaces:
- per-twin: total meetings attended, commitments made, escalations,
            avg STT latency, avg TTS latency
- per-day: meetings per day for sparklines
- system-wide: totals + rate limits in effect
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from sqlalchemy import func, case
from sqlalchemy.orm import Session

from db.models import (
    DigitalTwin, Meeting, MeetingParticipant, MeetingUtterance,
    WorkerVoiceProfile,
)


def per_twin_summary(db: Session, twin_id: UUID, since_days: int = 30) -> dict:
    """One twin's meeting health over the last `since_days`."""
    twin = db.query(DigitalTwin).filter(DigitalTwin.id == twin_id).first()
    if not twin:
        return {}

    cutoff = datetime.utcnow() - timedelta(days=since_days)

    participants = (
        db.query(MeetingParticipant)
        .filter(
            MeetingParticipant.twin_id == twin_id,
            MeetingParticipant.joined_at >= cutoff,
        )
        .all()
    )

    meetings_attended = len(participants)
    total_commitments = sum(p.commitment_count or 0 for p in participants)
    total_escalations = sum(p.escalation_count or 0 for p in participants)
    active_now = sum(1 for p in participants if p.session_status in ("active", "escalated"))

    # Average twin TTS latency (where speaker_role == 'twin' AND latency_ms is set)
    twin_latency = (
        db.query(func.avg(MeetingUtterance.latency_ms))
        .filter(
            MeetingUtterance.speaker_role == "twin",
            MeetingUtterance.spoken_at >= cutoff,
            MeetingUtterance.latency_ms.isnot(None),
            MeetingUtterance.participant_id.in_([p.id for p in participants]) if participants else False,
        )
        .scalar()
    )

    # Average STT latency (external speakers in same meetings)
    meeting_ids = [str(p.meeting_id) for p in participants]
    stt_latency = None
    if meeting_ids:
        stt_latency = (
            db.query(func.avg(MeetingUtterance.latency_ms))
            .filter(
                MeetingUtterance.speaker_role == "external",
                MeetingUtterance.spoken_at >= cutoff,
                MeetingUtterance.latency_ms.isnot(None),
                MeetingUtterance.meeting_id.in_(meeting_ids),
            )
            .scalar()
        )

    return {
        "twin_id": str(twin_id),
        "twin_name": twin.name,
        "twin_role": twin.role,
        "window_days": since_days,
        "meetings_attended": meetings_attended,
        "active_now": active_now,
        "total_commitments": total_commitments,
        "total_escalations": total_escalations,
        "commitment_rate_per_meeting": round(total_commitments / max(meetings_attended, 1), 2),
        "escalation_rate_per_meeting": round(total_escalations / max(meetings_attended, 1), 2),
        "avg_twin_tts_latency_ms": int(twin_latency) if twin_latency else None,
        "avg_stt_latency_ms": int(stt_latency) if stt_latency else None,
    }


def system_wide(db: Session, since_days: int = 30) -> dict:
    """Aggregate every twin's activity for the admin dashboard."""
    cutoff = datetime.utcnow() - timedelta(days=since_days)

    total_meetings = (
        db.query(func.count(func.distinct(MeetingParticipant.meeting_id)))
        .filter(MeetingParticipant.joined_at >= cutoff)
        .scalar() or 0
    )
    total_twins = (
        db.query(func.count(func.distinct(MeetingParticipant.twin_id)))
        .filter(MeetingParticipant.joined_at >= cutoff)
        .scalar() or 0
    )
    total_utterances = (
        db.query(func.count(MeetingUtterance.id))
        .filter(MeetingUtterance.spoken_at >= cutoff)
        .scalar() or 0
    )
    total_commitments = (
        db.query(func.count(MeetingUtterance.id))
        .filter(
            MeetingUtterance.spoken_at >= cutoff,
            MeetingUtterance.is_commitment == True,  # noqa: E712
        )
        .scalar() or 0
    )
    total_escalations = (
        db.query(func.sum(MeetingParticipant.escalation_count))
        .filter(MeetingParticipant.joined_at >= cutoff)
        .scalar() or 0
    )
    active_now = (
        db.query(func.count(MeetingParticipant.id))
        .filter(MeetingParticipant.session_status.in_(("active", "escalated")))
        .scalar() or 0
    )
    voice_profiles_ready = (
        db.query(func.count(WorkerVoiceProfile.id))
        .filter(WorkerVoiceProfile.status == "ready")
        .scalar() or 0
    )

    return {
        "window_days": since_days,
        "total_meetings_with_twin": int(total_meetings),
        "total_twins_attending": int(total_twins),
        "total_utterances": int(total_utterances),
        "total_commitments": int(total_commitments),
        "total_escalations": int(total_escalations),
        "active_sessions_now": int(active_now),
        "voice_profiles_ready": int(voice_profiles_ready),
    }


def recent_escalations(db: Session, limit: int = 10) -> list[dict]:
    """Most recent escalations across all twins — for the ops review list."""
    rows = (
        db.query(MeetingParticipant, DigitalTwin, Meeting)
        .join(DigitalTwin, DigitalTwin.id == MeetingParticipant.twin_id)
        .join(Meeting, Meeting.id == MeetingParticipant.meeting_id)
        .filter(MeetingParticipant.escalation_count > 0)
        .order_by(MeetingParticipant.left_at.desc().nulls_last())
        .limit(limit)
        .all()
    )
    return [
        {
            "participant_id": str(p.id),
            "meeting_id": str(m.id),
            "meeting_title": m.title,
            "twin_id": str(t.id),
            "twin_name": t.name,
            "escalation_count": p.escalation_count,
            "commitment_count": p.commitment_count,
            "authority": p.meeting_authority,
            "session_status": p.session_status,
            "joined_at": p.joined_at.isoformat() if p.joined_at else None,
            "left_at": p.left_at.isoformat() if p.left_at else None,
        }
        for p, t, m in rows
    ]
