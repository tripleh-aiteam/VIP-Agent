"""
VIP AI Platform — A2A (Agent-to-Agent) Service
Every message uses A2AMessageEnvelope. All messages are persisted, traced, and authorized.
High-risk messages can be flagged for judgement review.
"""

from datetime import datetime
from uuid import UUID, uuid4
from typing import Any

from sqlalchemy.orm import Session

from db.models import A2AMessage, CoreAgent
from services.audit_service import record_event
from services.event_bus import publish
from services.logger import log


# Message types that require judgement review
HIGH_RISK_TYPES = {"risk_alert", "escalation_request"}

VALID_MESSAGE_TYPES = {
    "risk_alert",
    "data_request",
    "report_request",
    "report_response",
    "feedback_request",
    "escalation_request",
}

VALID_PURPOSES = {"delegate", "inform", "query", "escalate", "ack"}


def send_message(
    db: Session,
    trace_id: str,
    sender_agent_id: str,
    target_agent_id: str,
    message_type: str,
    purpose: str,
    payload: dict[str, Any],
    source_task_id: UUID | None = None,
    authorization_context: dict | None = None,
    proof_of_intent: dict | None = None,
    deadline: datetime | None = None,
) -> dict:
    """
    Send an A2A message. Validates, persists, publishes to event bus, and flags high-risk.
    """

    # Validate message type
    if message_type not in VALID_MESSAGE_TYPES:
        raise ValueError(f"Invalid message_type: {message_type}. Must be one of: {VALID_MESSAGE_TYPES}")

    if purpose not in VALID_PURPOSES:
        raise ValueError(f"Invalid purpose: {purpose}. Must be one of: {VALID_PURPOSES}")

    # Verify sender exists
    sender = db.query(CoreAgent).filter(CoreAgent.name == sender_agent_id).first()
    if not sender:
        sender = db.query(CoreAgent).filter(CoreAgent.id == sender_agent_id).first() if len(sender_agent_id) > 30 else None
    sender_name = sender.name if sender else sender_agent_id
    sender_uuid = sender.id if sender else None

    # Verify target exists
    target = db.query(CoreAgent).filter(CoreAgent.name == target_agent_id).first()
    if not target:
        target = db.query(CoreAgent).filter(CoreAgent.id == target_agent_id).first() if len(target_agent_id) > 30 else None
    target_name = target.name if target else target_agent_id
    target_uuid = target.id if target else None

    if not sender_uuid or not target_uuid:
        raise ValueError(f"Agent not found: sender={sender_agent_id}, target={target_agent_id}")

    # Determine if high-risk
    is_high_risk = message_type in HIGH_RISK_TYPES
    needs_judgement = is_high_risk

    # Build envelope
    message_id = uuid4()
    envelope = {
        "message_id": str(message_id),
        "trace_id": trace_id,
        "sender_agent_id": sender_name,
        "target_agent_id": target_name,
        "source_task_id": str(source_task_id) if source_task_id else None,
        "message_type": message_type,
        "purpose": purpose,
        "authorization_context": authorization_context or {"sender_role": "agent", "trust_level": "internal"},
        "proof_of_intent": proof_of_intent,
        "deadline": deadline.isoformat() if deadline else None,
        "payload": payload,
        "is_high_risk": is_high_risk,
        "needs_judgement": needs_judgement,
    }

    # Persist to DB
    msg = A2AMessage(
        id=message_id,
        sender_agent_id=sender_uuid,
        target_agent_id=target_uuid,
        task_run_id=source_task_id,
        trace_id=trace_id,
        message_type=message_type,
        envelope_json=envelope,
        status="sent",
    )
    db.add(msg)
    db.flush()

    # Publish to event bus
    publish(f"a2a.{message_type}", envelope)
    publish(f"a2a.to.{target_name}", envelope)

    # Audit
    record_event(db, "a2a", f"a2a.{message_type}", trace_id, {
        "message_id": str(message_id),
        "sender": sender_name,
        "target": target_name,
        "purpose": purpose,
        "is_high_risk": is_high_risk,
    })

    log.info(
        f"a2a: {sender_name} -> {target_name} [{message_type}/{purpose}]",
        extra={"trace_id": trace_id, "action": f"a2a.{message_type}"},
    )

    db.commit()

    return {
        "message_id": str(message_id),
        "trace_id": trace_id,
        "sender": sender_name,
        "target": target_name,
        "message_type": message_type,
        "purpose": purpose,
        "status": "sent",
        "is_high_risk": is_high_risk,
        "needs_judgement": needs_judgement,
    }


def get_message(db: Session, message_id: UUID) -> dict | None:
    msg = db.query(A2AMessage).filter(A2AMessage.id == message_id).first()
    if not msg:
        return None
    return _msg_to_dict(msg)


def list_messages(
    db: Session,
    message_type: str | None = None,
    trace_id: str | None = None,
    limit: int = 50,
) -> list[dict]:
    q = db.query(A2AMessage)
    if message_type:
        q = q.filter(A2AMessage.message_type == message_type)
    if trace_id:
        q = q.filter(A2AMessage.trace_id == trace_id)
    msgs = q.order_by(A2AMessage.created_at.desc()).limit(limit).all()
    return [_msg_to_dict(m) for m in msgs]


