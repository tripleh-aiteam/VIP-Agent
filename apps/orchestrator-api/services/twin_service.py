"""
VIP AI Platform — Digital Twin Service
CRUD operations, mode switching, knowledge management, activity logging.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from db.models import (
    DigitalTwin, TwinKnowledge, TwinActivityLog, TwinTask, TwinHandoff, WorkerStatus,
)


# ---------------------------------------------------------------------------
#  Twin CRUD
# ---------------------------------------------------------------------------

def create_twin(
    db: Session,
    name: str,
    role: str,
    department: Optional[str] = None,
    avatar_url: Optional[str] = None,
    personality_prompt: Optional[str] = None,
    skills: Optional[list] = None,
    permission_level: str = "suggest",
    linked_agent_id: Optional[UUID] = None,
) -> DigitalTwin:
    twin = DigitalTwin(
        name=name,
        role=role,
        department=department,
        avatar_url=avatar_url,
        personality_prompt=personality_prompt,
        skills=skills or [],
        permission_level=permission_level,
        linked_agent_id=linked_agent_id,
        mode="shadow",
        status="idle",
    )
    db.add(twin)
    db.flush()
    return twin


def get_twin(db: Session, twin_id: UUID) -> Optional[DigitalTwin]:
    return db.query(DigitalTwin).filter(DigitalTwin.id == twin_id).first()


def list_twins(db: Session) -> list[DigitalTwin]:
    return db.query(DigitalTwin).order_by(DigitalTwin.created_at.desc()).all()


def update_twin(db: Session, twin_id: UUID, **kwargs) -> Optional[DigitalTwin]:
    twin = get_twin(db, twin_id)
    if not twin:
        return None
    for key, value in kwargs.items():
        if value is not None and hasattr(twin, key):
            setattr(twin, key, value)
    twin.updated_at = datetime.utcnow()
    db.flush()
    return twin


def delete_twin(db: Session, twin_id: UUID) -> bool:
    twin = get_twin(db, twin_id)
    if not twin:
        return False
    db.delete(twin)
    db.flush()
    return True


# ---------------------------------------------------------------------------
#  Mode Switching
# ---------------------------------------------------------------------------

def switch_mode(db: Session, twin_id: UUID, mode: str) -> Optional[DigitalTwin]:
    twin = get_twin(db, twin_id)
    if not twin:
        return None
    old_mode = twin.mode
    twin.mode = mode
    twin.updated_at = datetime.utcnow()

    # Log mode change
    log_activity(db, twin_id, "mode_switch", f"Mode changed: {old_mode} → {mode}")
    db.flush()
    return twin


def set_status(db: Session, twin_id: UUID, status: str) -> Optional[DigitalTwin]:
    twin = get_twin(db, twin_id)
    if not twin:
        return None
    twin.status = status
    twin.updated_at = datetime.utcnow()
    db.flush()
    return twin


# ---------------------------------------------------------------------------
#  Knowledge Management
# ---------------------------------------------------------------------------

def add_knowledge(
    db: Session,
    twin_id: UUID,
    title: str,
    content: str,
    source_type: str = "document",
) -> TwinKnowledge:
    knowledge = TwinKnowledge(
        twin_id=twin_id,
        title=title,
        content=content,
        source_type=source_type,
    )
    db.add(knowledge)
    db.flush()
    return knowledge


def get_knowledge(db: Session, twin_id: UUID) -> list[TwinKnowledge]:
    return (
        db.query(TwinKnowledge)
        .filter(TwinKnowledge.twin_id == twin_id)
        .order_by(TwinKnowledge.created_at.desc())
        .all()
    )


def delete_knowledge(db: Session, knowledge_id: UUID) -> bool:
    knowledge = db.query(TwinKnowledge).filter(TwinKnowledge.id == knowledge_id).first()
    if not knowledge:
        return False
    db.delete(knowledge)
    db.flush()
    return True


# ---------------------------------------------------------------------------
#  Activity Logging
# ---------------------------------------------------------------------------

def log_activity(
    db: Session,
    twin_id: UUID,
    action_type: str,
    description: str,
    metadata: Optional[dict] = None,
) -> TwinActivityLog:
    log = TwinActivityLog(
        twin_id=twin_id,
        action_type=action_type,
        description=description,
        metadata_json=metadata or {},
        timestamp=datetime.utcnow(),
    )
    db.add(log)
    db.flush()
    return log


def get_activity(db: Session, twin_id: UUID, limit: int = 50) -> list[TwinActivityLog]:
    return (
        db.query(TwinActivityLog)
        .filter(TwinActivityLog.twin_id == twin_id)
        .order_by(TwinActivityLog.timestamp.desc())
        .limit(limit)
        .all()
    )


# ---------------------------------------------------------------------------
#  Task Management
# ---------------------------------------------------------------------------

def create_task(
    db: Session,
    twin_id: UUID,
    title: str,
    description: Optional[str] = None,
    priority: str = "medium",
    deadline: Optional[datetime] = None,
    assigned_by: str = "vip",
    meeting_id: Optional[UUID] = None,
) -> TwinTask:
    task = TwinTask(
        twin_id=twin_id,
        title=title,
        description=description,
        priority=priority,
        deadline=deadline,
        assigned_by=assigned_by,
        assigned_in_meeting_id=meeting_id,
        status="todo",
        needs_review=False,
    )
    db.add(task)
    db.flush()

    log_activity(db, twin_id, "task_assigned", f"New task: {title} (priority: {priority})")
    return task


def get_tasks(db: Session, twin_id: UUID) -> list[TwinTask]:
    return (
        db.query(TwinTask)
        .filter(TwinTask.twin_id == twin_id)
        .order_by(TwinTask.created_at.desc())
        .all()
    )


def get_all_tasks(db: Session, status: Optional[str] = None) -> list[TwinTask]:
    query = db.query(TwinTask)
    if status:
        query = query.filter(TwinTask.status == status)
    return query.order_by(TwinTask.created_at.desc()).all()


def update_task_status(
    db: Session,
    task_id: UUID,
    status: str,
    result_text: Optional[str] = None,
    result_json: Optional[dict] = None,
) -> Optional[TwinTask]:
    task = db.query(TwinTask).filter(TwinTask.id == task_id).first()
    if not task:
        return None
    task.status = status
    if status == "in_progress" and not task.started_at:
        task.started_at = datetime.utcnow()
    if status in ("review", "done"):
        task.completed_at = datetime.utcnow()
    if status == "review":
        task.needs_review = True
        task.review_status = "pending"
    if result_text:
        task.result_text = result_text
    if result_json:
        task.result_json = result_json
    db.flush()
    return task


def review_task(
    db: Session,
    task_id: UUID,
    review_status: str,
    reviewed_by: str = "vip",
    comment: Optional[str] = None,
) -> Optional[TwinTask]:
    task = db.query(TwinTask).filter(TwinTask.id == task_id).first()
    if not task:
        return None
    task.review_status = review_status
    task.reviewed_by = reviewed_by
    task.review_comment = comment
    if review_status == "approved":
        task.status = "done"

    # --- Feedback → Knowledge Loop ---
    # When rejected: save the correction so twin never repeats the mistake
    if review_status == "rejected" and comment:
        add_knowledge(
            db,
            twin_id=task.twin_id,
            title=f"Correction: {task.title}",
            content=(
                f"CORRECTION from {reviewed_by}:\n"
                f"Task: {task.title}\n"
                f"What I did wrong: {(task.result_text or '')[:300]}\n"
                f"Feedback: {comment}\n"
                f"RULE: Do NOT repeat this mistake. Follow the feedback above."
            ),
            source_type="decision",
        )
        log_activity(
            db, task.twin_id, "feedback",
            f"Learned from rejection: {comment[:80]}",
            {"task": task.title, "feedback": comment},
        )

    # When approved: save as positive reinforcement
    if review_status == "approved" and task.result_text:
        add_knowledge(
            db,
            twin_id=task.twin_id,
            title=f"Approved approach: {task.title}",
            content=(
                f"APPROVED WORK:\n"
                f"Task: {task.title}\n"
                f"What I did: {task.result_text[:300]}\n"
                f"Result: Boss approved this approach. Use similar approach for similar tasks."
            ),
            source_type="decision",
        )
        log_activity(
            db, task.twin_id, "feedback",
            f"Positive reinforcement: {task.title} approved",
            {"task": task.title},
        )

    db.flush()
    return task


# ---------------------------------------------------------------------------
#  Twin Summary (for Control Room / Dashboard)
# ---------------------------------------------------------------------------

def get_twin_summary(db: Session, twin_id: UUID) -> dict:
    twin = get_twin(db, twin_id)
    if not twin:
        return {}
    current_task = None
    if twin.current_task_id:
        ct = db.query(TwinTask).filter(TwinTask.id == twin.current_task_id).first()
        if ct:
            current_task = {"id": str(ct.id), "title": ct.title, "status": ct.status}

    last_activity = (
        db.query(TwinActivityLog)
        .filter(TwinActivityLog.twin_id == twin_id)
        .order_by(TwinActivityLog.timestamp.desc())
        .first()
    )

    return {
        "id": str(twin.id),
        "name": twin.name,
        "role": twin.role,
        "department": twin.department,
        "mode": twin.mode,
        "status": twin.status,
        "permission_level": twin.permission_level,
        "current_task": current_task,
        "last_activity": last_activity.description if last_activity else None,
        "last_active_at": last_activity.timestamp.isoformat() if last_activity else None,
    }


def get_all_twin_summaries(db: Session) -> list[dict]:
    twins = list_twins(db)
    return [get_twin_summary(db, twin.id) for twin in twins]
