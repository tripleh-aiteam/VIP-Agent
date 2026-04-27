"""
VIP AI Platform — Digital Twin Router
CRUD, mode switching, tasks, knowledge, activity, and chat endpoints.
"""

from typing import Optional
from uuid import UUID

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session

from db.base import get_db
from db.models import PlatformUser
from services import twin_service
from services import twin_brain
from contracts.twin import (
    TwinCreate, TwinUpdate, TwinModeSwitch,
    TwinTaskCreate, TwinTaskUpdate, TwinTaskReview,
    TwinKnowledgeCreate, TwinChatMessage,
)

router = APIRouter(prefix="/twins", tags=["digital-twins"])


def _check_twin_access(twin_id: UUID, x_user_email: Optional[str] = Header(None), db: Session = Depends(get_db)):
    """Block workers from accessing twins that aren't theirs."""
    if not x_user_email:
        return  # No header = boss mode
    user = db.query(PlatformUser).filter(PlatformUser.email == x_user_email).first()
    if not user:
        return
    if user.role in ("admin", "operator", "viewer"):
        return  # Boss can access any twin
    if user.role == "worker":
        if not user.twin_id or str(user.twin_id) != str(twin_id):
            raise HTTPException(status_code=403, detail="You can only access your own twin")


# ---------------------------------------------------------------------------
#  Twin CRUD
# ---------------------------------------------------------------------------

@router.get("")
def list_twins(db: Session = Depends(get_db)):
    """List all digital twins."""
    twins = twin_service.list_twins(db)
    return [
        {
            "id": str(t.id),
            "name": t.name,
            "role": t.role,
            "department": t.department,
            "avatar_url": t.avatar_url,
            "personality_prompt": t.personality_prompt,
            "skills": t.skills or [],
            "mode": t.mode,
            "permission_level": t.permission_level,
            "status": t.status,
            "current_task_id": str(t.current_task_id) if t.current_task_id else None,
            "linked_agent_id": str(t.linked_agent_id) if t.linked_agent_id else None,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        }
        for t in twins
    ]


# ---------------------------------------------------------------------------
#  Intelligence Metrics (must be before /{twin_id} to avoid route conflict)
# ---------------------------------------------------------------------------

@router.get("/intelligence/all")
def get_all_intelligence(db: Session = Depends(get_db)):
    """Get intelligence metrics for all twins (boss dashboard)."""
    from services.twin_intelligence import get_all_twins_intelligence
    return get_all_twins_intelligence(db)


@router.get("/summary/all")
def get_all_summaries(db: Session = Depends(get_db)):
    """Get summary of all twins for Control Room view."""
    return twin_service.get_all_twin_summaries(db)


@router.get("/handoff/today")
def get_today_handoffs(db: Session = Depends(get_db)):
    """Get all handoffs for today (morning report for boss)."""
    from db.models import TwinHandoff, DigitalTwin
    from datetime import datetime as dt, timedelta
    cutoff = dt.utcnow() - timedelta(hours=24)
    handoffs = db.query(TwinHandoff).filter(TwinHandoff.created_at >= cutoff).order_by(TwinHandoff.created_at.desc()).all()
    result = []
    for h in handoffs:
        twin = db.query(DigitalTwin).filter(DigitalTwin.id == h.twin_id).first()
        result.append({
            "id": str(h.id), "twin_id": str(h.twin_id), "twin_name": twin.name if twin else "Unknown",
            "twin_role": twin.role if twin else "", "date": h.date.isoformat() if h.date else None,
            "tasks_completed": h.tasks_completed or [], "tasks_pending_review": h.tasks_pending_review or [],
            "meeting_notes": h.meeting_notes or [], "overnight_summary": h.overnight_summary,
            "reviewed": h.reviewed, "reviewed_at": h.reviewed_at.isoformat() if h.reviewed_at else None,
            "created_at": h.created_at.isoformat() if h.created_at else None,
        })
    total_completed = sum(len(h.get("tasks_completed", [])) for h in result)
    total_review = sum(len(h.get("tasks_pending_review", [])) for h in result)
    unreviewed = sum(1 for h in result if not h["reviewed"])
    return {"handoffs": result, "stats": {"twins_worked": len(result), "tasks_completed": total_completed, "items_need_review": total_review, "unreviewed_handoffs": unreviewed}}


