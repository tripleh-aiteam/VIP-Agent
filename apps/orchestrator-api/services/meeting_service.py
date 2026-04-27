"""
VIP AI Platform — Meeting Service
Create meetings, join twins, send messages, auto-generate minutes.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from db.models import (
    Meeting, MeetingParticipant, MeetingMessage, MeetingMinutes,
    DigitalTwin, TwinTask,
)
from services import twin_service, twin_brain


# ---------------------------------------------------------------------------
#  Meeting CRUD
# ---------------------------------------------------------------------------

def create_meeting(
    db: Session,
    title: str,
    meeting_type: str = "all_hands",
    scheduled_at: Optional[datetime] = None,
    created_by: str = "vip",
) -> Meeting:
    meeting = Meeting(
        title=title,
        meeting_type=meeting_type,
        status="scheduled",
        scheduled_at=scheduled_at,
        created_by=created_by,
    )
    db.add(meeting)
    db.flush()
    return meeting


def get_meeting(db: Session, meeting_id: UUID) -> Optional[Meeting]:
    return db.query(Meeting).filter(Meeting.id == meeting_id).first()


def list_meetings(db: Session) -> list[Meeting]:
    return db.query(Meeting).order_by(Meeting.created_at.desc()).all()


# ---------------------------------------------------------------------------
#  Start / End Meeting
# ---------------------------------------------------------------------------

def start_meeting(db: Session, meeting_id: UUID) -> Optional[Meeting]:
    meeting = get_meeting(db, meeting_id)
    if not meeting:
        return None
    meeting.status = "active"
    meeting.started_at = datetime.utcnow()
    db.flush()
    return meeting


def end_meeting(db: Session, meeting_id: UUID) -> Optional[dict]:
    meeting = get_meeting(db, meeting_id)
    if not meeting:
        return None
    meeting.status = "ended"
    meeting.ended_at = datetime.utcnow()

    # Resume paused tasks for all participants
    participants = (
        db.query(MeetingParticipant)
        .filter(MeetingParticipant.meeting_id == meeting_id)
        .all()
    )
    for p in participants:
        twin_service.set_status(db, p.twin_id, "idle")

    # Generate final minutes
    minutes = generate_minutes(db, meeting_id)

    db.flush()
    return {
        "meeting_id": str(meeting_id),
        "status": "ended",
        "duration_minutes": int((meeting.ended_at - meeting.started_at).total_seconds() / 60) if meeting.started_at else 0,
        "participants": len(participants),
        "minutes": minutes,
    }


# ---------------------------------------------------------------------------
#  Join Twins
# ---------------------------------------------------------------------------

def join_twins(db: Session, meeting_id: UUID, twin_ids: list[UUID]) -> list[dict]:
    results = []
    for twin_id in twin_ids:
        twin = twin_service.get_twin(db, twin_id)
        if not twin:
            continue

        # Check if already joined
        existing = (
            db.query(MeetingParticipant)
            .filter(MeetingParticipant.meeting_id == meeting_id, MeetingParticipant.twin_id == twin_id)
            .first()
        )
        if existing:
            results.append({"twin_id": str(twin_id), "name": twin.name, "status": "already_joined"})
            continue

        # Pause current task if working
        paused_task_id = twin.current_task_id

        participant = MeetingParticipant(
            meeting_id=meeting_id,
            twin_id=twin_id,
            joined_at=datetime.utcnow(),
            paused_task_id=paused_task_id,
        )
        db.add(participant)

        # Set twin status to in_meeting
        twin_service.set_status(db, twin_id, "in_meeting")
        twin_service.log_activity(db, twin_id, "meeting", f"Joined meeting")

        results.append({"twin_id": str(twin_id), "name": twin.name, "status": "joined"})

    db.flush()
    return results


def call_all_hands(db: Session, meeting_id: UUID) -> list[dict]:
    """Add ALL active twins to the meeting."""
    twins = twin_service.list_twins(db)
    twin_ids = [t.id for t in twins]
    return join_twins(db, meeting_id, twin_ids)


def get_participants(db: Session, meeting_id: UUID) -> list[dict]:
    participants = (
        db.query(MeetingParticipant)
        .filter(MeetingParticipant.meeting_id == meeting_id)
        .all()
    )
    return [
        {
            "twin_id": str(p.twin_id),
            "twin_name": p.twin.name if p.twin else "Unknown",
            "twin_role": p.twin.role if p.twin else "",
            "joined_at": p.joined_at.isoformat() if p.joined_at else None,
        }
        for p in participants
    ]


# ---------------------------------------------------------------------------
#  Messages
# ---------------------------------------------------------------------------

def send_message(
    db: Session,
    meeting_id: UUID,
    content: str,
    sender_type: str = "vip",
    sender_twin_id: Optional[UUID] = None,
) -> dict:
    """Boss sends a message. System routes to relevant twin(s) and gets responses."""

    # Save boss message
    msg = MeetingMessage(
        meeting_id=meeting_id,
        sender_type=sender_type,
        sender_twin_id=sender_twin_id,
        content=content,
    )
    db.add(msg)
    db.flush()

    # If boss sent the message, get twin responses
    responses = []
    if sender_type == "vip":
        participants = (
            db.query(MeetingParticipant)
            .filter(MeetingParticipant.meeting_id == meeting_id)
            .all()
        )

        # Detect which twin(s) to route to
        target_twins = _route_message(content, participants, db)

        for twin_id in target_twins:
            twin = twin_service.get_twin(db, twin_id)
            if not twin:
                continue

            twin_service.set_status(db, twin_id, "working")
            db.flush()

            # Twin thinks and responds
            response_text = twin_brain.think(db, twin_id, content)

            # Save twin response
            twin_msg = MeetingMessage(
                meeting_id=meeting_id,
                sender_type="twin",
                sender_twin_id=twin_id,
                content=response_text,
            )
            db.add(twin_msg)

            twin_service.set_status(db, twin_id, "in_meeting")

            responses.append({
                "twin_id": str(twin_id),
                "twin_name": twin.name,
                "twin_role": twin.role,
                "content": response_text,
            })

        db.flush()

    return {
        "message_id": str(msg.id),
        "sender_type": sender_type,
        "content": content,
        "twin_responses": responses,
    }


def get_messages(db: Session, meeting_id: UUID) -> list[dict]:
    messages = (
        db.query(MeetingMessage)
        .filter(MeetingMessage.meeting_id == meeting_id)
        .order_by(MeetingMessage.created_at.asc())
        .all()
    )
    return [
        {
            "id": str(m.id),
            "sender_type": m.sender_type,
            "sender_twin_id": str(m.sender_twin_id) if m.sender_twin_id else None,
            "sender_twin_name": m.sender_twin.name if m.sender_twin else None,
            "sender_twin_role": m.sender_twin.role if m.sender_twin else None,
            "content": m.content,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in messages
    ]


# ---------------------------------------------------------------------------
#  Message Routing
# ---------------------------------------------------------------------------

def _route_message(content: str, participants: list, db: Session) -> list[UUID]:
    """Detect which twin(s) should respond to the boss's message."""
    content_lower = content.lower()

    # Check if boss addressed everyone
    everyone_keywords = ["everyone", "all", "모두", "전원", "다들"]
    if any(kw in content_lower for kw in everyone_keywords):
        return [p.twin_id for p in participants]

    # Check if boss addressed a specific twin by name
    for p in participants:
        twin = twin_service.get_twin(db, p.twin_id)
        if twin and twin.name.lower() in content_lower:
            return [p.twin_id]

    # Check keywords for routing
    keyword_map = {
        "asset": ["asset", "portfolio", "lease", "contract", "property", "자산", "임대"],
        "stock": ["stock", "market", "kospi", "trading", "주식", "시장", "코스피"],
        "realty": ["realty", "real estate", "listing", "vacancy", "부동산", "공실"],
        "dev": ["code", "bug", "fix", "deploy", "api", "코드", "버그", "개발"],
    }

    for p in participants:
        twin = twin_service.get_twin(db, p.twin_id)
        if not twin:
            continue
        role_lower = (twin.role or "").lower()
        dept_lower = (twin.department or "").lower()

        for category, keywords in keyword_map.items():
            if any(kw in content_lower for kw in keywords):
                if category in role_lower or category in dept_lower or category in (twin.name or "").lower():
                    return [p.twin_id]

    # Default: all participants respond
    return [p.twin_id for p in participants]