def receive_webhook(
    db: Session,
    sender_agent_id: str,
    trace_id: str,
    message_type: str,
    purpose: str,
    payload: dict[str, Any],
    in_reply_to: str | None = None,
    proof_of_intent: dict | None = None,
) -> dict:
    """
    Receive an inbound A2A message from an external agent via webhook.
    Validates sender, persists the message, publishes to event bus, and
    optionally links it to the original outbound message.
    """

    if message_type not in VALID_MESSAGE_TYPES:
        raise ValueError(f"Invalid message_type: {message_type}. Must be one of: {VALID_MESSAGE_TYPES}")

    if purpose not in VALID_PURPOSES:
        raise ValueError(f"Invalid purpose: {purpose}. Must be one of: {VALID_PURPOSES}")

    # Verify sender agent exists
    sender = db.query(CoreAgent).filter(CoreAgent.name == sender_agent_id).first()
    if not sender:
        sender = db.query(CoreAgent).filter(CoreAgent.id == sender_agent_id).first() if len(sender_agent_id) > 30 else None
    if not sender:
        raise ValueError(f"Unknown sender agent: {sender_agent_id}")

    # If replying to a message, look up original to find target
    original_msg = None
    target_agent = None
    if in_reply_to:
        try:
            original_msg = db.query(A2AMessage).filter(A2AMessage.id == UUID(in_reply_to)).first()
        except (ValueError, AttributeError):
            pass
        if original_msg:
            # The reply target is whoever sent the original message
            target_agent = original_msg.sender_agent
            # Mark original as delivered
            original_msg.status = "delivered"
            db.flush()

    # Default target: the orchestrator itself (represented by the first system agent, or sender's own entry)
    if not target_agent:
        # Webhook messages are directed to the orchestrator
        target_agent = db.query(CoreAgent).filter(CoreAgent.name == "Asset Agent").first() or sender

    is_high_risk = message_type in HIGH_RISK_TYPES

    message_id = uuid4()
    envelope = {
        "message_id": str(message_id),
        "trace_id": trace_id,
        "sender_agent_id": sender.name,
        "target_agent_id": target_agent.name,
        "message_type": message_type,
        "purpose": purpose,
        "direction": "inbound",
        "in_reply_to": in_reply_to,
        "proof_of_intent": proof_of_intent,
        "payload": payload,
        "is_high_risk": is_high_risk,
    }

    msg = A2AMessage(
        id=message_id,
        sender_agent_id=sender.id,
        target_agent_id=target_agent.id,
        task_run_id=original_msg.task_run_id if original_msg else None,
        trace_id=trace_id,
        message_type=message_type,
        envelope_json=envelope,
        status="received",
    )
    db.add(msg)
    db.flush()

    # Publish to event bus
    publish(f"a2a.inbound.{message_type}", envelope)
    publish(f"a2a.from.{sender.name}", envelope)

    # Audit
    record_event(db, "a2a_webhook", f"a2a.webhook.{message_type}", trace_id, {
        "message_id": str(message_id),
        "sender": sender.name,
        "target": target_agent.name,
        "purpose": purpose,
        "direction": "inbound",
        "in_reply_to": in_reply_to,
        "is_high_risk": is_high_risk,
    })

    log.info(
        f"a2a webhook: {sender.name} -> orchestrator [{message_type}/{purpose}]",
        extra={"trace_id": trace_id, "action": f"a2a.webhook.{message_type}"},
    )

    db.commit()

    return {
        "accepted": True,
        "message_id": str(message_id),
        "trace_id": trace_id,
        "sender": sender.name,
        "message_type": message_type,
        "purpose": purpose,
        "status": "received",
        "is_high_risk": is_high_risk,
        "in_reply_to": in_reply_to,
    }


def receive_agent_data(
    db: Session,
    agent_type: str,
    trace_id: str,
    data_type: str,
    payload: dict[str, Any],
    source_message_id: str | None = None,
) -> dict:
    """
    Receive structured data/result from an agent via webhook.
    Used when an agent pushes data proactively (e.g., market alert, updated listings).
    """

    # Find the agent by type
    agent = (
        db.query(CoreAgent)
        .filter(CoreAgent.type == agent_type, CoreAgent.status == "active")
        .order_by(CoreAgent.priority_score.desc())
        .first()
    )
    if not agent:
        raise ValueError(f"No active agent found for type: {agent_type}")

    message_id = uuid4()
    is_high_risk = data_type in ("risk_alert", "escalation_request")

    envelope = {
        "message_id": str(message_id),
        "trace_id": trace_id,
        "sender_agent_id": agent.name,
        "target_agent_id": "orchestrator",
        "message_type": data_type,
        "purpose": "inform",
        "direction": "inbound",
        "source_message_id": source_message_id,
        "payload": payload,
        "is_high_risk": is_high_risk,
    }

    # Find any agent to use as target FK (orchestrator doesn't have its own agent row)
    # Use the sender itself as target for FK constraint
    msg = A2AMessage(
        id=message_id,
        sender_agent_id=agent.id,
        target_agent_id=agent.id,  # self-referencing for orchestrator-bound messages
        trace_id=trace_id,
        message_type=data_type,
        envelope_json=envelope,
        status="received",
    )
    db.add(msg)
    db.flush()

    # Publish
    publish(f"a2a.inbound.{data_type}", envelope)
    publish(f"a2a.agent_data.{agent_type}", envelope)

    # Audit
    record_event(db, "a2a_webhook", f"a2a.agent_data.{data_type}", trace_id, {
        "message_id": str(message_id),
        "agent": agent.name,
        "agent_type": agent_type,
        "data_type": data_type,
        "direction": "inbound",
    })

    log.info(
        f"a2a agent data: {agent.name} pushed {data_type}",
        extra={"trace_id": trace_id, "action": f"a2a.agent_data.{data_type}"},
    )

    db.commit()

    return {
        "accepted": True,
        "message_id": str(message_id),
        "trace_id": trace_id,
        "agent": agent.name,
        "agent_type": agent_type,
        "data_type": data_type,
        "status": "received",
        "is_high_risk": is_high_risk,
    }