# ---------------------------------------------------------------------------
#  Twin CRUD (dynamic /{twin_id} routes below)
# ---------------------------------------------------------------------------

@router.get("/{twin_id}")
def get_twin(twin_id: UUID, db: Session = Depends(get_db), _=Depends(_check_twin_access)):
    """Get a specific digital twin's full profile."""
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        raise HTTPException(status_code=404, detail="Twin not found")
    return {
        "id": str(twin.id),
        "name": twin.name,
        "role": twin.role,
        "department": twin.department,
        "avatar_url": twin.avatar_url,
        "personality_prompt": twin.personality_prompt,
        "skills": twin.skills or [],
        "mode": twin.mode,
        "permission_level": twin.permission_level,
        "status": twin.status,
        "current_task_id": str(twin.current_task_id) if twin.current_task_id else None,
        "linked_agent_id": str(twin.linked_agent_id) if twin.linked_agent_id else None,
        "created_at": twin.created_at.isoformat() if twin.created_at else None,
        "updated_at": twin.updated_at.isoformat() if twin.updated_at else None,
    }


@router.post("", status_code=201)
def create_twin(body: TwinCreate, db: Session = Depends(get_db)):
    """Create a new digital twin."""
    twin = twin_service.create_twin(
        db=db,
        name=body.name,
        role=body.role,
        department=body.department,
        avatar_url=body.avatar_url,
        personality_prompt=body.personality_prompt,
        skills=body.skills,
        permission_level=body.permission_level.value,
        linked_agent_id=body.linked_agent_id,
    )
    db.commit()
    return {
        "created": True,
        "id": str(twin.id),
        "name": twin.name,
        "role": twin.role,
        "mode": twin.mode,
        "status": twin.status,
    }


