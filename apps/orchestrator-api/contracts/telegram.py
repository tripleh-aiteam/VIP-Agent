"""
VIP AI Platform — Telegram Contract
Payload format for all Telegram bot actions.
"""

from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class TelegramActionType(str, Enum):
    command = "command"
    approval = "approval"
    notification = "notification"
    query = "query"
    alert = "alert"


class TelegramActionPayload(BaseModel):
    """Payload for Telegram bot interactions — commands, approvals, notifications."""

    action_id: UUID = Field(default_factory=uuid4)
    telegram_user_id: str = Field(..., description="Telegram user ID")
    chat_id: str = Field(..., description="Telegram chat ID")
    action_type: TelegramActionType = Field(...)
    command: Optional[str] = Field(None, description="Bot command (e.g., /status, /approve)")
    args: list[str] = Field(default_factory=list, description="Command arguments")
    related_task_run_id: Optional[UUID] = None
    related_judgement_id: Optional[UUID] = None
    reply_text: Optional[str] = Field(None, description="Text to send back to user")
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    version: str = Field(default="1.0")

    model_config = {"json_schema_extra": {"examples": [
        {
            "telegram_user_id": "123456789",
            "chat_id": "-100987654321",
            "action_type": "command",
            "command": "/status",
            "args": ["portfolio", "PF-1234"],
        }
    ]}}
