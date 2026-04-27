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
