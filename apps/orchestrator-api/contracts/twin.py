"""
VIP AI Platform — Digital Twin Contracts
Pydantic schemas for twin CRUD, tasks, knowledge, and activity.
"""

from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
#  Enums
# ---------------------------------------------------------------------------

class TwinMode(str, Enum):
    shadow = "shadow"
    active = "active"
    handoff = "handoff"


class TwinPermission(str, Enum):
    observe = "observe"
    suggest = "suggest"
    act = "act"
    act_unsupervised = "act_unsupervised"


class TwinStatus(str, Enum):
    online = "online"
    working = "working"
    idle = "idle"
    offline = "offline"
    in_meeting = "in_meeting"


class TaskStatus(str, Enum):
    todo = "todo"
    in_progress = "in_progress"
    review = "review"
    done = "done"


class TaskPriority(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    urgent = "urgent"


class ReviewStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class KnowledgeSourceType(str, Enum):
    document = "document"
    decision = "decision"
    style = "style"
    instruction = "instruction"


# ---------------------------------------------------------------------------
#  Twin Schemas
# ---------------------------------------------------------------------------

class TwinCreate(BaseModel):
    name: str = Field(..., description="Twin display name")
    role: str = Field(..., description="Job role: Backend Developer, Stock Analyst, etc.")
    department: Optional[str] = Field(None, description="AI Team | Business | Asset | Investment")
    avatar_url: Optional[str] = None
    personality_prompt: Optional[str] = Field(None, description="System prompt for twin personality")
    skills: list[str] = Field(default_factory=list, description="List of skills")
    permission_level: TwinPermission = TwinPermission.suggest
    linked_agent_id: Optional[UUID] = Field(None, description="Link to existing CoreAgent")


class TwinUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    department: Optional[str] = None
    avatar_url: Optional[str] = None
    personality_prompt: Optional[str] = None
    skills: Optional[list[str]] = None
    permission_level: Optional[TwinPermission] = None
    linked_agent_id: Optional[UUID] = None


class TwinModeSwitch(BaseModel):
    mode: TwinMode


class TwinProfile(BaseModel):
    id: UUID
    name: str
    role: str
    department: Optional[str]
    avatar_url: Optional[str]
    personality_prompt: Optional[str]
    skills: list[str]
    mode: TwinMode
    permission_level: TwinPermission
    status: TwinStatus
    current_task_id: Optional[UUID]
    linked_agent_id: Optional[UUID]
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
#  Twin Task Schemas
# ---------------------------------------------------------------------------

class TwinTaskCreate(BaseModel):
    title: str = Field(..., description="Task title")
    description: Optional[str] = None
    priority: TaskPriority = TaskPriority.medium
    deadline: Optional[datetime] = None


class TwinTaskUpdate(BaseModel):
    status: Optional[TaskStatus] = None
    result_text: Optional[str] = None
    result_json: Optional[dict] = None


class TwinTaskReview(BaseModel):
    review_status: ReviewStatus
    review_comment: Optional[str] = None


class TwinTaskResponse(BaseModel):
    id: UUID
    twin_id: UUID
    title: str
    description: Optional[str]
    status: TaskStatus
    priority: TaskPriority
    deadline: Optional[datetime]
    assigned_by: Optional[str]
    needs_review: bool
    review_status: Optional[str]
    created_at: datetime
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
#  Twin Knowledge Schemas
# ---------------------------------------------------------------------------

class TwinKnowledgeCreate(BaseModel):
    title: str
    content: str
    source_type: KnowledgeSourceType = KnowledgeSourceType.document


class TwinKnowledgeResponse(BaseModel):
    id: UUID
    twin_id: UUID
    title: str
    content: str
    source_type: str
    created_at: datetime

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
#  Twin Activity Schemas
# ---------------------------------------------------------------------------

class TwinActivityEntry(BaseModel):
    id: UUID
    twin_id: UUID
    action_type: str
    description: str
    metadata_json: dict
    timestamp: datetime

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
#  Twin Chat Schemas
# ---------------------------------------------------------------------------

class TwinChatMessage(BaseModel):
    message: str = Field(..., description="Message to send to the twin")
    model: str | None = Field(None, description="LLM model to use (e.g. 'claude-sonnet-4-6'). Defaults if omitted.")


# ---------------------------------------------------------------------------
#  Live Meeting (twin-attends-meeting feature) — Sprint 1
# ---------------------------------------------------------------------------

class TwinMeetingAuthority(str, Enum):
    listener_only = "listener_only"          # twin attends silent, reports later
    answer_factual = "answer_factual"        # answers factual questions, no commitments
    answer_and_commit = "answer_and_commit"  # can agree to action items within scope
    full_proxy = "full_proxy"                # full authority (rare)


class MeetingJoinRequest(BaseModel):
    """Worker (or boss on worker's behalf) authorizes twin to attend a meeting."""
    meeting_id: UUID = Field(..., description="The Meeting row to join")
    for_user_id: Optional[UUID] = Field(None, description="Worker the twin represents; defaults to twin's owner")
    authority: TwinMeetingAuthority = TwinMeetingAuthority.answer_factual
    reason: Optional[str] = Field(None, description="Why twin is attending (sick, personal, scheduling conflict)")


class MeetingJoinResponse(BaseModel):
    participant_id: UUID
    meeting_id: UUID
    twin_id: UUID
    authority: TwinMeetingAuthority
    session_status: str
    joined_at: datetime
    is_voice: bool


class MeetingLeaveRequest(BaseModel):
    reason: Optional[str] = Field(None, description="Why twin is leaving (meeting ended, escalation, error)")
    generate_summary: bool = True


class MeetingEscalateRequest(BaseModel):
    """Twin needs the worker's input mid-meeting."""
    question: str = Field(..., description="The question the twin can't answer without the worker")
    stall_phrase_kr: Optional[str] = Field(None, description="What twin should say aloud while waiting (Korean)")


class MeetingUtteranceCreate(BaseModel):
    """Internal: voice pipeline pushes an utterance into the audit log."""
    speaker_role: str  # boss | worker | twin | colleague | external
    speaker_label: Optional[str] = None
    text: str
    text_korean: Optional[str] = None
    audio_url: Optional[str] = None
    is_commitment: bool = False
    requires_worker_review: bool = False
    confidence: Optional[float] = None
    latency_ms: Optional[int] = None


class MeetingActiveSession(BaseModel):
    participant_id: UUID
    meeting_id: UUID
    meeting_title: str
    twin_id: UUID
    twin_name: str
    authority: str
    session_status: str
    joined_at: datetime
    commitment_count: int
    escalation_count: int


# ---------------------------------------------------------------------------
#  Live STT pipeline — Sprint 2
# ---------------------------------------------------------------------------

class MeetingListenStartAsterisk(BaseModel):
    asterisk_channel_id: str = Field(..., description="Asterisk live channel ID (UniqueID)")
    speaker_label: str = Field("SIP Caller", description="Display label for utterances from this stream")


class MeetingListenStatus(BaseModel):
    session_id: str
    twin_id: str
    meeting_id: str
    source: str
    status: str                    # running | done | stopped | error
    chunks_processed: int
    last_utterance_preview: str
    started_at: str
    finished_at: Optional[str] = None
    error_message: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


class MeetingListenStopResponse(BaseModel):
    stopped: bool
    session_id: Optional[str] = None
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
#  Sprint 3 — Twin speaks in meeting
# ---------------------------------------------------------------------------

class TwinRespondRequest(BaseModel):
    prompt: str = Field(..., description="What was said to the twin / question being asked")
    model: Optional[str] = Field(None, description="LLM model override")
    speak_aloud: bool = Field(True, description="Render TTS audio. False = think only")


class TwinRespondResponse(BaseModel):
    spoke: bool
    text: Optional[str] = None
    audio_url: Optional[str] = None
    is_commitment: Optional[bool] = None
    escalated: Optional[bool] = None
    wanted_to_say: Optional[str] = None
    authority: Optional[str] = None
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
#  Sprint 4 — Worker voice clone profile
# ---------------------------------------------------------------------------

class VoiceConsentRequest(BaseModel):
    consent_text: Optional[str] = Field(None, description="Custom consent text; defaults to KR template")


class VoiceProfileResponse(BaseModel):
    id: str
    user_id: str
    consent_given: bool
    consent_given_at: Optional[str] = None
    consent_revoked_at: Optional[str] = None
    sample_url: Optional[str] = None
    sample_duration_sec: Optional[int] = None
    sample_quality_score: Optional[float] = None
    status: str
    melotts_model_path: Optional[str] = None
    training_started_at: Optional[str] = None
    training_completed_at: Optional[str] = None
    failure_reason: Optional[str] = None
    created_at: Optional[str] = None


# ---------------------------------------------------------------------------
#  Sprint 8 — Auto-create meeting via assistant intent
# ---------------------------------------------------------------------------

class AutoCreateMeetingRequest(BaseModel):
    text: str = Field(..., description="Natural-language command (KR or EN), e.g. 'Let's have a meeting with Kim and Davronbek'")
    authority: TwinMeetingAuthority = TwinMeetingAuthority.answer_factual
    meeting_type: str = "all_hands"
    title: Optional[str] = None


class AutoCreateMeetingResponse(BaseModel):
    ok: bool
    reason: Optional[str] = None
    meeting_id: Optional[str] = None
    meeting_title: Optional[str] = None
    meeting_room_url: Optional[str] = None
    joined: list = Field(default_factory=list)
    skipped: list = Field(default_factory=list)
    unmatched_names: list = Field(default_factory=list)
    candidates: Optional[list] = None
    message: str
    korean_message: str


# ---------------------------------------------------------------------------
#  v3 — Groups
# ---------------------------------------------------------------------------

class GroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=160)
    description: Optional[str] = None
    avatar_color: Optional[str] = None


