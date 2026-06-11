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
    for ev in ("KIWOOM_REPORT_EMAIL", "NEWSPAPER_REPORT_EMAIL", "YOUTUBE_REPORT_EMAIL",
               "MASTER_REPORT_EMAIL", "REPORT_EMAIL_TO", "SMTP_USER", "SMTP_EMAIL"):
        v = os.getenv(ev)
        if v:
            allowed.add(v.strip().lower())
    # The configured distribution list (REPORT_RECIPIENTS / DEFAULT_RECIPIENTS) is
    # allowlisted — these are the intended recipients, so a ?email test may target
    # one of them. This does NOT permit arbitrary third-party addresses.
    try:
        from services.report_email import default_recipients
        for r in default_recipients():
            allowed.add(r.strip().lower())
    except Exception:
        pass
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


@router.post("/compose/newspaper", dependencies=[Depends(rate_limit_compose)])
def trigger_newspaper_report(email: Optional[str] = Query(None, description="Optional recipient for the .docx email — must be on the allowlist. Scheduled run uses NEWSPAPER_REPORT_EMAIL env."), db: Session = Depends(get_db)):
    """Manually trigger the Newspaper (news analysis) report (also runs 7:00 AM KST)."""
    if email:
        if email.strip().lower() not in _allowed_report_recipients():
            raise HTTPException(403, "recipient not allowed — add it to REPORT_ALLOWED_RECIPIENTS env")
        email = email.strip()
    from services.scheduler_service import _newspaper_daily_report
    import threading
    threading.Thread(target=lambda: _newspaper_daily_report(email_override=email), daemon=True).start()
    return {"triggered": True, "email": email or "(env NEWSPAPER_REPORT_EMAIL)",
            "message": "Newspaper report running in background. Check Reports → Newspaper in ~40s."}


@router.post("/compose/youtube", dependencies=[Depends(rate_limit_compose)])
def trigger_youtube_report(email: Optional[str] = Query(None, description="Optional recipient for the .docx email — must be on the allowlist. Scheduled run uses YOUTUBE_REPORT_EMAIL env."), db: Session = Depends(get_db)):
    """Manually trigger the YouTube (video analysis) report (also runs 6:30 AM KST)."""
    if email:
        if email.strip().lower() not in _allowed_report_recipients():
            raise HTTPException(403, "recipient not allowed — add it to REPORT_ALLOWED_RECIPIENTS env")
        email = email.strip()
    from services.scheduler_service import _youtube_daily_report
    import threading
    threading.Thread(target=lambda: _youtube_daily_report(email_override=email), daemon=True).start()
    return {"triggered": True, "email": email or "(env YOUTUBE_REPORT_EMAIL)",
            "message": "YouTube report running in background. Check Reports → YouTube in ~60s."}


@router.post("/compose/master", dependencies=[Depends(rate_limit_compose)])
def trigger_master_report(email: Optional[str] = Query(None, description="Optional recipient for the .docx email — must be on the allowlist. Scheduled run uses MASTER_REPORT_EMAIL env."), db: Session = Depends(get_db)):
    """Manually trigger the Master synthesis report (consolidates the latest
    Kiwoom + Newspaper + YouTube reports). Also runs 6:50 AM KST."""
    if email:
        if email.strip().lower() not in _allowed_report_recipients():
            raise HTTPException(403, "recipient not allowed — add it to REPORT_ALLOWED_RECIPIENTS env")
        email = email.strip()
    from services.scheduler_service import _master_daily_report
    import threading
    threading.Thread(target=lambda: _master_daily_report(email_override=email), daemon=True).start()
    return {"triggered": True, "email": email or "(env MASTER_REPORT_EMAIL)",
            "message": "Master report running in background. Check Reports in ~40s."}


@router.post("/compose/all", dependencies=[Depends(rate_limit_compose)])
def trigger_all_reports(email: Optional[str] = Query(None, description="Optional single recipient (allowlisted) for a test; omit to email the full recipient list."), db: Session = Depends(get_db)):
    """On-demand 'Generate Now': build ALL 4 reports with the freshest data and
    email the consolidated set to every recipient. Runs in the background
    (~8-12 min). Used by the Reports page button."""
    if email:
        if email.strip().lower() not in _allowed_report_recipients():
            raise HTTPException(403, "recipient not allowed — add it to REPORT_ALLOWED_RECIPIENTS env")
        email = email.strip()
    from services.scheduler_service import run_all_reports_now
    import threading
    threading.Thread(target=lambda: run_all_reports_now(email_override=email), daemon=True).start()
    return {"triggered": True,
            "email": email or "(all recipients)",
            "message": "Generating all 4 reports with current data — the consolidated email "
                       "will arrive in ~8-12 minutes."}


