"""
VIP AI Platform — SQLAlchemy Models
25+ tables across 8 domains: core, orchestration, audit, a2a, agent-ops, telegram, spatial, digital-twin.
"""

import uuid
from datetime import datetime, time

from sqlalchemy import (
    Column, String, Text, Boolean, Integer, Float,
    DateTime, Time, ForeignKey, Enum as SAEnum,
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
#  PLATFORM USERS domain
# ===========================================================================

class PlatformUser(Base):
    __tablename__ = "platform_users"

    id = _uuid()
    email = Column(String(255), unique=True, nullable=False)
    name = Column(String(120), nullable=False)
    password_hash = Column(String(255))                    # bcrypt hash
    role = Column(String(30), default="viewer")           # admin | operator | viewer
    org_id = Column(String(120), default="default")
    status = Column(String(20), default="active")         # active | inactive | suspended
    preferences_json = Column(JSONB, default=dict)        # notification prefs, timezone, etc.
    telegram_user_id = Column(String(60))                 # linked telegram account
    reset_token = Column(String(255))                      # password reset token
    reset_token_expires = Column(DateTime)                 # reset token expiry
    last_login_at = Column(DateTime)
    created_at = _now()

    # --- Digital Twin fields ---
    has_twin = Column(Boolean, default=False)
    twin_id = Column(UUID(as_uuid=True), ForeignKey("digital_twins.id"), nullable=True)
    department = Column(String(120))                       # AI Team | Business | Asset | Investment
    working_hours_start = Column(Time, default=time(9, 0))   # 09:00
    working_hours_end = Column(Time, default=time(18, 0))    # 18:00

    twin = relationship("DigitalTwin", foreign_keys=[twin_id])


class PlatformNotification(Base):
    __tablename__ = "platform_notifications"

    id = _uuid()
    user_id = Column(UUID(as_uuid=True), ForeignKey("platform_users.id"))
    title = Column(String(255), nullable=False)
    body = Column(Text)
    severity = Column(String(20), default="info")         # info | warning | critical
    notification_type = Column(String(60))                # risk_alert | report | workflow | a2a
    source_trace_id = Column(String(64))
    is_read = Column(Boolean, default=False)
    created_at = _now()

    user = relationship("PlatformUser")


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


# ===========================================================================
#  DIGITAL TWIN domain
# ===========================================================================

class DigitalTwin(Base):
    __tablename__ = "digital_twins"

    id = _uuid()
    name = Column(String(120), nullable=False)                # "김개발" or "Dev Kim"
    role = Column(String(120), nullable=False)                # "Backend Developer"
    department = Column(String(120))                          # AI Team | Business | Asset | Investment
    avatar_url = Column(Text)                                 # profile image or color code
    personality_prompt = Column(Text)                         # system prompt describing personality
    skills = Column(JSONB, default=list)                      # ["Python", "FastAPI", "SQL"]
    mode = Column(String(20), default="shadow")               # shadow | active | handoff
    permission_level = Column(String(30), default="suggest")  # observe | suggest | act | act_unsupervised
    status = Column(String(20), default="idle")               # online | working | idle | offline | in_meeting
    current_task_id = Column(UUID(as_uuid=True), nullable=True)
    linked_agent_id = Column(UUID(as_uuid=True), ForeignKey("core_agents.id"), nullable=True)
    created_at = _now()
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # relationships
    linked_agent = relationship("CoreAgent", foreign_keys=[linked_agent_id])
    knowledge = relationship("TwinKnowledge", back_populates="twin", cascade="all, delete-orphan")
    activity_logs = relationship("TwinActivityLog", back_populates="twin", cascade="all, delete-orphan")
    tasks = relationship("TwinTask", back_populates="twin", cascade="all, delete-orphan")
    handoffs = relationship("TwinHandoff", back_populates="twin", cascade="all, delete-orphan")


class TwinKnowledge(Base):
    __tablename__ = "twin_knowledge"

    id = _uuid()
    twin_id = Column(UUID(as_uuid=True), ForeignKey("digital_twins.id"), nullable=False)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    source_type = Column(String(30), default="document")      # document | decision | style | instruction
    created_at = _now()

    twin = relationship("DigitalTwin", back_populates="knowledge")


class TwinActivityLog(Base):
    __tablename__ = "twin_activity_logs"

    id = _uuid()
    twin_id = Column(UUID(as_uuid=True), ForeignKey("digital_twins.id"), nullable=False)
    action_type = Column(String(30), nullable=False)          # reading | writing | analyzing | thinking | waiting | tool_call
    description = Column(Text, nullable=False)
    metadata_json = Column(JSONB, default=dict)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)

    twin = relationship("DigitalTwin", back_populates="activity_logs")


