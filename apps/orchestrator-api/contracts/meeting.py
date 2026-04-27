"""
VIP AI Platform — Meeting Contracts
Pydantic schemas for meeting creation, messages, minutes, and participants.
"""

from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
#  Enums
# ---------------------------------------------------------------------------

class MeetingType(str, Enum):
    all_hands = "all_hands"
    team = "team"
    one_on_one = "one_on_one"
    standup = "standup"
    weekly_review = "weekly_review"


class MeetingStatus(str, Enum):
    scheduled = "scheduled"
    active = "active"
    ended = "ended"


# ---------------------------------------------------------------------------
#  Meeting Schemas
# ---------------------------------------------------------------------------

class MeetingCreate(BaseModel):
    title: str = Field(..., description="Meeting title")
    meeting_type: MeetingType = MeetingType.all_hands
    scheduled_at: Optional[datetime] = None
    twin_ids: list[UUID] = Field(default_factory=list, description="Twins to invite")


class MeetingMessageSend(BaseModel):
    content: str = Field(..., description="Message content from boss")


class MeetingMessageResponse(BaseModel):
    id: UUID
    meeting_id: UUID
    sender_type: str             # vip | twin
    sender_twin_id: Optional[UUID]
    sender_twin_name: Optional[str] = None
    content: str
    created_at: datetime

    class Config:
        from_attributes = True


class MeetingParticipantResponse(BaseModel):
    twin_id: UUID
    twin_name: str
    twin_role: str
    twin_status: str
    joined_at: datetime

    class Config:
        from_attributes = True


class MeetingMinutesResponse(BaseModel):
    id: UUID
    meeting_id: UUID
    decisions: list
    tasks_assigned: list
    open_questions: list
    summary: Optional[str]
    generated_at: datetime

    class Config:
        from_attributes = True


class MeetingResponse(BaseModel):
    id: UUID
    title: str
    meeting_type: str
    status: str
    scheduled_at: Optional[datetime]
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    created_by: str
    participant_count: int = 0
    message_count: int = 0
    created_at: datetime

    class Config:
        from_attributes = True
