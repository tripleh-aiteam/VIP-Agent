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

    # Live voice meeting fields (Sprint 1 — twin-attends-meeting feature)
    is_voice = Column(Boolean, default=False, nullable=False)  # text meeting vs. live voice meeting
    voice_call_id = Column(UUID(as_uuid=True), ForeignKey("voice_calls.id"), nullable=True)
    sip_call_id = Column(String(120), nullable=True)           # Asterisk Call-ID for audio routing

    created_at = _now()

    participants = relationship("MeetingParticipant", back_populates="meeting", cascade="all, delete-orphan")
    messages = relationship("MeetingMessage", back_populates="meeting", cascade="all, delete-orphan", order_by="MeetingMessage.created_at")
    minutes = relationship("MeetingMinutes", back_populates="meeting", cascade="all, delete-orphan")
    utterances = relationship("MeetingUtterance", back_populates="meeting", cascade="all, delete-orphan", order_by="MeetingUtterance.spoken_at")


class MeetingParticipant(Base):
    __tablename__ = "meeting_participants"

    id = _uuid()
    meeting_id = Column(UUID(as_uuid=True), ForeignKey("meetings.id"), nullable=False)
    twin_id = Column(UUID(as_uuid=True), ForeignKey("digital_twins.id"), nullable=False)
    joined_at = Column(DateTime, default=datetime.utcnow)
    paused_task_id = Column(UUID(as_uuid=True), ForeignKey("twin_tasks.id"), nullable=True)

    # Twin-attends-meeting fields (Sprint 1)
    participant_type = Column(String(20), default="twin", nullable=False)
                                                             # twin (regular) | twin_proxy (attending FOR absent worker) | observer
    for_user_id = Column(UUID(as_uuid=True), ForeignKey("platform_users.id"), nullable=True)
                                                             # if twin_proxy: which worker the twin represents
    meeting_authority = Column(String(30), default="answer_factual", nullable=False)
                                                             # listener_only | answer_factual | answer_and_commit | full_proxy
    authorized_by_user_id = Column(UUID(as_uuid=True), ForeignKey("platform_users.id"), nullable=True)
    authorized_at = Column(DateTime, nullable=True)
    session_status = Column(String(20), default="active", nullable=False)
                                                             # active | left | escalated | ended
    left_at = Column(DateTime, nullable=True)
    escalation_count = Column(Integer, default=0, nullable=False)
    commitment_count = Column(Integer, default=0, nullable=False)  # how many "I will do X" commitments twin made

    meeting = relationship("Meeting", back_populates="participants")
    twin = relationship("DigitalTwin")
    paused_task = relationship("TwinTask", foreign_keys=[paused_task_id])
    for_user = relationship("PlatformUser", foreign_keys=[for_user_id])
    authorized_by = relationship("PlatformUser", foreign_keys=[authorized_by_user_id])
    utterances = relationship("MeetingUtterance", back_populates="participant", cascade="all, delete-orphan")


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


class MeetingUtterance(Base):
    """Audit log of every utterance in a live voice meeting.
    Captures who spoke, what they said, and whether twin commitments
    require post-meeting worker review. Sprint 1 of twin-attends-meeting.
    """
    __tablename__ = "meeting_utterances"

    id = _uuid()
    meeting_id = Column(UUID(as_uuid=True), ForeignKey("meetings.id"), nullable=False, index=True)
    participant_id = Column(UUID(as_uuid=True), ForeignKey("meeting_participants.id"), nullable=True, index=True)
                                                              # null when speaker is non-tracked (boss, external)
    speaker_role = Column(String(20), nullable=False)         # boss | worker | twin | colleague | external
    speaker_label = Column(String(120))                       # human-readable name (e.g. "김개발 Twin", "Boss")
    text = Column(Text, nullable=False)
    text_korean = Column(Text)                                 # original Korean if twin/boss spoke Korean
    audio_url = Column(Text)                                   # Asterisk recording segment URL
    spoken_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    # Twin-specific flags (only set when speaker_role=twin)
    is_commitment = Column(Boolean, default=False, nullable=False)  # twin said "I/we will ..."
    requires_worker_review = Column(Boolean, default=False, nullable=False)
    confidence = Column(Float)                                 # STT confidence, 0-1
    latency_ms = Column(Integer)                               # time from previous speaker end → this utterance start

    created_at = _now()

    meeting = relationship("Meeting", back_populates="utterances")
    participant = relationship("MeetingParticipant", back_populates="utterances")


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