def request_data_from_agent(
    db: Session,
    requester_agent_id: str,
    target_agent_type: str,
    trace_id: str,
    data_request: str,
    context: dict[str, Any] | None = None,
) -> dict:
    """
    Cross-agent data request: one agent requests data from another through the orchestrator.
    1. Sends an A2A data_request message
    2. Fetches actual data from the target agent via adapter
    3. Stores the response as an A2A report_response message
    4. Returns the data linked to both A2A messages
    """
    from services.agent_service import resolve_agent
    from adapters import get_adapter

    # Verify requester
    requester = db.query(CoreAgent).filter(CoreAgent.name == requester_agent_id).first()
    if not requester:
        raise ValueError(f"Requester agent not found: {requester_agent_id}")

    # Resolve target agent
    target = resolve_agent(db, target_agent_type)
    if not target:
        raise ValueError(f"No active agent for type: {target_agent_type}")

    # Step 1: Send data_request A2A message
    request_result = send_message(
        db=db,
        trace_id=trace_id,
        sender_agent_id=requester.name,
        target_agent_id=target.name,
        message_type="data_request",
        purpose="query",
        payload={"request": data_request, "context": context or {}},
        proof_of_intent={"reason": f"Cross-agent data request: {data_request}"},
    )

    # Step 2: Fetch data from target agent via adapter
    adapter = get_adapter(
        agent_type=target.type,
        agent_name=target.name,
        endpoint_url=target.endpoint_url or "",
        is_mock=target.is_mock,
        timeout_seconds=30,
        auth_type=target.auth_type,
    )

    task_type_map = {
        "asset": "asset_summary",
        "stock": "stock_analysis",
        "realty": "realty_listing_fetch",
    }
    task_type = task_type_map.get(target_agent_type, f"{target_agent_type}_query")

    adapter_result = adapter.execute(
        task_run_id=request_result["message_id"],
        trace_id=trace_id,
        task_type=task_type,
        input_payload={"from_a2a": True, "request": data_request, **(context or {})},
    )

    fetched_data = adapter_result.output_payload or {}
    fetch_success = adapter_result.success

    # Step 3: Store the response as an A2A report_response
    response_result = send_message(
        db=db,
        trace_id=trace_id,
        sender_agent_id=target.name,
        target_agent_id=requester.name,
        message_type="report_response",
        purpose="inform",
        payload={
            "data_type": data_request,
            "success": fetch_success,
            "data": fetched_data,
            "summary": adapter_result.summary,
        },
        proof_of_intent={"reason": f"Response to data request: {data_request}"},
    )

    # Mark the original request as delivered
    try:
        original = db.query(A2AMessage).filter(
            A2AMessage.id == UUID(request_result["message_id"])
        ).first()
        if original:
            original.status = "delivered"
            db.flush()
            db.commit()
    except Exception:
        pass

    log.info(
        f"a2a data flow: {requester.name} <- {target.name} [{data_request}] success={fetch_success}",
        extra={"trace_id": trace_id, "action": "a2a.data_flow.completed"},
    )

    return {
        "trace_id": trace_id,
        "requester": requester.name,
        "target": target.name,
        "data_request": data_request,
        "success": fetch_success,
        "data": fetched_data,
        "summary": adapter_result.summary,
        "request_message_id": request_result["message_id"],
        "response_message_id": response_result["message_id"],
        "a2a_chain": [request_result["message_id"], response_result["message_id"]],
    }


def _msg_to_dict(msg: A2AMessage) -> dict:
    return {
        "id": str(msg.id),
        "trace_id": msg.trace_id,
        "sender_agent": msg.sender_agent.name if msg.sender_agent else str(msg.sender_agent_id),
        "target_agent": msg.target_agent.name if msg.target_agent else str(msg.target_agent_id),
        "message_type": msg.message_type,
        "status": msg.status,
        "envelope": msg.envelope_json,
        "source_task_id": str(msg.task_run_id) if msg.task_run_id else None,
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
    }