# ---------------------------------------------------------------------------
#  Meeting Minutes
# ---------------------------------------------------------------------------

def generate_minutes(db: Session, meeting_id: UUID) -> dict:
    """Generate meeting minutes from conversation."""
    messages = get_messages(db, meeting_id)
    if not messages:
        return {"decisions": [], "tasks_assigned": [], "open_questions": [], "summary": "No messages in meeting."}

    # Simple extraction: look for patterns
    decisions = []
    tasks_assigned = []
    open_questions = []

    for msg in messages:
        content = msg.get("content", "")
        content_lower = content.lower()

        # Detect decisions (boss confirms)
        if msg["sender_type"] == "vip":
            if any(kw in content_lower for kw in ["yes", "approved", "do it", "go ahead", "확인", "승인", "진행"]):
                decisions.append(content[:100])
            # Detect task assignments
            if any(kw in content_lower for kw in ["by monday", "by tomorrow", "prepare", "fix", "까지", "준비", "수정"]):
                tasks_assigned.append({
                    "assigned_to": msg.get("sender_twin_name", "Unknown"),
                    "task": content[:100],
                })
            # Detect questions
            if "?" in content or "할까" in content:
                open_questions.append(content[:100])

    # Build summary
    participant_names = set()
    for msg in messages:
        if msg.get("sender_twin_name"):
            participant_names.add(msg["sender_twin_name"])

    summary = f"Meeting with {len(participant_names)} twins. {len(messages)} messages exchanged. {len(decisions)} decisions made. {len(tasks_assigned)} tasks assigned."

    # Save to DB
    minutes = MeetingMinutes(
        meeting_id=meeting_id,
        decisions=decisions,
        tasks_assigned=tasks_assigned,
        open_questions=open_questions,
        summary=summary,
    )
    db.add(minutes)
    db.flush()

    return {
        "decisions": decisions,
        "tasks_assigned": tasks_assigned,
        "open_questions": open_questions,
        "summary": summary,
    }


def get_minutes(db: Session, meeting_id: UUID) -> Optional[dict]:
    minutes = (
        db.query(MeetingMinutes)
        .filter(MeetingMinutes.meeting_id == meeting_id)
        .order_by(MeetingMinutes.generated_at.desc())
        .first()
    )
    if not minutes:
        return None
    return {
        "id": str(minutes.id),
        "decisions": minutes.decisions,
        "tasks_assigned": minutes.tasks_assigned,
        "open_questions": minutes.open_questions,
        "summary": minutes.summary,
        "generated_at": minutes.generated_at.isoformat() if minutes.generated_at else None,
    }


# ---------------------------------------------------------------------------
#  Quick Start (All-in-one)
# ---------------------------------------------------------------------------

def quick_start_meeting(db: Session, title: str = "Quick All-Hands") -> dict:
    """Create + start + call all twins in one step."""
    meeting = create_meeting(db, title=title, meeting_type="all_hands")
    start_meeting(db, meeting.id)
    joined = call_all_hands(db, meeting.id)
    db.flush()
    return {
        "meeting_id": str(meeting.id),
        "title": title,
        "status": "active",
        "participants": joined,
    }