@router.patch("/{twin_id}")
def update_twin(twin_id: UUID, body: TwinUpdate, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Update a digital twin's profile."""
    update_data = body.model_dump(exclude_none=True)
    if "permission_level" in update_data:
        update_data["permission_level"] = update_data["permission_level"].value
    twin = twin_service.update_twin(db, twin_id, **update_data)
    if not twin:
        raise HTTPException(status_code=404, detail="Twin not found")
    db.commit()
    return {"updated": True, "id": str(twin.id), "name": twin.name}


@router.delete("/{twin_id}")
def delete_twin(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Delete a digital twin."""
    success = twin_service.delete_twin(db, twin_id)
    if not success:
        raise HTTPException(status_code=404, detail="Twin not found")
    db.commit()
    return {"deleted": True, "id": str(twin_id)}


# ---------------------------------------------------------------------------
#  Mode Switching
# ---------------------------------------------------------------------------

@router.post("/{twin_id}/mode")
def switch_mode(twin_id: UUID, body: TwinModeSwitch, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Switch twin mode: shadow / active / handoff."""
    twin = twin_service.switch_mode(db, twin_id, body.mode.value)
    if not twin:
        raise HTTPException(status_code=404, detail="Twin not found")
    db.commit()
    return {"id": str(twin.id), "name": twin.name, "mode": twin.mode}


# ---------------------------------------------------------------------------
#  Tasks
# ---------------------------------------------------------------------------

@router.get("/{twin_id}/tasks")
def get_twin_tasks(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Get all tasks assigned to a twin."""
    tasks = twin_service.get_tasks(db, twin_id)
    return [
        {
            "id": str(t.id),
            "twin_id": str(t.twin_id),
            "title": t.title,
            "description": t.description,
            "status": t.status,
            "priority": t.priority,
            "deadline": t.deadline.isoformat() if t.deadline else None,
            "assigned_by": t.assigned_by,
            "needs_review": t.needs_review,
            "review_status": t.review_status,
            "review_comment": t.review_comment,
            "result_text": t.result_text,
            "result_json": t.result_json,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        }
        for t in tasks
    ]


@router.post("/{twin_id}/tasks", status_code=201)
def create_twin_task(twin_id: UUID, body: TwinTaskCreate, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Assign a new task to a twin."""
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        raise HTTPException(status_code=404, detail="Twin not found")
    task = twin_service.create_task(
        db=db,
        twin_id=twin_id,
        title=body.title,
        description=body.description,
        priority=body.priority.value,
        deadline=body.deadline,
    )
    db.commit()
    return {"created": True, "id": str(task.id), "title": task.title, "twin": twin.name}


@router.post("/{twin_id}/tasks/{task_id}/execute")
def execute_task(twin_id: UUID, task_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Make a twin actually work on and complete a task."""
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        raise HTTPException(status_code=404, detail="Twin not found")

    result = twin_brain.execute_task(db, twin_id, task_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])

    db.commit()
    return result


@router.post("/{twin_id}/execute-all")
def execute_all_tasks(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Execute all pending tasks for a twin."""
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        raise HTTPException(status_code=404, detail="Twin not found")

    results = twin_brain.execute_pending_tasks(db, twin_id)
    db.commit()
    return {"twin": twin.name, "tasks_executed": len(results), "results": results}


# ---------------------------------------------------------------------------
#  Knowledge
# ---------------------------------------------------------------------------

@router.get("/{twin_id}/knowledge")
def get_twin_knowledge(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Get all knowledge documents for a twin."""
    knowledge = twin_service.get_knowledge(db, twin_id)
    return [
        {
            "id": str(k.id),
            "twin_id": str(k.twin_id),
            "title": k.title,
            "content": k.content,
            "source_type": k.source_type,
            "created_at": k.created_at.isoformat() if k.created_at else None,
        }
        for k in knowledge
    ]


@router.post("/{twin_id}/knowledge", status_code=201)
def add_twin_knowledge(twin_id: UUID, body: TwinKnowledgeCreate, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Add a knowledge document to a twin."""
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        raise HTTPException(status_code=404, detail="Twin not found")
    knowledge = twin_service.add_knowledge(
        db=db,
        twin_id=twin_id,
        title=body.title,
        content=body.content,
        source_type=body.source_type.value,
    )
    db.commit()
    return {"created": True, "id": str(knowledge.id), "title": knowledge.title}


@router.delete("/{twin_id}/knowledge/{knowledge_id}")
def delete_twin_knowledge(twin_id: UUID, knowledge_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Delete a knowledge document."""
    success = twin_service.delete_knowledge(db, knowledge_id)
    if not success:
        raise HTTPException(status_code=404, detail="Knowledge doc not found")
    db.commit()
    return {"deleted": True, "id": str(knowledge_id)}


# ---------------------------------------------------------------------------
#  Activity (for Control Room [Watch])
# ---------------------------------------------------------------------------

@router.get("/{twin_id}/activity")
def get_twin_activity(twin_id: UUID, limit: int = 50, db: Session = Depends(get_db)):
    """Get recent activity log for a twin (used by Control Room [Watch])."""
    activities = twin_service.get_activity(db, twin_id, limit=limit)
    return [
        {
            "id": str(a.id),
            "twin_id": str(a.twin_id),
            "action_type": a.action_type,
            "description": a.description,
            "metadata": a.metadata_json,
            "timestamp": a.timestamp.isoformat() if a.timestamp else None,
        }
        for a in activities
    ]


@router.get("/{twin_id}/summary")
def get_twin_summary(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Get summary of a specific twin."""
    summary = twin_service.get_twin_summary(db, twin_id)
    if not summary:
        raise HTTPException(status_code=404, detail="Twin not found")
    return summary


# ---------------------------------------------------------------------------
#  Chat with Twin (1-on-1)
# ---------------------------------------------------------------------------

@router.post("/{twin_id}/chat")
def chat_with_twin(twin_id: UUID, body: TwinChatMessage, x_user_email: Optional[str] = Header(None), db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Send a message to a twin and get an intelligent response."""
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        raise HTTPException(status_code=404, detail="Twin not found")

    # Set twin as working while thinking
    twin_service.set_status(db, twin_id, "working")
    db.flush()

    # Detect if boss is chatting (no worker email header = boss)
    is_boss = not x_user_email
    if is_boss:
        # Save boss message as DirectMessage so worker sees it
        from db.models import DirectMessage
        dm = DirectMessage(twin_id=twin_id, sender_type="boss", content=body.message)
        db.add(dm)
        try:
            from services.twin_notifications import notify
            notify(db, twin_id, "boss_message", f"Boss chatted with your twin", body.message[:100])
        except Exception:
            pass
        db.flush()

    # Get response from twin brain
    response = twin_brain.think(db, twin_id, body.message)

    db.commit()

    return {
        "twin_id": str(twin_id),
        "twin_name": twin.name,
        "twin_role": twin.role,
        "message": body.message,
        "response": response,
    }


@router.post("/handoff/{handoff_id}/review")
def review_handoff(handoff_id: UUID, db: Session = Depends(get_db)):
    """Mark a handoff as reviewed by boss."""
    from db.models import TwinHandoff
    from datetime import datetime

    handoff = db.query(TwinHandoff).filter(TwinHandoff.id == handoff_id).first()
    if not handoff:
        raise HTTPException(status_code=404, detail="Handoff not found")

    handoff.reviewed = True
    handoff.reviewed_at = datetime.utcnow()
    db.commit()

    return {"reviewed": True, "id": str(handoff_id)}


# ---------------------------------------------------------------------------
#  Direct Messages (Boss ↔ Worker)
# ---------------------------------------------------------------------------

class SendMessageBody(BaseModel):
    content: str
    sender_type: str = "boss"   # boss | worker


@router.get("/{twin_id}/messages")
def get_messages(twin_id: UUID, limit: int = 50, db: Session = Depends(get_db)):
    """Get conversation between boss and worker for a specific twin."""
    from db.models import DirectMessage, DigitalTwin

    twin = db.query(DigitalTwin).filter(DigitalTwin.id == twin_id).first()
    if not twin:
        raise HTTPException(status_code=404, detail="Twin not found")

    messages = (
        db.query(DirectMessage)
        .filter(DirectMessage.twin_id == twin_id)
        .order_by(DirectMessage.created_at.asc())
        .limit(limit)
        .all()
    )

    return {
        "twin_id": str(twin_id),
        "twin_name": twin.name,
        "messages": [
            {
                "id": str(m.id),
                "sender_type": m.sender_type,
                "content": m.content,
                "is_read": m.is_read,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ],
        "unread_count": sum(1 for m in messages if not m.is_read),
    }


@router.post("/{twin_id}/messages")
def send_message(twin_id: UUID, body: SendMessageBody, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Send a message (boss → worker or worker → boss)."""
    from db.models import DirectMessage, DigitalTwin

    twin = db.query(DigitalTwin).filter(DigitalTwin.id == twin_id).first()
    if not twin:
        raise HTTPException(status_code=404, detail="Twin not found")

    msg = DirectMessage(
        twin_id=twin_id,
        sender_type=body.sender_type,
        content=body.content,
    )
    db.add(msg)

    # Log activity
    twin_service.log_activity(
        db, twin_id,
        "boss_message" if body.sender_type == "boss" else "worker_reply",
        f"{'Boss' if body.sender_type == 'boss' else 'Worker'}: {body.content[:80]}",
    )

    db.commit()
    return {
        "sent": True,
        "id": str(msg.id),
        "sender_type": body.sender_type,
        "twin_name": twin.name,
    }


@router.post("/{twin_id}/messages/read")
def mark_messages_read(twin_id: UUID, reader: str = "worker", db: Session = Depends(get_db)):
    """Mark all messages as read (worker reads boss messages, or boss reads worker replies)."""
    from db.models import DirectMessage

    sender_to_mark = "boss" if reader == "worker" else "worker"
    messages = (
        db.query(DirectMessage)
        .filter(DirectMessage.twin_id == twin_id, DirectMessage.sender_type == sender_to_mark, DirectMessage.is_read == False)
        .all()
    )
    for m in messages:
        m.is_read = True
    db.commit()
    return {"marked_read": len(messages)}


# ---------------------------------------------------------------------------
#  Feedback / Corrections
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
#  Reports
# ---------------------------------------------------------------------------

class BroadcastBody(BaseModel):
    message: str
    priority: str = "normal"    # normal | urgent


@router.post("/broadcast")
def broadcast_message(body: BroadcastBody, db: Session = Depends(get_db)):
    """Boss sends a message to ALL workers at once."""
    from db.models import DirectMessage, DigitalTwin
    from services.twin_notifications import notify

    twins = db.query(DigitalTwin).all()
    sent_count = 0

    for twin in twins:
        # Save as direct message
        msg = DirectMessage(
            twin_id=twin.id,
            sender_type="boss",
            content=f"{'🚨 URGENT: ' if body.priority == 'urgent' else ''}{body.message}",
        )
        db.add(msg)

        # Create notification
        notify(db, twin.id,
            "boss_message",
            f"{'🚨 ' if body.priority == 'urgent' else ''}Message from Boss",
            body.message,
        )
        sent_count += 1

    db.commit()
    return {"sent": True, "twins_notified": sent_count, "message": body.message, "priority": body.priority}


@router.get("/reports/absences")
def get_worker_absences(hours: int = 24, db: Session = Depends(get_db)):
    """Check which workers haven't logged in (absent for X hours)."""
    from services.twin_reports import check_worker_absences
    absent = check_worker_absences(db, hours_threshold=hours)
    return {"absent_workers": absent, "count": len(absent), "threshold_hours": hours}


# ---------------------------------------------------------------------------
#  Meeting Recording & Summary
# ---------------------------------------------------------------------------

class MeetingTranscriptBody(BaseModel):
    transcript: str
    meeting_title: str = "Meeting"
    participants: list[str] = []
    meeting_id: Optional[str] = None
    save_to_twin_ids: list[str] = []


@router.post("/meetings/summarize")
def summarize_meeting(body: MeetingTranscriptBody, db: Session = Depends(get_db)):
    """Generate bilingual summary (Korean + English) from meeting transcript."""
    from services.meeting_recorder import generate_meeting_summary, save_meeting_to_twin_knowledge

    meeting_uuid = UUID(body.meeting_id) if body.meeting_id else None
    result = generate_meeting_summary(
        db, body.transcript, body.meeting_title, meeting_uuid, body.participants,
    )

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    # Save to specified twins' knowledge
    for twin_id_str in body.save_to_twin_ids:
        try:
            save_meeting_to_twin_knowledge(
                db, UUID(twin_id_str), body.meeting_title,
                result.get("english_summary", ""), result.get("korean_summary", ""),
                result.get("action_items", []),
            )
        except Exception:
            pass

    db.commit()
    return result


@router.get("/reports/boss-briefing")
def get_boss_briefing(db: Session = Depends(get_db)):
    """Get daily briefing for boss — all twins overnight summary."""
    from services.twin_reports import generate_boss_briefing
    return generate_boss_briefing(db)


@router.get("/reports/monthly")
def get_monthly_comparison(db: Session = Depends(get_db)):
    """Get monthly twin comparison report (boss view)."""
    from services.twin_reports import generate_monthly_comparison
    return generate_monthly_comparison(db)


@router.get("/reports/weekly")
def get_weekly_update(db: Session = Depends(get_db)):
    """Get weekly team performance report (boss view)."""
    from services.twin_reports import generate_weekly_update
    return generate_weekly_update(db)


class WeeklyMessageBody(BaseModel):
    message: str = ""


@router.post("/reports/weekly/send")
def send_weekly_update(body: WeeklyMessageBody, db: Session = Depends(get_db)):
    """Boss generates weekly report with personal message and sends to all workers."""
    from services.twin_reports import generate_weekly_update
    report = generate_weekly_update(db, boss_message=body.message)

    # Save as direct message to all twins
    twins = db.query(DigitalTwin).all()
    from db.models import DirectMessage

    summary = f"Weekly Update from Boss:\n\n{body.message}\n\nTeam Performance: {report['company_stats']['total_tasks_completed']} tasks completed, avg progress {report['company_stats']['average_progress']}%"

    for twin in twins:
        msg = DirectMessage(
            twin_id=twin.id,
            sender_type="boss",
            content=summary,
        )
        db.add(msg)

    db.commit()
    return {"sent": True, "twins_notified": len(twins), "report": report}


# ---------------------------------------------------------------------------
#  Worker Notifications
# ---------------------------------------------------------------------------

@router.get("/{twin_id}/notifications")
def get_twin_notifications(twin_id: UUID, unread_only: bool = False, limit: int = 20, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Get notifications for a twin (task completed, etc.)."""
    from services.twin_notifications import get_notifications, get_unread_count
    notifications = get_notifications(db, twin_id, unread_only=unread_only, limit=limit)
    unread = get_unread_count(db, twin_id)
    return {
        "notifications": [
            {
                "id": str(n.id),
                "type": n.type,
                "title": n.title,
                "body": n.body,
                "is_read": n.is_read,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in notifications
        ],
        "unread_count": unread,
    }


@router.post("/{twin_id}/notifications/read-all")
def mark_all_notifications_read(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Mark all notifications as read."""
    from services.twin_notifications import mark_all_read
    count = mark_all_read(db, twin_id)
    db.commit()
    return {"marked_read": count}


@router.get("/{twin_id}/reports/weekly-self")
def get_weekly_self_report(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Get twin's weekly self-report — what it learned and achieved this week."""
    from services.twin_reports import generate_weekly_self_report
    report = generate_weekly_self_report(db, twin_id)
    if "error" in report:
        raise HTTPException(status_code=404, detail=report["error"])
    return report


class EveningHandoffBody(BaseModel):
    selected_task_ids: list[str] = []
    new_tasks: list[dict] = []
    instructions: str = ""


@router.get("/{twin_id}/reports/evening")
def get_evening_handoff(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Get evening handoff data — today's summary + unfinished tasks."""
    from services.twin_reports import get_evening_handoff_data
    return get_evening_handoff_data(db, twin_id)


@router.post("/{twin_id}/reports/evening/handoff")
def submit_evening_handoff(twin_id: UUID, body: EveningHandoffBody, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Worker submits evening handoff — select tasks + add new + instructions → twin takes over."""
    from services.twin_reports import process_evening_handoff
    result = process_evening_handoff(db, twin_id, body.selected_task_ids, body.new_tasks, body.instructions)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    db.commit()
    return result


@router.get("/{twin_id}/reports/morning")
def get_morning_report(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Get morning report for a worker — what twin did overnight + today's tasks."""
    from services.twin_reports import generate_morning_report
    report = generate_morning_report(db, twin_id)
    if "error" in report:
        raise HTTPException(status_code=404, detail=report["error"])
    return report


# ---------------------------------------------------------------------------
#  Twin Version Snapshots (from colleague.skill)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
#  AI Session Import (Claude Code, ChatGPT, Gemini)
# ---------------------------------------------------------------------------

@router.get("/claude-projects/list")
def list_claude_code_projects():
    """List all Claude Code projects on the PC."""
    from services.claude_auto_import import list_claude_projects
    return list_claude_projects()


class ClaudeAutoImportBody(BaseModel):
    project_filter: Optional[str] = None   # e.g. "c--Users-TRIPLEH-Desktop-VIP-Agent"
    hours: int = 24
    max_sessions: int = 10


@router.post("/{twin_id}/import/claude-auto")
def auto_import_claude(twin_id: UUID, body: ClaudeAutoImportBody, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Auto-import Claude Code sessions from PC files — no copy-paste needed."""
    from services.claude_auto_import import import_recent_sessions
    result = import_recent_sessions(db, twin_id, body.project_filter, body.hours, body.max_sessions)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    db.commit()
    return result


class ClaudeImportBody(BaseModel):
    session_text: str
    session_title: Optional[str] = None
    auto_extract: bool = True


class GenericAIImportBody(BaseModel):
    session_text: str
    source: str = "chatgpt"   # chatgpt | gemini | claude | bard | copilot
    session_title: Optional[str] = None


@router.post("/{twin_id}/import/claude")
def import_claude(twin_id: UUID, body: ClaudeImportBody, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Import a Claude Code session — twin learns from your coding work."""
    from services.claude_import import import_claude_session
    result = import_claude_session(db, twin_id, body.session_text, body.session_title, body.auto_extract)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    db.commit()
    return result


@router.post("/{twin_id}/import/ai-session")
def import_ai_session(twin_id: UUID, body: GenericAIImportBody, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Import a ChatGPT/Gemini/Claude web conversation."""
    from services.claude_import import import_generic_ai_session
    result = import_generic_ai_session(db, twin_id, body.session_text, body.source, body.session_title)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    db.commit()
    return result


class SnapshotBody(BaseModel):
    version_name: str
    notes: str = ""


@router.get("/{twin_id}/snapshots")
def list_twin_snapshots(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """List all snapshots for a twin."""
    from services.twin_snapshots import list_snapshots
    return list_snapshots(db, twin_id)


@router.post("/{twin_id}/snapshots", status_code=201)
def create_twin_snapshot(twin_id: UUID, body: SnapshotBody, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Create a snapshot of current twin state (save/backup)."""
    from services.twin_snapshots import create_snapshot
    snapshot = create_snapshot(db, twin_id, body.version_name, body.notes)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Twin not found")
    db.commit()
    return {
        "created": True,
        "id": str(snapshot.id),
        "version_name": snapshot.version_name,
        "knowledge_count": snapshot.knowledge_count,
        "intelligence_pct": snapshot.intelligence_pct,
    }


@router.post("/{twin_id}/snapshots/{snapshot_id}/restore")
def restore_twin_snapshot(twin_id: UUID, snapshot_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Restore twin to a previous snapshot."""
    from services.twin_snapshots import restore_snapshot
    result = restore_snapshot(db, snapshot_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    db.commit()
    return result


@router.delete("/{twin_id}/snapshots/{snapshot_id}")
def delete_twin_snapshot(twin_id: UUID, snapshot_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Delete a snapshot."""
    from services.twin_snapshots import delete_snapshot
    success = delete_snapshot(db, snapshot_id)
    if not success:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    db.commit()
    return {"deleted": True}


@router.post("/{twin_id}/self-improve")
def run_self_improvement(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Manually trigger self-improvement cycle for a twin."""
    from services.twin_self_improve import run_self_improvement_cycle
    result = run_self_improvement_cycle(db, twin_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    db.commit()
    return result


@router.get("/{twin_id}/self-improve/history")
def get_self_improvement_history(twin_id: UUID, limit: int = 20, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Get recent self-improvement activities."""
    from db.models import TwinActivityLog
    activities = (
        db.query(TwinActivityLog)
        .filter(TwinActivityLog.twin_id == twin_id, TwinActivityLog.action_type == "self_improve")
        .order_by(TwinActivityLog.timestamp.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": str(a.id),
            "description": a.description,
            "metadata": a.metadata_json,
            "timestamp": a.timestamp.isoformat(),
        }
        for a in activities
    ]


@router.get("/{twin_id}/intelligence")
def get_twin_intel(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Get intelligence metrics for a specific twin."""
    from services.twin_intelligence import get_twin_intelligence
    result = get_twin_intelligence(db, twin_id)
    if not result:
        raise HTTPException(status_code=404, detail="Twin not found")
    return result


@router.get("/{twin_id}/intelligence/timeline")
def get_intel_timeline(twin_id: UUID, days: int = 30, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Get learning growth timeline (daily data for charts)."""
    from services.twin_intelligence import get_learning_timeline
    return get_learning_timeline(db, twin_id, days=days)


# ---------------------------------------------------------------------------
#  Google Drive Integration
# ---------------------------------------------------------------------------

@router.get("/gdrive/status")
def gdrive_status():
    """Check if Google Drive integration is configured."""
    from services.gdrive_service import is_configured
    return {"configured": is_configured()}


@router.get("/{twin_id}/gdrive/auth-url")
def get_gdrive_auth_url(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Get Google OAuth URL for worker to authorize Drive access."""
    from services.gdrive_service import is_configured, get_auth_url
    if not is_configured():
        raise HTTPException(400, "Google Drive not configured. Admin needs to set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.")
    return {"auth_url": get_auth_url(str(twin_id))}


@router.post("/{twin_id}/gdrive/connect")
def connect_gdrive(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Exchange OAuth code and store tokens (called after redirect)."""
    # This will be called from the frontend callback page
    return {"status": "endpoint ready — implement callback flow"}


@router.post("/{twin_id}/gdrive/pull")
def pull_gdrive_docs(twin_id: UUID, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Manually trigger Google Drive document pull."""
    from services.gdrive_service import is_configured
    if not is_configured():
        raise HTTPException(400, "Google Drive not configured")
    # TODO: Pull docs using stored access token
    return {"status": "Google Drive pull — requires OAuth setup first", "twin_id": str(twin_id)}


class CorrectionBody(BaseModel):
    task_title: str
    what_was_wrong: str
    correct_approach: str


@router.post("/{twin_id}/correct")
def submit_correction(twin_id: UUID, body: CorrectionBody, db: Session = Depends(get_db), _ac=Depends(_check_twin_access)):
    """Worker or boss submits a correction — twin learns from it."""
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        raise HTTPException(status_code=404, detail="Twin not found")

    # Save as knowledge
    twin_service.add_knowledge(
        db, twin_id,
        title=f"Correction: {body.task_title}",
        content=(
            f"CORRECTION:\n"
            f"Task: {body.task_title}\n"
            f"What was wrong: {body.what_was_wrong}\n"
            f"Correct approach: {body.correct_approach}\n"
            f"RULE: Always follow the correct approach above."
        ),
        source_type="decision",
    )

    twin_service.log_activity(
        db, twin_id, "feedback",
        f"Learned correction: {body.task_title}",
        {"wrong": body.what_was_wrong[:100], "correct": body.correct_approach[:100]},
    )

    db.commit()
    return {"saved": True, "twin": twin.name, "correction": body.task_title}
