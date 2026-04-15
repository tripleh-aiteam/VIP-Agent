"""
VIP AI Platform — SQLAlchemy Models
15 tables across 6 domains: core, orchestration, audit, a2a, agent-ops, telegram, spatial.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Text, Boolean, Integer, Float,
    DateTime, ForeignKey, Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from db.base import Base


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _uuid():
    return Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _now():
    return Column(DateTime, default=datetime.utcnow, nullable=False)


# ===========================================================================
#  CORE domain
# ===========================================================================

class CoreAgent(Base):
    __tablename__ = "core_agents"

    id = _uuid()
    name = Column(String(120), nullable=False, unique=True)
    type = Column(String(60), nullable=False)          # asset, stock, realty, custom …
    version = Column(String(30), default="0.1.0")
    owner_team = Column(String(120))
    endpoint_url = Column(Text)
    auth_type = Column(String(30), default="none")     # none | api_key | oauth
    status = Column(String(20), default="active")      # active | inactive | error
    is_mock = Column(Boolean, default=True)
    capabilities_json = Column(JSONB, default=dict)
    supported_task_types = Column(JSONB, default=list)  # ["asset_summary", "evaluate_portfolio"]
    supported_channels = Column(JSONB, default=list)    # ["web", "telegram"]
    priority_score = Column(Integer, default=100)       # higher = preferred (for routing)
    reliability_score = Column(Float, default=1.0)      # 0.0-1.0, updated by heartbeats
    description = Column(Text)
    created_at = _now()
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # relationships
    heartbeats = relationship("AgentHeartbeat", back_populates="agent", cascade="all, delete-orphan")
    task_runs = relationship("OrchTaskRun", back_populates="target_agent")


class CoreChannel(Base):
    __tablename__ = "core_channels"

    id = _uuid()
    type = Column(String(30), nullable=False)          # web | telegram | slack | whatsapp | ai_glass
    config_json = Column(JSONB, default=dict)
    status = Column(String(20), default="active")

    sessions = relationship("CoreSession", back_populates="channel")


class CoreSession(Base):
    __tablename__ = "core_sessions"

    id = _uuid()
    user_id = Column(String(120), nullable=False)
    channel_id = Column(UUID(as_uuid=True), ForeignKey("core_channels.id"), nullable=False)
    org_id = Column(String(120))
    session_key = Column(String(255), unique=True, nullable=False)
    context_json = Column(JSONB, default=dict)
    created_at = _now()
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    channel = relationship("CoreChannel", back_populates="sessions")


# ===========================================================================
#  ORCHESTRATION domain
# ===========================================================================

class OrchTaskDefinition(Base):
    __tablename__ = "orch_task_definitions"

    id = _uuid()
    task_type = Column(String(100), nullable=False, unique=True)
    target_agent_type = Column(String(60), nullable=False)
    input_schema_json = Column(JSONB, default=dict)
    output_schema_json = Column(JSONB, default=dict)
    timeout_seconds = Column(Integer, default=300)
    requires_judgement = Column(Boolean, default=False)
    created_at = _now()

    runs = relationship("OrchTaskRun", back_populates="task_definition")
    schedule_rules = relationship("OrchScheduleRule", back_populates="target_task_definition")


class OrchTaskRun(Base):
    __tablename__ = "orch_task_runs"

    id = _uuid()
    task_definition_id = Column(UUID(as_uuid=True), ForeignKey("orch_task_definitions.id"), nullable=False)
    initiator_type = Column(String(30), nullable=False)   # user | schedule | agent | system
    initiator_id = Column(String(120))
    source_channel = Column(String(30))
    target_agent_id = Column(UUID(as_uuid=True), ForeignKey("core_agents.id"))
    trace_id = Column(String(64), index=True)
    input_payload = Column(JSONB, default=dict)
    output_payload = Column(JSONB)
    status = Column(String(20), default="pending")        # pending | running | completed | failed
    error_message = Column(Text)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)

    task_definition = relationship("OrchTaskDefinition", back_populates="runs")
    target_agent = relationship("CoreAgent", back_populates="task_runs")
    judgement_cases = relationship("AuditJudgementCase", back_populates="task_run")
    a2a_messages = relationship("A2AMessage", back_populates="task_run")


class OrchScheduleRule(Base):
    __tablename__ = "orch_schedule_rules"

    id = _uuid()
    name = Column(String(120), nullable=False, unique=True)
    cron_expr = Column(String(60), nullable=False)
    target_task_definition_id = Column(UUID(as_uuid=True), ForeignKey("orch_task_definitions.id"), nullable=False)
    enabled = Column(Boolean, default=True)
    created_at = _now()

    target_task_definition = relationship("OrchTaskDefinition", back_populates="schedule_rules")


class OrchReport(Base):
    __tablename__ = "orch_reports"

    id = _uuid()
    report_type = Column(String(60), nullable=False)
    source_run_ids_json = Column(JSONB, default=list)
    content_json = Column(JSONB, default=dict)
    delivery_channel = Column(String(30))
    created_at = _now()


# ===========================================================================
#  AUDIT domain
# ===========================================================================

class AuditJudgementCase(Base):
    __tablename__ = "audit_judgement_cases"

    id = _uuid()
    task_run_id = Column(UUID(as_uuid=True), ForeignKey("orch_task_runs.id"), nullable=False)
    rule_result = Column(String(30))
    model_result = Column(String(30))
    risk_score = Column(Float)
    decision = Column(String(30))                         # approve | reject | escalate
    evidence_json = Column(JSONB, default=dict)
    created_at = _now()

    task_run = relationship("OrchTaskRun", back_populates="judgement_cases")
    approval_requests = relationship("AuditApprovalRequest", back_populates="judgement_case", cascade="all, delete-orphan")


class AuditApprovalRequest(Base):
    __tablename__ = "audit_approval_requests"

    id = _uuid()
    judgement_case_id = Column(UUID(as_uuid=True), ForeignKey("audit_judgement_cases.id"), nullable=False)
    requested_by = Column(String(120), nullable=False)
    approved_by = Column(String(120))
    decision = Column(String(20))                         # approved | denied | pending
    decided_at = Column(DateTime)

    judgement_case = relationship("AuditJudgementCase", back_populates="approval_requests")


class AuditEventLog(Base):
    __tablename__ = "audit_event_logs"

    id = _uuid()
    source = Column(String(120), nullable=False)
    event_type = Column(String(60), nullable=False)
    trace_id = Column(String(64), index=True)
    payload_json = Column(JSONB, default=dict)
    created_at = _now()


# ===========================================================================
#  A2A (Agent-to-Agent) domain
# ===========================================================================

class A2AMessage(Base):
    __tablename__ = "a2a_messages"

    id = _uuid()
    sender_agent_id = Column(UUID(as_uuid=True), ForeignKey("core_agents.id"), nullable=False)
    target_agent_id = Column(UUID(as_uuid=True), ForeignKey("core_agents.id"), nullable=False)
    task_run_id = Column(UUID(as_uuid=True), ForeignKey("orch_task_runs.id"))
    trace_id = Column(String(64), index=True)
    message_type = Column(String(30), nullable=False)     # request | response | event
    envelope_json = Column(JSONB, default=dict)
    status = Column(String(20), default="sent")           # sent | delivered | failed
    created_at = _now()

    task_run = relationship("OrchTaskRun", back_populates="a2a_messages")
    sender_agent = relationship("CoreAgent", foreign_keys=[sender_agent_id])
    target_agent = relationship("CoreAgent", foreign_keys=[target_agent_id])


# ===========================================================================
#  AGENT-OPS domain
# ===========================================================================

class AgentHeartbeat(Base):
    __tablename__ = "agent_heartbeats"

    id = _uuid()
    agent_id = Column(UUID(as_uuid=True), ForeignKey("core_agents.id"), nullable=False)
    status = Column(String(20), nullable=False)           # healthy | degraded | offline
    latency_ms = Column(Integer)
    metadata_json = Column(JSONB, default=dict)
    created_at = _now()

    agent = relationship("CoreAgent", back_populates="heartbeats")


# ===========================================================================
#  SPATIAL CAPTURE domain (AI Glasses / Realty)
# ===========================================================================

class RealtySpatialCaptureSession(Base):
    __tablename__ = "realty_spatial_capture_sessions"

    id = _uuid()
    agent_id = Column(UUID(as_uuid=True), ForeignKey("core_agents.id"), nullable=False)
    device_id = Column(String(120))
    property_ref = Column(String(255))
    video_uri = Column(Text)
    audio_uri = Column(Text)
    model_3d_uri = Column(Text)
    metadata_json = Column(JSONB, default=dict)
    processing_status = Column(String(20), default="pending")  # pending | processing | done | error
    created_at = _now()


# ===========================================================================
#  TELEGRAM domain
# ===========================================================================

class TelegramUser(Base):
    __tablename__ = "telegram_users"

    id = _uuid()
    telegram_user_id = Column(String(60), unique=True, nullable=False)
    linked_user_id = Column(String(120))
    role = Column(String(30), default="viewer")           # admin | operator | viewer
    status = Column(String(20), default="active")
    created_at = _now()


class TelegramAction(Base):
    __tablename__ = "telegram_actions"

    id = _uuid()
    telegram_user_id = Column(String(60), nullable=False)
    action_type = Column(String(60), nullable=False)
    related_task_run_id = Column(UUID(as_uuid=True), ForeignKey("orch_task_runs.id"))
    payload_json = Column(JSONB, default=dict)
    status = Column(String(20), default="pending")
    created_at = _now()


# ===========================================================================
#  CHATBOT domain
# ===========================================================================

class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = _uuid()
    user_id = Column(String(120), nullable=False)
    channel = Column(String(30), default="web")            # web | telegram | api
    mode = Column(String(20), default="structured")        # structured | llm
    title = Column(String(255), default="New Chat")
    folder = Column(String(100), nullable=True)            # folder name or null
    status = Column(String(20), default="active")          # active | archived | closed
    created_at = _now()
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan", order_by="ChatMessage.created_at")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = _uuid()
    session_id = Column(UUID(as_uuid=True), ForeignKey("chat_sessions.id"), nullable=False)
    role = Column(String(20), nullable=False)              # user | assistant | system
    message_type = Column(String(30), default="plain_text")  # plain_text | command | workflow_result | approval_result | report_summary
    content_json = Column(JSONB, default=dict)             # {"text": "...", "data": {...}}
    trace_id = Column(String(64), index=True)
    created_at = _now()

    session = relationship("ChatSession", back_populates="messages")
