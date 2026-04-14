"""
VIP AI Platform — Runs & Reports Router
GET /runs, GET /reports
"""

from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from db.base import get_db
from db.models import OrchReport
from services import task_service

router = APIRouter(tags=["runs"])


@router.get("/runs")
def list_runs(
    status: Optional[str] = Query(None, description="Filter by status"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """List task runs with optional status filter."""
    runs = task_service.list_runs(db, status=status, limit=limit)
    return [
        {
            "id": str(r.id),
            "task_type": r.task_definition.task_type if r.task_definition else None,
            "trace_id": r.trace_id,
            "agent_name": r.target_agent.name if r.target_agent else None,
            "status": r.status,
            "initiator_type": r.initiator_type,
            "input_payload": r.input_payload,
            "output_payload": r.output_payload,
            "error_message": r.error_message,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        }
        for r in runs
    ]


@router.get("/reports")
def list_reports(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """List generated reports."""
    reports = db.query(OrchReport).order_by(OrchReport.created_at.desc()).limit(limit).all()
    return [
        {
            "id": str(r.id),
            "report_type": r.report_type,
            "source_run_ids": r.source_run_ids_json,
            "content": r.content_json,
            "delivery_channel": r.delivery_channel,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in reports
    ]
