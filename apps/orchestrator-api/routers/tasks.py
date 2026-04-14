"""
VIP AI Platform — Task Router
POST /tasks, GET /tasks/{id}, POST /tasks/{id}/dispatch
"""

from uuid import UUID
from pydantic import BaseModel, Field
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from db.base import get_db
from services import task_service

router = APIRouter(tags=["tasks"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class CreateTaskBody(BaseModel):
    trace_id: str = Field(..., description="Distributed tracing ID")
    task_type: str = Field(..., description="Must match orch_task_definitions.task_type")
    target_agent_type: str = Field(..., description="Agent type to route to")
    initiator_type: str = Field(default="user")
    initiator_id: str = Field(default="system")
    source_channel: Optional[str] = None
    priority: str = Field(default="medium")
    input_payload: dict[str, Any] = Field(default_factory=dict)


class TaskRunOut(BaseModel):
    id: str
    task_type: str | None
    trace_id: str | None
    agent_name: str | None
    status: str | None
    input_payload: dict | None
    output_payload: dict | None
    error_message: str | None
    started_at: str | None
    finished_at: str | None


def _run_to_out(run) -> TaskRunOut:
    return TaskRunOut(
        id=str(run.id),
        task_type=run.task_definition.task_type if run.task_definition else None,
        trace_id=run.trace_id,
        agent_name=run.target_agent.name if run.target_agent else None,
        status=run.status,
        input_payload=run.input_payload,
        output_payload=run.output_payload,
        error_message=run.error_message,
        started_at=run.started_at.isoformat() if run.started_at else None,
        finished_at=run.finished_at.isoformat() if run.finished_at else None,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/tasks", response_model=TaskRunOut, status_code=201)
def create_task(body: CreateTaskBody, db: Session = Depends(get_db)):
    """Create a new task run. Resolves the target agent and stores in pending state."""
    try:
        run = task_service.create_task(
            db=db,
            trace_id=body.trace_id,
            task_type=body.task_type,
            target_agent_type=body.target_agent_type,
            initiator_type=body.initiator_type,
            initiator_id=body.initiator_id,
            source_channel=body.source_channel,
            input_payload=body.input_payload,
            priority=body.priority,
        )
        return _run_to_out(run)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/tasks/{task_id}", response_model=TaskRunOut)
def get_task(task_id: UUID, db: Session = Depends(get_db)):
    """Get a task run by ID."""
    run = task_service.get_task_run(db, task_id)
    if not run:
        raise HTTPException(status_code=404, detail="Task run not found")
    return _run_to_out(run)


@router.post("/tasks/{task_id}/dispatch", response_model=TaskRunOut)
def dispatch_task(task_id: UUID, db: Session = Depends(get_db)):
    """Dispatch a pending task to its assigned agent. Retry-safe."""
    try:
        run = task_service.dispatch_task(db, task_id)
        return _run_to_out(run)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
