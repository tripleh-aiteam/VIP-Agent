"""
VIP AI Platform — A2A Router
POST /a2a/send, GET /a2a/messages, GET /a2a/messages/{id}, POST /a2a/demo/risk-flow
"""

from uuid import UUID
from pydantic import BaseModel, Field
from typing import Any, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from db.base import get_db
from services import a2a_service
from services.event_bus import is_redis_connected

router = APIRouter(prefix="/a2a", tags=["a2a"])


class SendMessageBody(BaseModel):
    trace_id: str = Field(...)
    sender_agent_id: str = Field(..., description="Agent name or UUID")
    target_agent_id: str = Field(..., description="Agent name or UUID")
    message_type: str = Field(..., description="risk_alert | data_request | report_request | report_response | feedback_request | escalation_request")
    purpose: str = Field(..., description="delegate | inform | query | escalate | ack")
    payload: dict[str, Any] = Field(default_factory=dict)
    source_task_id: Optional[str] = None
    authorization_context: Optional[dict] = None
    proof_of_intent: Optional[dict] = None

    model_config = {"json_schema_extra": {"examples": [
        {
            "trace_id": "tr-risk-001",
            "sender_agent_id": "Stock Agent",
            "target_agent_id": "Asset Agent",
            "message_type": "risk_alert",
            "purpose": "escalate",
            "proof_of_intent": {"reason": "KOSPI dropped 3.2% — portfolio exposure check needed"},
            "payload": {"alert_level": "high", "trigger": "market_drop", "index": "KOSPI", "change_pct": -3.2},
        }
    ]}}


@router.get("/status")
def a2a_status():
    """Check A2A event bus status."""
    return {
        "event_bus": "redis" if is_redis_connected() else "in-memory",
        "message_types": list(a2a_service.VALID_MESSAGE_TYPES),
        "purposes": list(a2a_service.VALID_PURPOSES),
        "high_risk_types": list(a2a_service.HIGH_RISK_TYPES),
    }


@router.post("/send", status_code=201)
def send_message(body: SendMessageBody, db: Session = Depends(get_db)):
    """Send an A2A message. Persisted, traced, and published to event bus."""
    try:
        result = a2a_service.send_message(
            db=db,
            trace_id=body.trace_id,
            sender_agent_id=body.sender_agent_id,
            target_agent_id=body.target_agent_id,
            message_type=body.message_type,
            purpose=body.purpose,
            payload=body.payload,
            source_task_id=UUID(body.source_task_id) if body.source_task_id else None,
            authorization_context=body.authorization_context,
            proof_of_intent=body.proof_of_intent,
        )
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/messages")
def list_messages(
    message_type: Optional[str] = Query(None),
    trace_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """List A2A messages with optional filters."""
    return a2a_service.list_messages(db, message_type=message_type, trace_id=trace_id, limit=limit)


@router.get("/messages/{message_id}")
def get_message(message_id: UUID, db: Session = Depends(get_db)):
    """Get a single A2A message with full envelope."""
    msg = a2a_service.get_message(db, message_id)
    if not msg:
        raise HTTPException(404, "Message not found")
    return msg


@router.post("/demo/risk-flow")
def demo_risk_flow(db: Session = Depends(get_db)):
    """
    Demo flow:
    1. Stock agent emits risk_alert to orchestrator
    2. Orchestrator requests asset review from asset agent
    3. Orchestrator requests realty exposure summary from realty agent
    All messages linked by trace_id, persisted, and audited.
    """
    trace_id = f"tr-risk-demo-{int(datetime.utcnow().timestamp())}"
    results = []

    # Step 1: Stock agent emits risk_alert
    r1 = a2a_service.send_message(
        db=db,
        trace_id=trace_id,
        sender_agent_id="Stock Agent",
        target_agent_id="Asset Agent",
        message_type="risk_alert",
        purpose="escalate",
        payload={
            "alert_level": "high",
            "trigger": "market_drop",
            "index": "KOSPI",
            "change_pct": -3.2,
            "affected_sectors": ["tech", "finance"],
        },
        proof_of_intent={"reason": "KOSPI dropped 3.2% — immediate portfolio review required"},
    )
    results.append({"step": 1, "action": "stock -> asset: risk_alert", **r1})

    # Step 2: Orchestrator requests asset review
    r2 = a2a_service.send_message(
        db=db,
        trace_id=trace_id,
        sender_agent_id="Asset Agent",
        target_agent_id="Stock Agent",
        message_type="data_request",
        purpose="query",
        payload={
            "request": "portfolio_exposure_check",
            "portfolio_ids": ["PF-1234", "PF-5678"],
            "focus_sectors": ["tech", "finance"],
        },
        proof_of_intent={"reason": "Triggered by risk_alert — checking portfolio exposure"},
    )
    results.append({"step": 2, "action": "asset -> stock: data_request (exposure check)", **r2})

    # Step 3: Orchestrator requests realty exposure summary
    r3 = a2a_service.send_message(
        db=db,
        trace_id=trace_id,
        sender_agent_id="Asset Agent",
        target_agent_id="Real Estate Agent",
        message_type="report_request",
        purpose="delegate",
        payload={
            "request": "realty_exposure_summary",
            "regions": ["Seoul-Gangnam", "Seoul-Seocho"],
            "context": "Market drop triggered review of real estate exposure",
        },
        proof_of_intent={"reason": "Cross-asset exposure check following KOSPI risk alert"},
    )
    results.append({"step": 3, "action": "asset -> realty: report_request (exposure summary)", **r3})

    return {
        "demo": "risk-flow",
        "trace_id": trace_id,
        "steps": len(results),
        "messages": results,
        "verify": {
            "all_messages": f"/a2a/messages?trace_id={trace_id}",
            "audit_events": "/runs",
            "supabase": "Check a2a_messages and audit_event_logs tables",
        },
    }
