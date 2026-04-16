"""
VIP AI Platform — Report Router
POST /reports/compose/daily, /reports/compose/weekly, /reports/compose/alert
GET /reports/{id}, GET /reports/{id}/markdown
"""

from uuid import UUID
from pydantic import BaseModel, Field
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from db.base import get_db
from db.models import OrchReport
from services import report_service

router = APIRouter(prefix="/reports", tags=["reports"])


class ComposeBody(BaseModel):
    delivery_channel: str = Field(default="web")
    trace_id: str = Field(default="system")
    hours_back: int = Field(default=24, ge=1, le=720, description="How many hours back to look for data")


@router.post("/compose/daily")
def compose_daily(body: ComposeBody, db: Session = Depends(get_db)):
    """Compose a daily executive summary from the last 24h of task runs."""
    return report_service.compose_report(
        db, report_type="daily_summary",
        hours_back=body.hours_back, delivery_channel=body.delivery_channel, trace_id=body.trace_id,
    )


@router.post("/compose/weekly")
def compose_weekly(body: ComposeBody, db: Session = Depends(get_db)):
    """Compose a weekly summary from the last 168h of task runs."""
    return report_service.compose_report(
        db, report_type="weekly_summary",
        hours_back=max(body.hours_back, 168), delivery_channel=body.delivery_channel, trace_id=body.trace_id,
    )


@router.post("/compose/alert")
def compose_alert(body: ComposeBody, db: Session = Depends(get_db)):
    """Compose an urgent alert summary from recent task runs."""
    return report_service.compose_report(
        db, report_type="urgent_alert_summary",
        hours_back=body.hours_back, delivery_channel=body.delivery_channel, trace_id=body.trace_id,
    )


class CrossAgentReportBody(BaseModel):
    agent_types: list[str] = Field(..., description="List of agent types to include (e.g., ['asset', 'stock', 'realty'])")
    report_type: str = Field(default="cross_agent_summary")
    delivery_channel: str = Field(default="web")
    trace_id: str = Field(default="system")

    model_config = {"json_schema_extra": {"examples": [
        {
            "agent_types": ["asset", "stock", "realty"],
            "report_type": "cross_agent_summary",
            "trace_id": "tr-report-001",
        }
    ]}}


@router.post("/compose/cross-agent")
def compose_cross_agent(body: CrossAgentReportBody, db: Session = Depends(get_db)):
    """
    Compose a combined report by fetching real-time data from multiple agents via A2A.
    Each agent is queried through the A2A data request flow.
    """
    return report_service.compose_cross_agent_report(
        db,
        agent_types=body.agent_types,
        report_type=body.report_type,
        trace_id=body.trace_id,
        delivery_channel=body.delivery_channel,
    )


@router.get("/{report_id}")
def get_report(report_id: UUID, db: Session = Depends(get_db)):
    """Get a report by ID with full JSON content."""
    report = report_service.get_report(db, report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    return report


@router.get("/{report_id}/markdown", response_class=PlainTextResponse)
def get_report_markdown(report_id: UUID, db: Session = Depends(get_db)):
    """Get a report in Markdown format."""
    report = report_service.get_report(db, report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    md = report.get("content", {}).get("markdown", "# No markdown available")
    return md


@router.get("/")
def list_reports(
    report_type: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List all reports."""
    q = db.query(OrchReport)
    if report_type:
        q = q.filter(OrchReport.report_type == report_type)
    reports = q.order_by(OrchReport.created_at.desc()).limit(limit).all()
    return [
        {
            "id": str(r.id),
            "report_type": r.report_type,
            "delivery_channel": r.delivery_channel,
            "source_run_count": len(r.source_run_ids_json) if r.source_run_ids_json else 0,
            "executive_summary": (r.content_json or {}).get("executive_summary", ""),
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in reports
    ]
