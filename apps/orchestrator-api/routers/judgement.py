"""
VIP AI Platform — Judgement Router
POST /judgement/evaluate, GET /judgement/cases, POST /judgement/{id}/approve|reject
"""

from uuid import UUID
from pydantic import BaseModel, Field
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from db.base import get_db
from services import judgement_service

router = APIRouter(prefix="/judgement", tags=["judgement"])


class EvaluateBody(BaseModel):
    trace_id: str = Field(...)
    task_run_id: str = Field(...)
    task_type: str = Field(...)
    agent_id: str = Field(...)
    agent_output: dict[str, Any] = Field(...)
    rules: Optional[list[str]] = None
    context: Optional[dict[str, Any]] = None
    require_human_approval: bool = Field(default=False)

    model_config = {"json_schema_extra": {"examples": [
        {
            "trace_id": "tr-judge-001",
            "task_run_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "task_type": "stock_analysis",
            "agent_id": "mock-stock-agent",
            "agent_output": {
                "risk_score": 0.75,
                "market_sentiment": "bearish",
                "stocks": [
                    {"symbol": "AAPL", "recommendation": "sell", "confidence": 0.9, "price": 450},
                    {"symbol": "GOOGL", "recommendation": "buy", "confidence": 0.85, "price": 200}
                ]
            }
        }
    ]}}


class ApproveRejectBody(BaseModel):
    user_id: str = Field(default="admin")
    trace_id: str = Field(default="system")
    reason: Optional[str] = None


@router.post("/evaluate", status_code=201)
def evaluate(body: EvaluateBody, db: Session = Depends(get_db)):
    """Run full judgement on a task run's output. Creates case + approval if needed."""
    try:
        result = judgement_service.evaluate(
            db=db,
            trace_id=body.trace_id,
            task_run_id=UUID(body.task_run_id),
            task_type=body.task_type,
            agent_id=body.agent_id,
            agent_output=body.agent_output,
            rules=body.rules,
            context=body.context,
            require_human_approval=body.require_human_approval,
        )
        return result
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/cases")
def list_cases(
    decision: Optional[str] = Query(None, description="Filter: auto_approve|human_review_required|rejected|conditional_approve"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """List judgement cases with optional decision filter."""
    return judgement_service.list_cases(db, decision=decision, limit=limit)


@router.get("/cases/{case_id}")
def get_case(case_id: UUID, db: Session = Depends(get_db)):
    """Get a single judgement case with full evidence and approval requests."""
    case = judgement_service.get_case(db, case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    return case


@router.post("/cases/{case_id}/approve")
def approve(case_id: UUID, body: ApproveRejectBody, db: Session = Depends(get_db)):
    """Approve a pending judgement case. Updates task run to completed."""
    result = judgement_service.approve_case(db, case_id, body.user_id, body.trace_id)
    if not result:
        raise HTTPException(404, "Case not found")
    return result


@router.post("/cases/{case_id}/reject")
def reject(case_id: UUID, body: ApproveRejectBody, db: Session = Depends(get_db)):
    """Reject a pending judgement case. Updates task run to failed."""
    result = judgement_service.reject_case(db, case_id, body.user_id, body.trace_id, body.reason or "")
    if not result:
        raise HTTPException(404, "Case not found")
    return result