class GroupAddMember(BaseModel):
    user_id: UUID
    role: str = Field("member", description="member | admin")


class GroupMessageSend(BaseModel):
    content: str = Field(..., min_length=1)
    sender_type: str = Field("boss", description="boss | worker | twin | system")
    twins_only: bool = Field(False, description="If message contains a meeting request, mark it twins-only (off-day mode)")


# ---------------------------------------------------------------------------
#  v3 — Schedule meeting from chat
# ---------------------------------------------------------------------------

class ScheduleMeetingFromChat(BaseModel):
    text: str = Field(..., description="Natural-language meeting request, e.g. 'Lets meet in 10 minutes'")
    group_id: Optional[UUID] = None
    authority: TwinMeetingAuthority = TwinMeetingAuthority.answer_factual
    twins_only: bool = Field(False, description="Off-day mode: only twins attend (no human workers). Twins get full_proxy authority.")


# ---------------------------------------------------------------------------
#  v3 — Hand-raise (Zoom-style)
# ---------------------------------------------------------------------------

class AskInMeetingRequest(BaseModel):
    question: str = Field(..., min_length=1)
    threshold: Optional[float] = Field(None, ge=0, le=1)


class GrantFloorRequest(BaseModel):
    raise_id: UUID
    model: Optional[str] = None
