"""
VIP AI Platform — Judgement Contracts
JudgementRequest: sent by Orchestrator to Judgement Service.
JudgementResult: returned by Judgement Service after evaluation.
"""

from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class RiskLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class JudgementDecision(str, Enum):
    approve = "approve"
    reject = "reject"
    escalate = "escalate"
    hold = "hold"


class JudgementRequest(BaseModel):
    """Request to the Judgement Service to evaluate a task run's output."""

    judgement_id: UUID = Field(default_factory=uuid4)
    trace_id: str = Field(...)
    task_run_id: UUID = Field(..., description="The task run being evaluated")
    task_type: str = Field(...)
    agent_id: str = Field(..., description="Agent that produced the output")
    agent_output: dict[str, Any] = Field(..., description="The output to evaluate")
    rules: list[str] = Field(default_factory=list, description="Rule IDs to apply")
    context: dict[str, Any] = Field(default_factory=dict, description="Additional context for evaluation")
    require_human_approval: bool = Field(default=False)
    requested_at: datetime = Field(default_factory=datetime.utcnow)
    version: str = Field(default="1.0")

    model_config = {"json_schema_extra": {"examples": [
        {
            "trace_id": "tr-20260413-001",
            "task_run_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "task_type": "stock_analysis",
            "agent_id": "Stock Agent",
            "agent_output": {"recommendation": "buy", "confidence": 0.85},
            "rules": ["max_risk_threshold", "compliance_check"],
        }
    ]}}


class JudgementResult(BaseModel):
    """Result from the Judgement Service after evaluation."""

    judgement_id: UUID = Field(...)
    trace_id: str = Field(...)
    task_run_id: UUID = Field(...)
    rule_result: Optional[str] = Field(None, description="Aggregated rule evaluation result")
    model_result: Optional[str] = Field(None, description="AI model evaluation result")
    risk_score: float = Field(..., ge=0.0, le=1.0, description="Risk score 0.0 (safe) to 1.0 (critical)")
    risk_level: RiskLevel = Field(...)
    decision: JudgementDecision = Field(...)
    reasoning: Optional[str] = Field(None, description="Human-readable reasoning")
    evidence_json: dict[str, Any] = Field(default_factory=dict)
    requires_approval: bool = Field(default=False)
    judged_at: datetime = Field(default_factory=datetime.utcnow)
    version: str = Field(default="1.0")

    model_config = {"protected_namespaces": (), "json_schema_extra": {"examples": [
        {
            "judgement_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
            "trace_id": "tr-20260413-001",
            "task_run_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "rule_result": "pass",
            "model_result": "low_risk",
            "risk_score": 0.15,
            "risk_level": "low",
            "decision": "approve",
            "reasoning": "Stock recommendation within risk tolerance",
        }
    ]}}
