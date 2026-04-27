"""
VIP AI Platform — Control Room Service
Aggregates all twin + worker status for boss's live monitoring view.
"""

from datetime import datetime, time
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from db.models import DigitalTwin, TwinActivityLog, TwinTask, WorkerStatus, PlatformUser
from services import twin_service


# ---------------------------------------------------------------------------
#  Time helpers
# ---------------------------------------------------------------------------

def _is_working_hours() -> bool:
    """Check if current time is within default working hours (9-18 KST)."""
    from datetime import timezone, timedelta
    kst = timezone(timedelta(hours=9))
    now = datetime.now(kst)
    return 9 <= now.hour < 18 and now.weekday() < 5  # Mon-Fri, 9AM-6PM


def _get_time_display() -> dict:
    """Return current time info for Control Room header."""
    from datetime import timezone, timedelta
    kst = timezone(timedelta(hours=9))
    now = datetime.now(kst)
    working = _is_working_hours()
    return {
        "current_time": now.strftime("%I:%M %p"),
        "timezone": "KST",
        "is_working_hours": working,
        "mode_label": "Working Hours — Real Workers Active" if working else "After Hours — Twins Active",
        "day": now.strftime("%A"),
        "date": now.strftime("%Y-%m-%d"),
    }


# ---------------------------------------------------------------------------
#  Control Room Status
# ---------------------------------------------------------------------------

def get_control_room_status(db: Session) -> dict:
    """Get full control room view: all twins + workers + time info."""
    twins = twin_service.list_twins(db)
    time_info = _get_time_display()

    twin_cards = []
    for twin in twins:
        # Get current task if any
        current_task = None
        if twin.current_task_id:
            task = db.query(TwinTask).filter(TwinTask.id == twin.current_task_id).first()
            if task:
                current_task = {
                    "id": str(task.id),
                    "title": task.title,
                    "status": task.status,
                    "priority": task.priority,
                }

        # Get last activity
        last_activity = (
            db.query(TwinActivityLog)
            .filter(TwinActivityLog.twin_id == twin.id)
            .order_by(TwinActivityLog.timestamp.desc())
            .first()
        )

        # Count tasks in progress
        active_tasks = (
            db.query(TwinTask)
            .filter(TwinTask.twin_id == twin.id, TwinTask.status == "in_progress")
            .count()
        )

        twin_cards.append({
            "id": str(twin.id),
            "name": twin.name,
            "role": twin.role,
            "department": twin.department,
            "avatar_url": twin.avatar_url,
            "mode": twin.mode,
            "status": twin.status,
            "permission_level": twin.permission_level,
            "skills": twin.skills or [],
            "current_task": current_task,
            "active_tasks": active_tasks,
            "last_activity": {
                "description": last_activity.description,
                "action_type": last_activity.action_type,
                "timestamp": last_activity.timestamp.isoformat(),
            } if last_activity else None,
        })

    # Stats
    stats = {
        "total_twins": len(twins),
        "active_mode": sum(1 for t in twins if t.mode == "active"),
        "shadow_mode": sum(1 for t in twins if t.mode == "shadow"),
        "working": sum(1 for t in twins if t.status == "working"),
        "idle": sum(1 for t in twins if t.status == "idle"),
        "in_meeting": sum(1 for t in twins if t.status == "in_meeting"),
    }

    return {
        "time": time_info,
        "stats": stats,
        "twins": twin_cards,
    }


def get_twin_live_feed(db: Session, twin_id: UUID, limit: int = 30) -> list[dict]:
    """Get live activity feed for [Watch] view."""
    activities = (
        db.query(TwinActivityLog)
        .filter(TwinActivityLog.twin_id == twin_id)
        .order_by(TwinActivityLog.timestamp.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": str(a.id),
            "action_type": a.action_type,
            "description": a.description,
            "metadata": a.metadata_json,
            "timestamp": a.timestamp.isoformat(),
        }
        for a in reversed(activities)  # chronological order
    ]


def interrupt_twin(db: Session, twin_id: UUID, message: str) -> dict:
    """Boss interrupts a working twin with a message."""
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        return {"error": "Twin not found"}

    # Log the interrupt
    twin_service.log_activity(
        db, twin_id, "interrupted",
        f"Boss interrupt: {message}",
        {"interrupt_message": message},
    )

    # If twin was working, pause
    old_status = twin.status
    if twin.status == "working":
        twin_service.set_status(db, twin_id, "idle")

    db.flush()

    return {
        "twin_id": str(twin_id),
        "twin_name": twin.name,
        "previous_status": old_status,
        "current_status": "idle",
        "message_received": message,
    }


def get_everyone_summary(db: Session) -> dict:
    """Quick summary: what is everyone doing right now."""
    twins = twin_service.list_twins(db)
    summaries = []

    for twin in twins:
        last_activity = (
            db.query(TwinActivityLog)
            .filter(TwinActivityLog.twin_id == twin.id)
            .order_by(TwinActivityLog.timestamp.desc())
            .first()
        )
        if twin.status == "working" and last_activity:
            desc = f"{twin.name} ({twin.role}): {last_activity.description}"
        elif twin.status == "in_meeting":
            desc = f"{twin.name} ({twin.role}): In a meeting"
        elif twin.status == "idle":
            desc = f"{twin.name} ({twin.role}): Ready for tasks"
        else:
            desc = f"{twin.name} ({twin.role}): Offline"
        summaries.append(desc)

    time_info = _get_time_display()
    return {
        "time": time_info,
        "summary": summaries,
        "total": len(twins),
        "working": sum(1 for t in twins if t.status == "working"),
        "idle": sum(1 for t in twins if t.status == "idle"),
    }
