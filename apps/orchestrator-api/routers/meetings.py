"""
VIP AI Platform — Meeting Router
Create meetings, join twins, send messages, auto-generate minutes.
"""

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from db.base import get_db
from services import meeting_service
from contracts.meeting import MeetingCreate, MeetingMessageSend

router = APIRouter(prefix="/meetings", tags=["meetings"])


class JoinTwinsBody(BaseModel):
    twin_ids: list[UUID]


class QuickStartBody(BaseModel):
    title: str = "Quick All-Hands"


# ---------------------------------------------------------------------------
#  Meeting CRUD
# ---------------------------------------------------------------------------

@router.get("")
def list_meetings(db: Session = Depends(get_db)):
    """List all meetings (upcoming + recent)."""
    meetings = meeting_service.list_meetings(db)
    return [
        {
            "id": str(m.id),
            "title": m.title,
            "meeting_type": m.meeting_type,
            "status": m.status,
            "scheduled_at": m.scheduled_at.isoformat() if m.scheduled_at else None,
            "started_at": m.started_at.isoformat() if m.started_at else None,
            "ended_at": m.ended_at.isoformat() if m.ended_at else None,
            "created_by": m.created_by,
            "participant_count": len(m.participants) if m.participants else 0,
            "message_count": len(m.messages) if m.messages else 0,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in meetings
    ]


@router.get("/{meeting_id}")
def get_meeting(meeting_id: UUID, db: Session = Depends(get_db)):
    """Get meeting detail with participants."""
    meeting = meeting_service.get_meeting(db, meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    participants = meeting_service.get_participants(db, meeting_id)
    return {
        "id": str(meeting.id),
        "title": meeting.title,
        "meeting_type": meeting.meeting_type,
        "status": meeting.status,
        "started_at": meeting.started_at.isoformat() if meeting.started_at else None,
        "ended_at": meeting.ended_at.isoformat() if meeting.ended_at else None,
        "participants": participants,
    }


@router.post("", status_code=201)
def create_meeting(body: MeetingCreate, db: Session = Depends(get_db)):
    """Create a new meeting."""
    meeting = meeting_service.create_meeting(
        db, title=body.title, meeting_type=body.meeting_type.value,
        scheduled_at=body.scheduled_at,
    )
    # Auto-join specified twins
    joined = []
    if body.twin_ids:
        joined = meeting_service.join_twins(db, meeting.id, body.twin_ids)
    db.commit()
    return {
        "created": True,
        "id": str(meeting.id),
        "title": meeting.title,
        "participants": joined,
    }


# ---------------------------------------------------------------------------
#  Meeting Flow
# ---------------------------------------------------------------------------

@router.post("/{meeting_id}/start")
def start_meeting(meeting_id: UUID, db: Session = Depends(get_db)):
    """Start a meeting."""
    meeting = meeting_service.start_meeting(db, meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    db.commit()
    return {"id": str(meeting.id), "status": meeting.status, "started_at": meeting.started_at.isoformat()}


@router.post("/{meeting_id}/join")
def join_twins(meeting_id: UUID, body: JoinTwinsBody, db: Session = Depends(get_db)):
    """Add specific twins to a meeting."""
    results = meeting_service.join_twins(db, meeting_id, body.twin_ids)
    db.commit()
    return {"joined": results}


@router.post("/{meeting_id}/call-all")
def call_all_hands(meeting_id: UUID, db: Session = Depends(get_db)):
    """Summon ALL active twins to the meeting."""
    results = meeting_service.call_all_hands(db, meeting_id)
    db.commit()
    return {"joined": results}


@router.post("/{meeting_id}/end")
def end_meeting(meeting_id: UUID, db: Session = Depends(get_db)):
    """End the meeting and generate final minutes."""
    result = meeting_service.end_meeting(db, meeting_id)
    if not result:
        raise HTTPException(status_code=404, detail="Meeting not found")
    db.commit()
    return result


# ---------------------------------------------------------------------------
#  Messages
# ---------------------------------------------------------------------------

@router.post("/{meeting_id}/message")
def send_message(meeting_id: UUID, body: MeetingMessageSend, db: Session = Depends(get_db)):
    """Boss sends a message → system routes to twins → twins respond."""
    meeting = meeting_service.get_meeting(db, meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    if meeting.status != "active":
        raise HTTPException(status_code=400, detail="Meeting is not active")

    result = meeting_service.send_message(db, meeting_id, body.content)
    db.commit()
    return result


@router.get("/{meeting_id}/messages")
def get_messages(meeting_id: UUID, db: Session = Depends(get_db)):
    """Get all messages in a meeting."""
    return meeting_service.get_messages(db, meeting_id)


# ---------------------------------------------------------------------------
#  Minutes
# ---------------------------------------------------------------------------

@router.get("/{meeting_id}/minutes")
def get_minutes(meeting_id: UUID, db: Session = Depends(get_db)):
    """Get meeting minutes (auto-generated)."""
    minutes = meeting_service.get_minutes(db, meeting_id)
    if not minutes:
        # Generate on-demand
        minutes = meeting_service.generate_minutes(db, meeting_id)
        db.commit()
    return minutes


# ---------------------------------------------------------------------------
#  Quick Start
# ---------------------------------------------------------------------------

@router.post("/quick-start", status_code=201)
def quick_start(body: QuickStartBody, db: Session = Depends(get_db)):
    """Create + start + call all twins in one step."""
    result = meeting_service.quick_start_meeting(db, title=body.title)
    db.commit()
    return result