@router.get("/email-config")
def email_config():
    """Diagnostic health-check for the report email sender — BOOLEANS ONLY, and
    gated behind EXPOSE_DIAGNOSTICS=1 (off in production → 404). Returns no
    addresses, host, recipient values, or the password."""
    if os.getenv("EXPOSE_DIAGNOSTICS") != "1":
        raise HTTPException(404, "Not found")
    from services import report_email
    return {
        "smtp_configured": report_email.is_configured(),
        "smtp_host_set": bool(os.getenv("SMTP_HOST")),  # host defaults to gmail if unset
        "sender_set": bool(report_email.sender_address()),  # SMTP_USER or SMTP_EMAIL
        "password_set": bool(os.getenv("SMTP_PASSWORD")),
        "from_name_set": bool(os.getenv("SMTP_FROM_NAME")),
        "use_tls": os.getenv("SMTP_USE_TLS", "1"),
        "recipient_set": bool(os.getenv("KIWOOM_REPORT_EMAIL")),
        "note": "Booleans only. Sender = SMTP_USER or SMTP_EMAIL; host defaults to "
                "smtp.gmail.com. If smtp_configured is false, set SMTP_EMAIL + SMTP_PASSWORD.",
    }


@router.post("/test-email", dependencies=[Depends(rate_limit_compose)])
def test_email_send():
    """Synchronous SMTP self-test: send a tiny .docx to the DEFAULT recipient
    and return the ACTUAL SMTP result (ok + reason) so delivery failures are
    visible inline (e.g. Gmail rejecting a non-App-Password). No user input —
    recipient is the server-side default only."""
    from services import report_email
    from services.report_docx import markdown_to_docx
    if not report_email.is_configured():
        return {"ok": False,
                "reason": "SMTP not configured — need SMTP_EMAIL (or SMTP_USER) + SMTP_PASSWORD",
                "sender_set": bool(report_email.sender_address()),
                "password_set": bool(os.getenv("SMTP_PASSWORD"))}
    docx = markdown_to_docx(
        "# Email Test\n\nIf you received this, report email delivery works.\n\n"
        "| Check | Result |\n|---|---|\n| SMTP | OK |",
        "TripleH Email Test", "diagnostic")
    res = report_email.send_email_with_docx(
        report_email.DEFAULT_RECIPIENT,
        "[TripleH] Email delivery test",
        "This is a delivery test from the VIP orchestrator. "
        "If you see this with the attached .docx, report email works.",
        "TripleH_Email_Test.docx", docx)
    return res


@router.post("/test-news", dependencies=[Depends(rate_limit_compose)])
def test_news_provider():
    """Diagnostic: run one live web search and report which provider answered
    (or that none is configured). Returns provider name + result count + which
    search keys are present — no query content, no secrets."""
    from services.web_search import search_web, gemini_search_models
    res = search_web("KOSPI stock market news today", num_results=3)
    try:
        gmodels = gemini_search_models()[:12]
    except Exception:
        gmodels = []
    return {
        "ok": bool(res.get("ok")),
        "provider": res.get("provider"),
        "result_count": len(res.get("results", [])),
        "error": res.get("error"),
        "gemini_models_available": gmodels,
        "keys_present": {
            "SERPER_API_KEY": bool(os.getenv("SERPER_API_KEY")),
            "TAVILY_API_KEY": bool(os.getenv("TAVILY_API_KEY")),
            "GOOGLE_CSE_KEY": bool(os.getenv("GOOGLE_CSE_KEY")),
            "GEMINI_or_GOOGLE_API_KEY": bool(os.getenv("GEMINI_API_KEY")
                                             or os.getenv("GOOGLE_API_KEY")
                                             or os.getenv("GOOGLE_GENERATIVE_AI_API_KEY")),
        },
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
