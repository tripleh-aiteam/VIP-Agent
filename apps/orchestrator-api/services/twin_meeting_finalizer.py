"""
VIP AI Platform — Hybrid Meeting Finalizer (Sprint 10)
On 'End Meeting' the finalizer:
  1) generates bilingual (Korean + English) summary from the meeting's
     utterances + chat messages via existing meeting_recorder service,
  2) saves the summary to every attending twin's knowledge base
     (TwinKnowledge.add_knowledge) so each twin learns from the meeting,
  3) emails each participant's worker via twin_meeting_email,
  4) marks the meeting status='ended'.

This is the 'twin portal' persistence the boss asked for: every twin's
knowledge tab will show the new meeting summary, searchable by the twin
in future conversations (already integrated with twin_brain.think()).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from db.models import (
    Meeting, MeetingParticipant, MeetingUtterance, MeetingMessage,
    DigitalTwin, PlatformUser, TwinHandoff,
)
from services import twin_service, twin_meeting_email
from services.logger import log


def finalize_meeting(
    db: Session,
    meeting_id: UUID,
    send_emails: bool = True,
    save_to_twins: bool = True,
) -> dict:
    """End the meeting + run all post-meeting persistence + delivery."""
    meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
    if not meeting:
        return {"ok": False, "reason": "Meeting not found"}

    # Build transcript from BOTH voice utterances AND chat messages
    transcript = _build_full_transcript(db, meeting_id)
    participants = _load_participants(db, meeting_id)
    participant_names = [p["name"] for p in participants if p.get("name")]

    summary = {}
    if transcript:
        try:
            from services.meeting_recorder import generate_meeting_summary
            summary = generate_meeting_summary(
                db, transcript, meeting.title, meeting_id, participant_names,
            )
        except Exception as e:
            log.warning(f"twin_meeting_finalizer: summary gen failed: {e}")
            summary = {"error": str(e)}

    saved_to_twins: list[dict] = []
    if save_to_twins and not summary.get("error"):
        for p in participants:
            try:
                _save_to_twin_knowledge(db, p["twin_id"], meeting, summary)
                saved_to_twins.append({"twin_id": p["twin_id"], "twin_name": p["name"]})
            except Exception as e:
                log.warning(f"twin_meeting_finalizer: knowledge save failed for {p['name']}: {e}")

    emails_sent: list[dict] = []
    if send_emails and twin_meeting_email.is_configured() and not summary.get("error"):
        for p in participants:
            email = p.get("worker_email")
            if not email:
                continue
            r = twin_meeting_email.send_meeting_summary_email(
                to_email=email,
                to_name=p.get("worker_name") or p.get("name", "there"),
                meeting_title=meeting.title,
                english_summary=summary.get("english_summary", ""),
                korean_summary=summary.get("korean_summary", ""),
                action_items=summary.get("action_items", []),
                meeting_link=f"/meetings/{meeting_id}/room",
            )
            emails_sent.append(r)

    # Mark meeting ended
    meeting.status = "ended"
    meeting.ended_at = datetime.utcnow()

    # End any still-active twin participants
    db.query(MeetingParticipant).filter(
        MeetingParticipant.meeting_id == meeting_id,
        MeetingParticipant.session_status.in_(("active", "escalated")),
    ).update({
        MeetingParticipant.session_status: "ended",
        MeetingParticipant.left_at: datetime.utcnow(),
    }, synchronize_session=False)

    db.commit()

    return {
        "ok": True,
        "meeting_id": str(meeting_id),
        "meeting_title": meeting.title,
        "ended_at": meeting.ended_at.isoformat(),
        "participants": participants,
        "summary": summary,
        "saved_to_twin_knowledge": saved_to_twins,
        "emails_sent": emails_sent,
        "email_configured": twin_meeting_email.is_configured(),
    }


# ---------------------------------------------------------------------------
#  Internal helpers
# ---------------------------------------------------------------------------

def _build_full_transcript(db: Session, meeting_id: UUID) -> str:
    """Concatenate every speech utterance + chat message in chronological
    order. Speech goes through MeetingUtterance, text through MeetingMessage.
    """
    lines: list[tuple[datetime, str]] = []

    for u in (
        db.query(MeetingUtterance)
        .filter(MeetingUtterance.meeting_id == meeting_id)
        .all()
    ):
        if not u.text or not u.spoken_at:
            continue
        speaker = u.speaker_label or u.speaker_role
        lines.append((u.spoken_at, f"{speaker}: {u.text}"))

    for m in (
        db.query(MeetingMessage)
        .filter(MeetingMessage.meeting_id == meeting_id)
        .all()
    ):
        if not m.content or not m.created_at:
            continue
        speaker = "VIP Boss" if m.sender_type == "vip" else "Twin"
        if m.sender_twin_id:
            twin = db.query(DigitalTwin).filter(DigitalTwin.id == m.sender_twin_id).first()
            if twin:
                speaker = f"{twin.name} (Twin)"
        lines.append((m.created_at, f"{speaker}: {m.content}"))

    lines.sort(key=lambda x: x[0])
    return "\n".join(line for _ts, line in lines)


def _load_participants(db: Session, meeting_id: UUID) -> list[dict]:
    """Return a unified attendee list: twin participants + their workers."""
    out: list[dict] = []
    parts = (
        db.query(MeetingParticipant)
        .filter(MeetingParticipant.meeting_id == meeting_id)
        .all()
    )
    for p in parts:
        twin = db.query(DigitalTwin).filter(DigitalTwin.id == p.twin_id).first()
        worker_email = None
        worker_name = None
        # Prefer the explicit for_user_id (twin attending on behalf of someone).
        owner_id = p.for_user_id
        if not owner_id and twin:
            owner = (
                db.query(PlatformUser).filter(PlatformUser.twin_id == twin.id).first()
            )
            if owner:
                owner_id = owner.id
        if owner_id:
            user = db.query(PlatformUser).filter(PlatformUser.id == owner_id).first()
            if user:
                worker_email = user.email
                worker_name = user.name
        out.append({
            "participant_id": str(p.id),
            "twin_id": str(p.twin_id),
            "name": twin.name if twin else "Unknown Twin",
            "role": twin.role if twin else None,
            "session_status": p.session_status,
            "worker_email": worker_email,
            "worker_name": worker_name,
            "is_proxy": p.participant_type == "twin_proxy",
        })
    return out


def _save_to_twin_knowledge(
    db: Session, twin_id_str: str, meeting: Meeting, summary: dict,
) -> None:
    """Save the bilingual summary as a knowledge document on the twin so
    twin_brain.think() can reference it in future conversations.
    """
    from uuid import UUID as _UUID
    twin_id = _UUID(twin_id_str)
    content_parts = [
        f"Meeting: {meeting.title}",
        f"Date: {meeting.ended_at.strftime('%Y-%m-%d %H:%M') if meeting.ended_at else 'now'}",
    ]
    if summary.get("english_summary"):
        content_parts.append("--- English ---")
        content_parts.append(summary["english_summary"])
    if summary.get("korean_summary"):
        content_parts.append("--- 한국어 ---")
        content_parts.append(summary["korean_summary"])
    if summary.get("action_items"):
        content_parts.append("--- Action Items ---")
        for ai in summary["action_items"]:
            content_parts.append(f"- {ai}")

    twin_service.add_knowledge(
        db,
        twin_id=twin_id,
        title=f"Meeting summary: {meeting.title}"[:200],
        content="\n\n".join(content_parts)[:8000],
        source_type="document",
    )
    twin_service.log_activity(
        db, twin_id, "meeting_summary_saved",
        f"Saved summary of '{meeting.title}' to knowledge",
        {"meeting_id": str(meeting.id)},
    )
