"""
VIP AI Platform — Callback Router
POST /callbacks/agent-result — agents call this when they finish a task.
"""

from uuid import UUID
from pydantic import BaseModel, Field
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from db.base import get_db
from services import task_service

router = APIRouter(tags=["callbacks"])


class AgentResultBody(BaseModel):
    task_run_id: UUID = Field(...)
    trace_id: str = Field(...)
    agent_id: str = Field(...)
    status: str = Field(..., description="completed | failed")
    output_payload: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None
    summary: Optional[str] = None


@router.post("/callbacks/agent-result")
def agent_result_callback(body: AgentResultBody, db: Session = Depends(get_db)):
    """Receive task completion callback from an agent."""
    try:
        run = task_service.handle_agent_callback(
            db=db,
            task_run_id=body.task_run_id,
            trace_id=body.trace_id,
            agent_id=body.agent_id,
            status=body.status,
            output_payload=body.output_payload,
            error_message=body.error_message,
            summary=body.summary,
        )
        return {
            "accepted": True,
            "task_run_id": str(run.id),
            "final_status": run.status,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