class TwinTask(Base):
    __tablename__ = "twin_tasks"

    id = _uuid()
    twin_id = Column(UUID(as_uuid=True), ForeignKey("digital_twins.id"), nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text)
    status = Column(String(20), default="todo")               # todo | in_progress | review | done
    priority = Column(String(20), default="medium")           # low | medium | high | urgent
    deadline = Column(DateTime, nullable=True)
    assigned_by = Column(String(120))                         # "vip" or twin name
    assigned_in_meeting_id = Column(UUID(as_uuid=True), ForeignKey("meetings.id"), nullable=True)
    result_json = Column(JSONB, default=dict)
    result_text = Column(Text)
    needs_review = Column(Boolean, default=False)
    reviewed_by = Column(String(120))
    review_status = Column(String(20))                        # pending | approved | rejected
    review_comment = Column(Text)
    created_at = _now()
    started_at = Column(DateTime)
    completed_at = Column(DateTime)

    twin = relationship("DigitalTwin", back_populates="tasks")
    meeting = relationship("Meeting", foreign_keys=[assigned_in_meeting_id])


class Meeting(Base):
    __tablename__ = "meetings"

    id = _uuid()
    title = Column(String(255), nullable=False)
    meeting_type = Column(String(30), default="all_hands")    # all_hands | team | one_on_one | standup | weekly_review
    status = Column(String(20), default="scheduled")          # scheduled | active | ended
    scheduled_at = Column(DateTime, nullable=True)
    started_at = Column(DateTime)
    ended_at = Column(DateTime)
    created_by = Column(String(120), default="vip")
    recurrence_rule = Column(String(120))                     # cron expression for recurring meetings
    created_at = _now()

    participants = relationship("MeetingParticipant", back_populates="meeting", cascade="all, delete-orphan")
    messages = relationship("MeetingMessage", back_populates="meeting", cascade="all, delete-orphan", order_by="MeetingMessage.created_at")
    minutes = relationship("MeetingMinutes", back_populates="meeting", cascade="all, delete-orphan")


class MeetingParticipant(Base):
    __tablename__ = "meeting_participants"

    id = _uuid()
    meeting_id = Column(UUID(as_uuid=True), ForeignKey("meetings.id"), nullable=False)
    twin_id = Column(UUID(as_uuid=True), ForeignKey("digital_twins.id"), nullable=False)
    joined_at = Column(DateTime, default=datetime.utcnow)
    paused_task_id = Column(UUID(as_uuid=True), ForeignKey("twin_tasks.id"), nullable=True)

    meeting = relationship("Meeting", back_populates="participants")
    twin = relationship("DigitalTwin")
    paused_task = relationship("TwinTask", foreign_keys=[paused_task_id])


class MeetingMessage(Base):
    __tablename__ = "meeting_messages"

    id = _uuid()
    meeting_id = Column(UUID(as_uuid=True), ForeignKey("meetings.id"), nullable=False)
    sender_type = Column(String(10), nullable=False)          # vip | twin
    sender_twin_id = Column(UUID(as_uuid=True), ForeignKey("digital_twins.id"), nullable=True)
    content = Column(Text, nullable=False)
    routed_to_twins = Column(JSONB, default=list)             # [twin_id, ...] who should respond
    created_at = _now()

    meeting = relationship("Meeting", back_populates="messages")
    sender_twin = relationship("DigitalTwin", foreign_keys=[sender_twin_id])


