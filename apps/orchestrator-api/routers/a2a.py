"""
VIP AI Platform — A2A Router
POST /a2a/send, POST /a2a/webhook, POST /a2a/webhook/{agent_type}/data,
GET /a2a/messages, GET /a2a/messages/{id}, POST /a2a/demo/risk-flow
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
from services.a2a_triggers import list_triggers
from services.a2a_notifications import get_notifications
from services.api_security import rate_limit_webhook, verify_api_key

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
        "triggers_count": len(list_triggers()),
    }


@router.get("/triggers")
def get_triggers():
    """List all registered event-driven triggers."""
    return {
        "triggers": list_triggers(),
        "total": len(list_triggers()),
    }


@router.get("/notifications")
def get_a2a_notifications(
    limit: int = Query(50, ge=1, le=200),
    severity: Optional[str] = Query(None, description="Filter by severity: info, warning, critical"),
    db: Session = Depends(get_db),
):
    """Get A2A notifications for dashboard display (Telegram alerts are also stored here)."""
    return get_notifications(db, limit=limit, severity=severity)


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


# ---------------------------------------------------------------------------
#  Webhook endpoints — agents POST here to send data back to orchestrator
# ---------------------------------------------------------------------------

class WebhookMessageBody(BaseModel):
    """Inbound A2A message from an external agent."""
    sender_agent_id: str = Field(..., description="Name of the sending agent")
    trace_id: str = Field(..., description="Trace ID for correlation")
    message_type: str = Field(..., description="risk_alert | data_request | report_response | feedback_request | escalation_request | report_request")
    purpose: str = Field(..., description="inform | ack | escalate | delegate | query")
    payload: dict[str, Any] = Field(default_factory=dict)
    in_reply_to: Optional[str] = Field(None, description="Message ID this is replying to")
    proof_of_intent: Optional[dict] = None

    model_config = {"json_schema_extra": {"examples": [
        {
            "sender_agent_id": "Stock Agent",
            "trace_id": "tr-risk-001",
            "message_type": "risk_alert",
            "purpose": "escalate",
            "payload": {"alert_level": "high", "trigger": "market_drop", "index": "KOSPI", "change_pct": -3.2},
            "proof_of_intent": {"reason": "KOSPI dropped 3.2% — notifying orchestrator"},
        }
    ]}}


class AgentDataBody(BaseModel):
    """Structured data push from an agent."""
    trace_id: str = Field(..., description="Trace ID for correlation")
    data_type: str = Field(..., description="Type of data: report_response | data_request | risk_alert")
    payload: dict[str, Any] = Field(default_factory=dict, description="Agent-specific data payload")
    source_message_id: Optional[str] = Field(None, description="Original message ID that triggered this data push")

    model_config = {"json_schema_extra": {"examples": [
        {
            "trace_id": "tr-stock-data-001",
            "data_type": "report_response",
            "payload": {"market_news": [{"title": "KOSPI falls 3.2%", "sentiment": "negative"}], "watchlist": ["AAPL", "005930.KS"]},
            "source_message_id": "abc123-def456",
        }
    ]}}


@router.post("/webhook", status_code=201, dependencies=[Depends(rate_limit_webhook)])
def receive_webhook(body: WebhookMessageBody, db: Session = Depends(get_db), api_key: str = Depends(verify_api_key)):
    """
    Webhook for agents to send A2A messages to the orchestrator.
    Agents call this endpoint to push alerts, replies, or data back.
    """
    try:
        result = a2a_service.receive_webhook(
            db=db,
            sender_agent_id=body.sender_agent_id,
            trace_id=body.trace_id,
            message_type=body.message_type,
            purpose=body.purpose,
            payload=body.payload,
            in_reply_to=body.in_reply_to,
            proof_of_intent=body.proof_of_intent,
        )
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/webhook/{agent_type}/data", status_code=201, dependencies=[Depends(rate_limit_webhook)])
def receive_agent_data(agent_type: str, body: AgentDataBody, db: Session = Depends(get_db), api_key: str = Depends(verify_api_key)):
    """
    Typed webhook for agents to push structured data to the orchestrator.
    URL includes agent_type (asset, stock, realty) for routing.
    """
    try:
        result = a2a_service.receive_agent_data(
            db=db,
            agent_type=agent_type,
            trace_id=body.trace_id,
            data_type=body.data_type,
            payload=body.payload,
            source_message_id=body.source_message_id,
        )
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------------------------------------------------------------------------
#  Cross-agent data request — orchestrator fetches data on behalf of one agent
# ---------------------------------------------------------------------------

class DataRequestBody(BaseModel):
    """One agent requests data from another through the orchestrator."""
    requester_agent_id: str = Field(..., description="Name of the agent requesting data")
    target_agent_type: str = Field(..., description="Type of agent to fetch data from (asset, stock, realty)")
    trace_id: str = Field(..., description="Trace ID for correlation")
    data_request: str = Field(..., description="What data is being requested (e.g., portfolio_exposure, market_risk)")
    context: Optional[dict[str, Any]] = Field(None, description="Additional context for the request")

    model_config = {"json_schema_extra": {"examples": [
        {
            "requester_agent_id": "Stock Agent",
            "target_agent_type": "asset",
            "trace_id": "tr-cross-001",
            "data_request": "portfolio_exposure",
            "context": {"focus_sectors": ["tech", "finance"]},
        }
    ]}}


@router.post("/request-data", status_code=200)
def request_data(body: DataRequestBody, db: Session = Depends(get_db)):
    """
    Cross-agent data request flow:
    1. Agent A requests data from Agent B through orchestrator
    2. Orchestrator fetches real data from Agent B via adapter
    3. Data returned to Agent A, linked by A2A message chain
    """
    try:
        result = a2a_service.request_data_from_agent(
            db=db,
            requester_agent_id=body.requester_agent_id,
            target_agent_type=body.target_agent_type,
            trace_id=body.trace_id,
            data_request=body.data_request,
            context=body.context,
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


@router.get("/messages/{message_id}/response")
def get_message_response(message_id: UUID, db: Session = Depends(get_db)):
    """Get the response data for a specific A2A message (finds matching response for requests)."""
    result = a2a_service.get_response_data(db, message_id)
    if not result:
        raise HTTPException(404, "Message not found")
    return result


@router.patch("/messages/{message_id}/status")
def update_message_status(message_id: UUID, status: str = Query(...), db: Session = Depends(get_db)):
    """Update the status of an A2A message."""
    try:
        result = a2a_service.update_message_status(db, message_id, status)
        if not result:
            raise HTTPException(404, "Message not found")
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/chain/{trace_id}")
def get_conversation_chain(trace_id: str, db: Session = Depends(get_db)):
    """Get the full A2A conversation chain for a trace_id with request-response pairing."""
    return a2a_service.get_conversation_chain(db, trace_id)


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
