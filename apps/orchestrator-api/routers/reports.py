"""
VIP AI Platform — Report Router
POST /reports/compose/daily, /reports/compose/weekly, /reports/compose/alert
GET /reports/{id}, GET /reports/{id}/markdown
"""

import os
from uuid import UUID
from pydantic import BaseModel, Field
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from db.base import get_db
from db.models import OrchReport
from services import report_service
from services.api_security import rate_limit_compose

router = APIRouter(prefix="/reports", tags=["reports"])


class ComposeBody(BaseModel):
    delivery_channel: str = Field(default="web")
    trace_id: str = Field(default="system")
    hours_back: int = Field(default=24, ge=1, le=720, description="How many hours back to look for data")


@router.post("/compose/auto-daily", dependencies=[Depends(rate_limit_compose)])
def trigger_auto_daily(db: Session = Depends(get_db)):
    """Manually trigger the auto daily report pipeline (3 agent reports + combined)."""
    from services.scheduler_service import _auto_daily_reports
    import threading
    threading.Thread(target=_auto_daily_reports, daemon=True).start()
    return {"triggered": True, "message": "Auto daily reports running in background. Check Reports page in ~30 seconds."}


def _allowed_report_recipients() -> set[str]:
    """Allowlist of addresses the manual trigger may email. Built from
    REPORT_ALLOWED_RECIPIENTS (comma-separated) plus the configured server-side
    recipients — so an attacker cannot exfiltrate a report to an arbitrary inbox
    or abuse our SMTP to send mail to third parties."""
    allowed = {a.strip().lower() for a in (os.getenv("REPORT_ALLOWED_RECIPIENTS") or "").split(",") if a.strip()}
    for ev in ("KIWOOM_REPORT_EMAIL", "REPORT_EMAIL_TO", "SMTP_USER"):
        v = os.getenv(ev)
        if v:
            allowed.add(v.strip().lower())
    return allowed


@router.post("/compose/kiwoom", dependencies=[Depends(rate_limit_compose)])
def trigger_kiwoom_report(email: Optional[str] = Query(None, description="Optional recipient for the .docx email — must be on the REPORT_ALLOWED_RECIPIENTS allowlist (or a configured server recipient). Scheduled run uses KIWOOM_REPORT_EMAIL env."), db: Session = Depends(get_db)):
    """Manually trigger the Kiwoom daily market report (also runs 6:30 AM KST).
    Pass ?email=<addr> to send the Word attachment to an ALLOWLISTED address."""
    if email:
        if email.strip().lower() not in _allowed_report_recipients():
            raise HTTPException(403, "recipient not allowed — add it to REPORT_ALLOWED_RECIPIENTS env")
        email = email.strip()
    from services.scheduler_service import _kiwoom_daily_report
    import threading
    threading.Thread(target=lambda: _kiwoom_daily_report(email_override=email), daemon=True).start()
    return {"triggered": True, "email": email or "(env KIWOOM_REPORT_EMAIL)",
            "message": "Kiwoom daily report running in background. Check Reports → Kiwoom in ~30s."}


@router.get("/email-config")
def email_config():
    """Diagnostic: report whether the email sender is configured — BOOLEANS ONLY.
    Returns no addresses, host, or recipient values (avoids info disclosure on
    this unauthenticated route); only the sender domain is surfaced to help
    confirm the right account, and the password is never read."""
    from services import report_email
    user = os.getenv("SMTP_USER") or ""
    return {
        "smtp_configured": report_email.is_configured(),
        "smtp_host_set": bool(os.getenv("SMTP_HOST")),
        "sender_set": bool(user),
        "sender_domain": (user.split("@")[-1] if "@" in user else None),
        "password_set": bool(os.getenv("SMTP_PASSWORD")),
        "from_name_set": bool(os.getenv("SMTP_FROM_NAME")),
        "use_tls": os.getenv("SMTP_USE_TLS", "1"),
        "recipient_set": bool(os.getenv("KIWOOM_REPORT_EMAIL")),
        "allowed_recipient_count": len(_allowed_report_recipients()),
        "note": "If smtp_configured is false, set SMTP_HOST / SMTP_USER / "
                "SMTP_PASSWORD (Gmail app password) on Render.",
    }


@router.post("/compose/daily", dependencies=[Depends(rate_limit_compose)])
def compose_daily(body: ComposeBody, db: Session = Depends(get_db)):
    """Compose a daily executive summary from the last 24h of task runs."""
    return report_service.compose_report(
        db, report_type="daily_summary",
        hours_back=body.hours_back, delivery_channel=body.delivery_channel, trace_id=body.trace_id,
    )


@router.post("/compose/weekly", dependencies=[Depends(rate_limit_compose)])
def compose_weekly(body: ComposeBody, db: Session = Depends(get_db)):
    """Compose a weekly summary from the last 168h of task runs."""
    return report_service.compose_report(
        db, report_type="weekly_summary",
        hours_back=max(body.hours_back, 168), delivery_channel=body.delivery_channel, trace_id=body.trace_id,
    )


@router.post("/compose/alert", dependencies=[Depends(rate_limit_compose)])
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


@router.post("/compose/cross-agent", dependencies=[Depends(rate_limit_compose)])
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


@router.delete("/{report_id}")
def delete_report(report_id: UUID, db: Session = Depends(get_db)):
    """Delete a report."""
    report = db.query(OrchReport).filter(OrchReport.id == report_id).first()
    if not report:
        raise HTTPException(404, "Report not found")
    db.delete(report)
    db.commit()
    return {"deleted": True, "id": str(report_id)}


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