class MeetingMinutes(Base):
    __tablename__ = "meeting_minutes"

    id = _uuid()
    meeting_id = Column(UUID(as_uuid=True), ForeignKey("meetings.id"), nullable=False)
    decisions = Column(JSONB, default=list)                   # ["Approved KOSPI report format", ...]
    tasks_assigned = Column(JSONB, default=list)              # [{"twin": "Dev Twin", "task": "Fix login", "deadline": "Monday"}, ...]
    open_questions = Column(JSONB, default=list)              # ["Should we include foreign flow?", ...]
    summary = Column(Text)
    generated_at = Column(DateTime, default=datetime.utcnow)

    meeting = relationship("Meeting", back_populates="minutes")


class TwinHandoff(Base):
    __tablename__ = "twin_handoffs"

    id = _uuid()
    twin_id = Column(UUID(as_uuid=True), ForeignKey("digital_twins.id"), nullable=False)
    date = Column(DateTime, nullable=False)                   # handoff date
    tasks_completed = Column(JSONB, default=list)             # [{"task": "...", "result": "..."}, ...]
    tasks_pending_review = Column(JSONB, default=list)        # [{"task": "...", "draft": "..."}, ...]
    meeting_notes = Column(JSONB, default=list)               # [{"meeting": "...", "notes": "..."}, ...]
    overnight_summary = Column(Text)
    reviewed = Column(Boolean, default=False)
    reviewed_at = Column(DateTime)
    created_at = _now()

    twin = relationship("DigitalTwin", back_populates="handoffs")


class WorkerStatus(Base):
    __tablename__ = "worker_statuses"

    id = _uuid()
    user_id = Column(UUID(as_uuid=True), ForeignKey("platform_users.id"), nullable=False)
    is_online = Column(Boolean, default=False)
    last_active_at = Column(DateTime, default=datetime.utcnow)
    manual_status = Column(String(20), default="offline")     # working | meeting | break | offline
    created_at = _now()

    user = relationship("PlatformUser", foreign_keys=[user_id])


class DirectMessage(Base):
    __tablename__ = "direct_messages"

    id = _uuid()
    twin_id = Column(UUID(as_uuid=True), ForeignKey("digital_twins.id"), nullable=False)
    sender_type = Column(String(10), nullable=False)          # boss | worker
    content = Column(Text, nullable=False)
    is_read = Column(Boolean, default=False)
    created_at = _now()

    twin = relationship("DigitalTwin", foreign_keys=[twin_id])


class TwinSnapshot(Base):
    __tablename__ = "twin_snapshots"

    id = _uuid()
    twin_id = Column(UUID(as_uuid=True), ForeignKey("digital_twins.id"), nullable=False)
    version_name = Column(String(120), nullable=False)      # "v1.0 — initial training", "v2.0 — after bug fixes"
    snapshot_type = Column(String(30), default="manual")    # manual | auto | milestone
    personality_prompt = Column(Text)                        # Backup of twin's personality
    skills_json = Column(JSONB, default=list)                # Backup of skills
    mode = Column(String(20))
    permission_level = Column(String(30))
    knowledge_count = Column(Integer, default=0)
    intelligence_pct = Column(Integer, default=0)
    knowledge_ids = Column(JSONB, default=list)              # List of TwinKnowledge IDs at snapshot time
    notes = Column(Text)                                      # Why this snapshot was created
    created_at = _now()

    twin = relationship("DigitalTwin", foreign_keys=[twin_id])


class TwinNotification(Base):
    __tablename__ = "twin_notifications"

    id = _uuid()
    twin_id = Column(UUID(as_uuid=True), ForeignKey("digital_twins.id"), nullable=False)
    type = Column(String(30), nullable=False)              # task_completed | task_failed | boss_message | self_improved | handoff
    title = Column(String(255), nullable=False)
    body = Column(Text)
    is_read = Column(Boolean, default=False)
    created_at = _now()

    twin = relationship("DigitalTwin", foreign_keys=[twin_id])
