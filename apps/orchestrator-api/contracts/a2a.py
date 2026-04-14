"""
VIP AI Platform — A2A (Agent-to-Agent) Contract
Envelope for all inter-agent communication.
"""

from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class A2AMessageType(str, Enum):
    request = "request"
    response = "response"
    event = "event"
    broadcast = "broadcast"


class A2APurpose(str, Enum):
    delegate = "delegate"
    inform = "inform"
    query = "query"
    escalate = "escalate"
    ack = "ack"


class A2AAuthorizationContext(BaseModel):
    sender_role: str = "agent"
    trust_level: str = "internal"
    signed: bool = False
    signature_ref: Optional[str] = None


class ProofOfIntent(BaseModel):
    reason: str = Field(..., description="Why this message is being sent")
    originating_task_type: Optional[str] = None
    policy_ref: Optional[str] = None


class A2AMessageEnvelope(BaseModel):
    """Standard envelope for agent-to-agent communication."""

    message_id: UUID = Field(default_factory=uuid4)
    trace_id: str = Field(..., description="Distributed tracing ID")
    sender_agent_id: str = Field(..., description="ID of the sending agent")
    target_agent_id: str = Field(..., description="ID of the receiving agent")
    source_task_id: Optional[UUID] = Field(None, description="Task that triggered this message")
    message_type: A2AMessageType = Field(...)
    purpose: A2APurpose = Field(...)
    authorization_context: Optional[A2AAuthorizationContext] = None
    proof_of_intent: Optional[ProofOfIntent] = None
    deadline: Optional[datetime] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    version: str = Field(default="1.0")

    model_config = {"json_schema_extra": {"examples": [
        {
            "trace_id": "tr-20260413-001",
            "sender_agent_id": "mock-asset-agent",
            "target_agent_id": "mock-stock-agent",
            "message_type": "request",
            "purpose": "delegate",
            "proof_of_intent": {"reason": "Need stock data to complete portfolio analysis"},
            "payload": {"symbols": ["AAPL", "GOOGL"]},
        }
    ]}}
