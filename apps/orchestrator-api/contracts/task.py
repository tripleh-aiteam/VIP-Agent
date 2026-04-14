"""
VIP AI Platform — Task Contracts
TaskRequest: dispatched by Orchestrator to agents.
TaskResponse: returned by agents to Orchestrator.
"""

from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator


class Priority(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class InitiatorType(str, Enum):
    user = "user"
    schedule = "schedule"
    agent = "agent"
    system = "system"


class TaskStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    partial = "partial"
    cancelled = "cancelled"


class AuthorizationContext(BaseModel):
    user_id: Optional[str] = None
    org_id: Optional[str] = None
    roles: list[str] = Field(default_factory=list)
    token_ref: Optional[str] = None


class ApprovalContext(BaseModel):
    required: bool = False
    approver_id: Optional[str] = None
    approved_at: Optional[datetime] = None


class CallbackConfig(BaseModel):
    url: Optional[str] = None
    method: str = "POST"
    headers: dict[str, str] = Field(default_factory=dict)


class TaskRequest(BaseModel):
    """Standard input contract for tasks dispatched by the Orchestrator."""

    task_id: UUID = Field(default_factory=uuid4, description="Unique task identifier")
    trace_id: str = Field(..., description="Distributed tracing ID for cross-service correlation")
    initiator_type: InitiatorType = Field(..., description="Who initiated this task")
    initiator_id: str = Field(..., description="ID of the initiator (user_id, agent_id, schedule_id)")
    source_channel: Optional[str] = Field(None, description="Channel the task originated from (web, telegram, etc)")
    target_agent_type: str = Field(..., description="Type of agent to route to (asset, stock, realty, etc)")
    task_type: str = Field(..., description="Task type key (must match orch_task_definitions.task_type)")
    priority: Priority = Field(default=Priority.medium)
    authorization_context: Optional[AuthorizationContext] = None
    approval_context: Optional[ApprovalContext] = None
    deadline: Optional[datetime] = Field(None, description="Task must complete before this time")
    input_payload: dict[str, Any] = Field(default_factory=dict, description="Agent-specific input data")
    callback: Optional[CallbackConfig] = None
    metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary key-value metadata")
    version: str = Field(default="1.0", description="Contract version")

    model_config = {"json_schema_extra": {"examples": [
        {
            "trace_id": "tr-20260413-001",
            "initiator_type": "user",
            "initiator_id": "user-001",
            "source_channel": "web",
            "target_agent_type": "asset",
            "task_type": "asset_summary",
            "priority": "medium",
            "input_payload": {"portfolio_id": "PF-1234"},
        }
    ]}}


class TaskResponse(BaseModel):
    """Standard output contract returned by agents to the Orchestrator."""

    task_id: UUID = Field(..., description="Must match the request task_id")
    trace_id: str = Field(..., description="Must match the request trace_id")
    agent_id: str = Field(..., description="ID of the agent that executed the task")
    status: TaskStatus = Field(..., description="Execution result status")
    summary: Optional[str] = Field(None, description="Human-readable summary of the result")
    output_payload: Optional[dict[str, Any]] = Field(None, description="Agent-specific output data")
    error_message: Optional[str] = Field(None, description="Error details if status is failed")
    evidence_refs: list[str] = Field(default_factory=list, description="URIs to supporting evidence or artifacts")
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    version: str = Field(default="1.0")

    @model_validator(mode="after")
    def _check_error(self) -> TaskResponse:
        if self.status == TaskStatus.failed and not self.error_message:
            raise ValueError("error_message is required when status is 'failed'")
        return self

    model_config = {"json_schema_extra": {"examples": [
        {
            "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "trace_id": "tr-20260413-001",
            "agent_id": "mock-asset-agent",
            "status": "completed",
            "summary": "Portfolio PF-1234 summary generated",
            "output_payload": {"total_value": 1250000, "currency": "KRW"},
        }
    ]}}
