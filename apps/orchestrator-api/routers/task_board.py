"""
VIP AI Platform — Task Board Router
Kanban board across all twins: To Do → In Progress → Review → Done.
"""

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from db.base import get_db
from db.models import TwinTask, DigitalTwin
from services import twin_service

router = APIRouter(prefix="/task-board", tags=["task-board"])


class TaskCreateBody(BaseModel):
    twin_id: UUID
    title: str
    description: Optional[str] = None
    priority: str = "medium"
    deadline: Optional[str] = None


class TaskUpdateBody(BaseModel):
    status: Optional[str] = None
    result_text: Optional[str] = None


class TaskReviewBody(BaseModel):
    review_status: str = Field(..., description="approved | rejected")
    review_comment: Optional[str] = None


@router.get("")
def get_task_board(
    status: Optional[str] = None,
    twin_id: Optional[str] = None,
    priority: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Get all tasks across all twins for Kanban board."""
    query = db.query(TwinTask).join(DigitalTwin, TwinTask.twin_id == DigitalTwin.id)
    if status:
        query = query.filter(TwinTask.status == status)
    if twin_id:
        query = query.filter(TwinTask.twin_id == twin_id)
    if priority:
        query = query.filter(TwinTask.priority == priority)

    tasks = query.order_by(TwinTask.created_at.desc()).all()

    return [
        {
            "id": str(t.id),
            "twin_id": str(t.twin_id),
            "twin_name": t.twin.name if t.twin else "Unknown",
            "twin_role": t.twin.role if t.twin else "",
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
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "started_at": t.started_at.isoformat() if t.started_at else None,
            "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        }
        for t in tasks
    ]


@router.get("/stats")
def get_task_stats(db: Session = Depends(get_db)):
    """Task counts by status, priority, and twin."""
    tasks = db.query(TwinTask).all()

    by_status = {}
    by_priority = {}
    by_twin = {}
    overdue = 0

    from datetime import datetime
    now = datetime.utcnow()

    for t in tasks:
        by_status[t.status] = by_status.get(t.status, 0) + 1
        by_priority[t.priority] = by_priority.get(t.priority, 0) + 1

        twin_name = t.twin.name if t.twin else "Unknown"
        if twin_name not in by_twin:
            by_twin[twin_name] = {"total": 0, "done": 0}
        by_twin[twin_name]["total"] += 1
        if t.status == "done":
            by_twin[twin_name]["done"] += 1

        if t.deadline and t.deadline < now and t.status not in ("done", "review"):
            overdue += 1

    return {
        "total": len(tasks),
        "by_status": by_status,
        "by_priority": by_priority,
        "by_twin": by_twin,
        "overdue": overdue,
    }


@router.get("/review-queue")
def get_review_queue(db: Session = Depends(get_db)):
    """All tasks needing boss review."""
    tasks = (
        db.query(TwinTask)
        .filter(TwinTask.needs_review == True, TwinTask.review_status == "pending")
        .order_by(TwinTask.completed_at.desc())
        .all()
    )
    return [
        {
            "id": str(t.id),
            "twin_id": str(t.twin_id),
            "twin_name": t.twin.name if t.twin else "Unknown",
            "title": t.title,
            "description": t.description,
            "priority": t.priority,
            "result_text": t.result_text,
            "result_json": t.result_json,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        }
        for t in tasks
    ]


@router.post("/tasks", status_code=201)
def create_task(body: TaskCreateBody, db: Session = Depends(get_db)):
    """Create a new task and assign to a twin."""
    twin = twin_service.get_twin(db, body.twin_id)
    if not twin:
        raise HTTPException(status_code=404, detail="Twin not found")

    from datetime import datetime
    deadline = None
    if body.deadline:
        try:
            deadline = datetime.fromisoformat(body.deadline)
        except ValueError:
            pass

    task = twin_service.create_task(
        db=db,
        twin_id=body.twin_id,
        title=body.title,
        description=body.description,
        priority=body.priority,
        deadline=deadline,
    )
    db.commit()
    return {"created": True, "id": str(task.id), "title": task.title, "twin": twin.name}


@router.patch("/tasks/{task_id}")
def update_task(task_id: UUID, body: TaskUpdateBody, db: Session = Depends(get_db)):
    """Update task status (move on Kanban board)."""
    task = twin_service.update_task_status(
        db, task_id,
        status=body.status,
        result_text=body.result_text,
    )
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    db.commit()
    return {"updated": True, "id": str(task.id), "status": task.status}


@router.post("/tasks/{task_id}/review")
def review_task(task_id: UUID, body: TaskReviewBody, db: Session = Depends(get_db)):
    """Boss approves or rejects a twin's work."""
    task = twin_service.review_task(
        db, task_id,
        review_status=body.review_status,
        comment=body.review_comment,
    )
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    db.commit()
    return {
        "reviewed": True,
        "id": str(task.id),
        "review_status": task.review_status,
        "task_status": task.status,
    }