class TwinGroup(Base):
    """A boss-defined group of workers + their twins. Group chat lives in
    DirectMessage-like rows tagged by group_id (we reuse MeetingMessage for
    in-meeting chat; groups have their own table below for plain chat).
    Adding a worker to the group auto-includes their linked twin.
    """
    __tablename__ = "twin_groups"

    id = _uuid()
    name = Column(String(160), nullable=False)
    description = Column(Text)
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("platform_users.id"), nullable=True)
    avatar_color = Column(String(16))                       # hex color for tile UI
    created_at = _now()
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    members = relationship("TwinGroupMember", back_populates="group", cascade="all, delete-orphan")
    messages = relationship("TwinGroupMessage", back_populates="group", cascade="all, delete-orphan", order_by="TwinGroupMessage.created_at")


class TwinGroupMember(Base):
    __tablename__ = "twin_group_members"

    id = _uuid()
    group_id = Column(UUID(as_uuid=True), ForeignKey("twin_groups.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("platform_users.id"), nullable=True)   # the worker
    twin_id = Column(UUID(as_uuid=True), ForeignKey("digital_twins.id"), nullable=True)    # the worker's twin (auto)
    role = Column(String(20), default="member", nullable=False)   # admin | member
    joined_at = _now()

    group = relationship("TwinGroup", back_populates="members")
    user = relationship("PlatformUser", foreign_keys=[user_id])
    twin = relationship("DigitalTwin", foreign_keys=[twin_id])


class TwinGroupMessage(Base):
    __tablename__ = "twin_group_messages"

    id = _uuid()
    group_id = Column(UUID(as_uuid=True), ForeignKey("twin_groups.id"), nullable=False, index=True)
    sender_type = Column(String(10), nullable=False)              # boss | worker | twin | system
    sender_user_id = Column(UUID(as_uuid=True), ForeignKey("platform_users.id"), nullable=True)
    sender_twin_id = Column(UUID(as_uuid=True), ForeignKey("digital_twins.id"), nullable=True)
    content = Column(Text, nullable=False)
    meta_json = Column(JSONB, default=dict)                       # parsed intent, scheduled meeting_id, etc.
    created_at = _now()

    group = relationship("TwinGroup", back_populates="messages")


class MeetingHandRaise(Base):
    """One twin signalling 'I can answer this' during a meeting question.
    The boss reviews raised hands and grants the floor to one twin.
    """
    __tablename__ = "meeting_hand_raises"

    id = _uuid()
    meeting_id = Column(UUID(as_uuid=True), ForeignKey("meetings.id"), nullable=False, index=True)
    participant_id = Column(UUID(as_uuid=True), ForeignKey("meeting_participants.id"), nullable=False, index=True)
    twin_id = Column(UUID(as_uuid=True), ForeignKey("digital_twins.id"), nullable=False)
    question_text = Column(Text, nullable=False)                  # what the boss asked
    confidence_score = Column(Float, nullable=False)              # 0-1 — twin's self-rated ability to answer
    reasoning = Column(Text)                                       # twin's brief justification
    status = Column(String(20), default="raised", nullable=False)
                                                                  # raised | granted | declined | lowered
    raised_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    granted_at = Column(DateTime, nullable=True)
    lowered_at = Column(DateTime, nullable=True)

    twin = relationship("DigitalTwin", foreign_keys=[twin_id])
    participant = relationship("MeetingParticipant", foreign_keys=[participant_id])


class WorkerVoiceProfile(Base):
    """Per-worker cloned voice profile for twin-attends-meeting (Sprint 4).
    Stores consent record, voice sample reference, and MeloTTS model handle.
    A twin uses this profile (via PlatformUser.twin_id back-reference) so
    its voice in meetings sounds like the actual worker.
    """
    __tablename__ = "worker_voice_profiles"

    id = _uuid()
    user_id = Column(UUID(as_uuid=True), ForeignKey("platform_users.id"), nullable=False, unique=True, index=True)

    # Consent — required before any voice processing
    consent_given = Column(Boolean, default=False, nullable=False)
    consent_text = Column(Text)                                # full text of the consent the user agreed to
    consent_given_at = Column(DateTime)
    consent_revoked_at = Column(DateTime)                      # if user later revokes

    # Voice sample
    sample_url = Column(Text)                                  # storage URL for the recorded sample
    sample_duration_sec = Column(Integer)
    sample_quality_score = Column(Float)                       # 0-1, set by voice_clone evaluator

    # MeloTTS / clone state
    status = Column(String(20), default="pending", nullable=False)
                                                              # pending | training | ready | failed | revoked
    melotts_model_path = Column(Text)                          # path to fine-tuned model on disk / GCS
    training_started_at = Column(DateTime)
    training_completed_at = Column(DateTime)
    failure_reason = Column(Text)

    created_at = _now()
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("PlatformUser", foreign_keys=[user_id])


# ===========================================================================
# CHATBOT MODULE — SELF-IMPROVEMENT pillar (Phase 1: foundation tables)
#
# These tables let the chatbot learn from interactions:
#   - chatbot_interactions: log of every query + reply (raw data for analysis)
#   - chatbot_corrections: explicit user corrections ("no, you got it wrong")
#   - chatbot_auto_examples: phrasings auto-promoted into intent example lists
#   - chatbot_user_profiles: per-user preferences (length, tone, topic affinity)
# ===========================================================================

class ChatbotInteraction(Base):
    """One row per /chatbot/talk call. Source of truth for all learning analytics."""
    __tablename__ = "chatbot_interactions"

    id = _uuid()
    agent_id = Column(String(40), nullable=False, index=True)        # "vip" / "asset" / "health" / ...
    user_id = Column(UUID(as_uuid=True), nullable=True, index=True)  # null = anonymous
    query = Column(Text, nullable=False)
    language = Column(String(8), default="auto")
    intent = Column(String(80), nullable=True, index=True)           # matched intent name OR "fallback" / "free_answer"
    source = Column(String(40), nullable=True)                        # keyword / llm / workflow / script / proactive / fallback
    reply = Column(Text)
    action_type = Column(String(40), nullable=True)                   # navigate / trigger / ui_command / script / workflow
    latency_ms = Column(Integer, default=0)
    was_corrected = Column(Boolean, default=False)                    # set later if user corrects this turn
    correction_id = Column(UUID(as_uuid=True), nullable=True)         # links to ChatbotCorrection if any
    created_at = _now()


class ChatbotCorrection(Base):
    """Explicit user correction: 'no, you got it wrong, X really means Y'."""
    __tablename__ = "chatbot_corrections"

    id = _uuid()
    agent_id = Column(String(40), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), nullable=True)
    original_query = Column(Text, nullable=False)            # what the user originally said
    wrong_intent = Column(String(80), nullable=True)         # intent the chatbot picked first
    correct_intent = Column(String(80), nullable=True)       # the right intent (set after follow-up)
    correction_text = Column(Text)                            # what the user said when correcting
    applied = Column(Boolean, default=False)                  # has this correction been folded into auto_examples?
    created_at = _now()


class ChatbotAutoExample(Base):
    """Phrasings the system auto-learned and added to an intent's example list.
    Loaded at request time so future fast-path matches benefit from them."""
    __tablename__ = "chatbot_auto_examples"

    id = _uuid()
    agent_id = Column(String(40), nullable=False, index=True)
    intent = Column(String(80), nullable=False, index=True)
    example_text = Column(Text, nullable=False)
    source = Column(String(20), default="auto_vocab")        # auto_vocab | correction | manual
    confidence = Column(Float, default=0.5)
    use_count = Column(Integer, default=0)                    # bumped each time it matches
    created_at = _now()


class ChatbotUserProfile(Base):
    """Per-user preferences detected over time (Phase 2 / 3)."""
    __tablename__ = "chatbot_user_profiles"

    id = _uuid()
    user_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    agent_id = Column(String(40), nullable=False, index=True)
    preferred_length = Column(String(20), default="normal")   # terse | normal | detailed
    preferred_tone = Column(String(20), default="neutral")    # formal | casual | neutral
    topic_affinity = Column(JSONB, default=dict)              # {"asset": 12, "twins": 5, ...}
    language_preference = Column(String(8), default="auto")
    interaction_count = Column(Integer, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_at = _now()


# ===========================================================================
#  VOICE / CALLING AGENT domain (v1.2.0)
#
#  Multi-tenant by `agent_id` — every row knows which consuming agent
#  (vip / realty / health / ...) owns it. The dashboard's REST endpoints
#  scope queries by the URL's {agent_id} segment so no cross-agent reads
#  ever happen.
#
#  Provider-agnostic. The discriminator is `voice_calls.provider`, plus
#  `provider_call_id` for matching Vapi/Twilio webhook events back to our
#  rows. Adding a new provider only changes the value, never the schema.
# ===========================================================================

class VoiceProviderAssistant(Base):
    """
    Maps a telephony provider's assistant/number ID → our agent_id.
    Used by the webhook handler to route inbound events to the right
    agent without trusting the wire payload's agent claim.
    """
    __tablename__ = "voice_provider_assistants"

    id = _uuid()
    agent_id = Column(String(40), nullable=False, index=True)
    provider = Column(String(20), nullable=False)            # "vapi" | "twilio" | "bird" | "nhn-toast"
    provider_assistant_id = Column(String(120), nullable=False, index=True)
    phone_number = Column(String(40), nullable=False)         # E.164: "+82-70-XXXX-XXXX"
    active = Column(Boolean, default=True, nullable=False)
    created_at = _now()
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class VoiceCall(Base):
    """
    One phone call — inbound or outbound, AI-handled or escalated.
    Transcript turns live in voice_call_turns (separate table for
    efficient streaming inserts). Recording metadata in voice_recordings.
    """
    __tablename__ = "voice_calls"

    id = _uuid()
    agent_id = Column(String(40), nullable=False, index=True)
    provider = Column(String(20), nullable=False)            # which telephony provider handled this
    provider_call_id = Column(String(120), index=True)        # provider's call UUID — match webhooks
    direction = Column(String(12), nullable=False)            # "inbound" | "outbound"
    status = Column(String(16), nullable=False, default="ringing")
                                                              # ringing | active | completed | missed | failed | escalated
    urgency = Column(String(8))                               # low | medium | high
    caller_number = Column(String(40), nullable=False)
    caller_name = Column(String(120))
    caller_tag = Column(String(120))                          # "Lease #L1-040" etc.
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    ended_at = Column(DateTime)
    duration_sec = Column(Integer)
    summary = Column(Text)                                    # LLM-generated post-call
    recording_url = Column(Text)                              # signed URL — convenience copy of voice_recordings.signed_url
    needs_review = Column(Boolean, default=False)
    escalation_json = Column(JSONB)                           # {to, reason, at} when status=escalated
    raw_provider_event = Column(JSONB)                        # last raw webhook payload — debugging
    # Batch campaign linkage (optional)
    campaign_id = Column(UUID(as_uuid=True), ForeignKey("batch_campaigns.id"), index=True)
    recipient_id = Column(UUID(as_uuid=True), ForeignKey("batch_recipients.id"), index=True)
    created_at = _now()
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class VoiceCallTurn(Base):
    """One transcript turn within a call. Streams in via the Vapi webhook
    as the conversation unfolds. `partial=True` rows get overwritten when
    the final version arrives (handled in voice_service.upsert_turn).
    """
    __tablename__ = "voice_call_turns"

    id = _uuid()
    call_id = Column(UUID(as_uuid=True), ForeignKey("voice_calls.id"), nullable=False, index=True)
    role = Column(String(8), nullable=False)                 # "bot" | "user"
    text = Column(Text, nullable=False)
    at = Column(DateTime, nullable=False, default=datetime.utcnow)
    confidence = Column(Float)                                # STT confidence, 0-1
    partial = Column(Boolean, default=False, nullable=False)
    provider_turn_id = Column(String(120), index=True)         # provider's stable turn id
    created_at = _now()


class VoiceRecording(Base):
    """Recording metadata for a finished call. Audio bytes live in
    Supabase Storage at /{agent_id}/{call_id}.mp3 — this row stores the
    storage path + a cached signed URL with expiry.
    """
    __tablename__ = "voice_recordings"

    id = _uuid()
    call_id = Column(UUID(as_uuid=True), ForeignKey("voice_calls.id"), nullable=False, unique=True)
    agent_id = Column(String(40), nullable=False, index=True)
    storage_path = Column(Text, nullable=False)               # "/{agent_id}/{call_id}.mp3"
    size_bytes = Column(Integer)
    duration_sec = Column(Integer)
    format = Column(String(8), default="mp3")
    signed_url = Column(Text)                                  # cached signed URL
    signed_url_expires_at = Column(DateTime)
    retention_expires_at = Column(DateTime)                    # row + storage object both purged after this
    created_at = _now()


class BatchCampaign(Base):
    """An outbound campaign — agent dials a list of recipients one-by-one
    respecting pacing + working hours.
    """
    __tablename__ = "batch_campaigns"

    id = _uuid()
    agent_id = Column(String(40), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    reason = Column(String(60), nullable=False)               # outbound reason ID from AgentConfig.voice.outboundReasons
    status = Column(String(16), nullable=False, default="idle")
                                                              # idle | running | paused | completed
    pacing = Column(Integer, default=12)                       # calls per hour
    working_hours_json = Column(JSONB, default=dict)           # {start, end} hours
    created_by = Column(UUID(as_uuid=True), ForeignKey("platform_users.id"))
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = _now()
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BatchRecipient(Base):
    """One recipient in a batch campaign queue."""
    __tablename__ = "batch_recipients"

    id = _uuid()
    campaign_id = Column(UUID(as_uuid=True), ForeignKey("batch_campaigns.id"), nullable=False, index=True)
    name = Column(String(120))
    number = Column(String(40), nullable=False)               # E.164
    context_json = Column(JSONB, default=dict)                 # {amount, lease, dueDate, ...} for script fill-in
    status = Column(String(16), nullable=False, default="queued")
                                                              # queued | calling | completed | skipped | failed
    outcome = Column(String(32))                              # promised_to_pay | refused | voicemail_left | no_answer | wrong_number | technical_failure | needs_callback
    notes = Column(Text)                                      # AI-generated note
    call_id = Column(UUID(as_uuid=True), ForeignKey("voice_calls.id"))  # set once dialed
    attempted_at = Column(DateTime)
    queue_order = Column(Integer, default=0)                   # for stable ordering within campaign
    created_at = _now()
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ===========================================================================
#  CHATBOT INBOX domain (v1.3.0) — customer ↔ boss conversations
#
#  Multi-tenant by `agent_id` — every row knows which consuming agent
#  (vip / realty / health / ...) owns it. Mirrors the voice-call tables'
#  pattern.
#
#  Channel-agnostic: same table holds KakaoTalk, phone, SMS, web
#  conversations. The `channel` discriminator on chatbot_conversations
#  + nullable channel-specific id columns let one transport's metadata
#  live alongside another.
# ===========================================================================

class ChatbotCustomer(Base):
    """One customer (per agent_id) — keyed by their channel identifier.
    A single person reached via multiple channels (e.g. KakaoTalk AND
    phone) gets ONE row, linked by phone or kakao_user_id."""
    __tablename__ = "chatbot_customers"

    id = _uuid()
    agent_id = Column(String(40), nullable=False, index=True)
    name = Column(String(120))
    phone = Column(String(40), index=True)                    # E.164 — links KakaoTalk + phone if same person
    kakao_user_id = Column(String(120), index=True)           # Kakao app user identifier
    email = Column(String(254), index=True)                    # email channel identifier
    tag = Column(String(120))                                  # "Lease #L1-040", "Viewing #V-23"
    avatar_url = Column(Text)
    notes = Column(Text)                                       # Boss-added free-form notes
    tags_json = Column(JSONB, default=list)                    # ["VIP", "신규고객", ...]
    last_seen_at = Column(DateTime)
    created_at = _now()
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ChatbotConversation(Base):
    """One conversation thread between a customer and the bot/boss.
    Multiple messages within a conversation live in chatbot_messages.
    A new conversation starts when the customer is silent for >24h OR
    explicitly closes (e.g. boss marks resolved)."""
    __tablename__ = "chatbot_conversations"

    id = _uuid()
    agent_id = Column(String(40), nullable=False, index=True)
    channel = Column(String(12), nullable=False)              # "kakao" | "phone" | "sms" | "web" | "email"
    customer_id = Column(UUID(as_uuid=True), ForeignKey("chatbot_customers.id"), nullable=False, index=True)

    # Email threading keys (RFC Message-IDs + normalized subject). Looked up
    # on inbound delivery to attach the new mail to the right Conversation.
    thread_keys_json = Column(JSONB, default=list)             # list[str]
    # IMAP UID watermark so the poller can resume past the last fetched UID.
    last_imap_uid = Column(Integer)
    status = Column(String(20), nullable=False, default="needs_reply")
                                                              # needs_reply | bot_handling | needs_review | escalated | resolved | missed
    urgency = Column(String(8))                               # low | medium | high
    last_message_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    unread_count = Column(Integer, default=0)                  # boss's perspective
    preview = Column(Text)                                     # latest message text (cached for list view)

    # Bot's draft reply awaiting boss approval (Boss-IN mode)
    suggested_reply_json = Column(JSONB)                       # {text, kind, reasoning}

    # If escalated, where + when + why
    escalation_json = Column(JSONB)                            # {to, reason, at}

    # Linked phone call (when channel="phone" and call originated outside chatbot)
    voice_call_id = Column(UUID(as_uuid=True), ForeignKey("voice_calls.id"))

    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    ended_at = Column(DateTime)
    created_at = _now()
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ChatbotMessage(Base):
    """One message within a conversation. Polymorphic on `kind`:
    - text: text field has content
    - voice: voice_url + voice_duration_sec + voice_transcript populated
    - image: image_url + image_caption populated
    - file: file_url + file_name + file_size_bytes + file_mime populated
    - system: text field has system event description (e.g. escalation banner)
    """
    __tablename__ = "chatbot_messages"

    id = _uuid()
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("chatbot_conversations.id"),
                             nullable=False, index=True)
    author = Column(String(12), nullable=False)               # "customer" | "bot" | "boss"
    kind = Column(String(12), nullable=False)                 # "text" | "voice" | "image" | "file" | "system"
    at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Channel-specific provider message ID (Kakao messageId, Twilio SID, etc.)
    provider_message_id = Column(String(120), index=True)

    # Text content (also used for system events)
    text = Column(Text)

    # Voice message metadata
    voice_url = Column(Text)
    voice_duration_sec = Column(Integer)
    voice_transcript = Column(Text)                            # Whisper-transcribed text
    confidence = Column(Float)                                 # STT confidence

    # Image metadata
    image_url = Column(Text)
    image_caption = Column(Text)
    image_width = Column(Integer)
    image_height = Column(Integer)

    # File attachment metadata
    file_url = Column(Text)
    file_name = Column(String(200))
    file_size_bytes = Column(Integer)
    file_mime = Column(String(80))

    # Bot reply metadata (when author="bot")
    # {status: "auto" | "approved" | "draft", reasoning?: "..."}
    bot_meta_json = Column(JSONB)

    # For streaming/typing indicators
    partial = Column(Boolean, default=False)

    created_at = _now()


class ChatbotConversationAction(Base):
    """Audit log of business actions on a conversation:
    - viewing scheduled / rent reminder sent / document uploaded / call placed / note added
    Rendered in the right-side CustomerInfoPanel as activity history."""
    __tablename__ = "chatbot_conversation_actions"

    id = _uuid()
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("chatbot_conversations.id"),
                             nullable=False, index=True)
    at = Column(DateTime, nullable=False, default=datetime.utcnow)
    kind = Column(String(40), nullable=False)
                                                              # viewing_scheduled | rent_reminder_sent |
                                                              # document_uploaded | call_placed |
                                                              # call_received | note_added
    description = Column(Text, nullable=False)
    ref_id = Column(String(120))                              # linked call_id / viewing_id / etc.
    created_by = Column(UUID(as_uuid=True), ForeignKey("platform_users.id"))
    created_at = _now()


class ChatbotAgentSetting(Base):
    """Per-agent runtime settings — currently used for the manual
    Boss-IN/Boss-OUT mode override + its reason + expiry.

    Why a DB row instead of in-memory: the boss flips the toggle, server
    restarts (deploys, OOM, scaling event), and the manual choice should
    NOT silently revert to auto-detect. This row makes the override survive."""
    __tablename__ = "chatbot_agent_settings"

    id = _uuid()
    agent_id = Column(String(40), nullable=False, unique=True, index=True)

    # Mode override — "in" / "out" / None (None = auto-detect by time)
    mode_override = Column(String(8))

    # Why the override was set: "meeting" / "lunch" / "off_day" / "vacation" / "other"
    # Free-form text in `mode_reason_note` for "other"
    mode_reason = Column(String(40))
    mode_reason_note = Column(Text)

    # When the override auto-expires (back to auto-detect). NULL = indefinite.
    mode_expires_at = Column(DateTime)

    # Whether auto-detect is allowed at all. When False, the override is
    # sticky until explicitly cleared regardless of time.
    auto_mode_enabled = Column(Boolean, default=True, nullable=False)

    # Audit trail
    updated_by = Column(UUID(as_uuid=True), ForeignKey("platform_users.id"))
    created_at = _now()
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ChatbotAgentAsset(Base):
    """Per-agent reusable file library — floor plans, brochures, contract
    templates, business cards, etc. The bot can autonomously send these
    in Boss-OUT mode when the customer's question matches the asset's
    keywords (e.g. customer asks "도면 보여주세요" → bot sends floor plan).

    The boss uploads assets via the dashboard; the autonomous-attachment
    dispatcher in chatbot_attachment_dispatcher.py picks a match based on
    keyword overlap with the incoming customer message."""
    __tablename__ = "chatbot_agent_assets"

    id = _uuid()
    agent_id = Column(String(40), nullable=False, index=True)
    label = Column(String(120), nullable=False)                # short title shown in dashboard
    description = Column(Text)                                  # what this asset is, when to send
    file_url = Column(Text, nullable=False)                     # public/signed URL the channel can fetch
    file_kind = Column(String(12), nullable=False, default="file")  # "image" | "file" | "voice"
    file_mime = Column(String(80))
    keywords_json = Column(JSONB, default=list)                 # ["도면", "평면도", "floor plan"]
    enabled = Column(Boolean, default=True, nullable=False)
    send_count = Column(Integer, default=0, nullable=False)     # how many times auto-sent
    last_sent_at = Column(DateTime)
    created_by = Column(UUID(as_uuid=True), ForeignKey("platform_users.id"))
    created_at = _now()
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ChatbotChannelMapping(Base):
    """Maps a provider channel ID to our internal agent_id.
    Mirrors voice_provider_assistants pattern. The Kakao webhook handler
    looks up agent_id here on every incoming event, so we never trust
    the wire payload's claimed agent."""
    __tablename__ = "chatbot_channel_mappings"

    id = _uuid()
    agent_id = Column(String(40), nullable=False, index=True)
    channel = Column(String(12), nullable=False)              # "kakao" | "sms" | "web"
    provider_channel_id = Column(String(120), nullable=False, index=True)
                                                              # Kakao Channel ID (e.g. "_abc123")
    display_name = Column(String(120))                         # for UI ("@triple-h-realestate")
    api_key_env_var = Column(String(80))                       # name of env var holding the API key
    webhook_secret_env_var = Column(String(80))                # name of env var holding webhook secret
    active = Column(Boolean, default=True, nullable=False)
    created_at = _now()
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
