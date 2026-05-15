"""
chatbot_mode_detector — Boss-IN / Boss-OUT mode resolution.

Two layers of decision:

1. **Manual override** (DB-backed in chatbot_agent_settings): the boss
   explicitly clicks "Boss-OUT" on the dashboard (with optional reason
   like "in meeting" and optional auto-expiry like "back in 2 hours").
   This takes priority over auto-detect and survives orchestrator restarts.

2. **Auto-detect** (fallback): time-of-day rule. Mon-Fri 09:00-18:00 KST
   = IN; everything else = OUT. Customizable via env vars
   CHATBOT_WORKING_HOURS_START / _END / _TZ_OFFSET.

The DB override expires automatically at `mode_expires_at` — the
chatbot_mode_expiry scheduler tick clears expired rows daily so
end-of-meeting transitions are seamless.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from db.base import SessionLocal
from db.models import ChatbotAgentSetting
from services.logger import log


BossMode = Literal["in", "out"]


# Valid mode reasons (matches dashboard dropdown). Free text via mode_reason_note.
MODE_REASONS = {
    "meeting": "외부 미팅 (External meeting)",
    "lunch": "점심 시간 (Lunch)",
    "off_day": "휴무 (Off day)",
    "vacation": "휴가 (Vacation)",
    "after_hours": "퇴근 (After hours)",
    "other": "기타 (Other)",
}


# ============================================================================
#  Public API
# ============================================================================

def get_mode(
    agent_id: str,
    *,
    db: Optional[Session] = None,
    now: Optional[datetime] = None,
) -> tuple[BossMode, bool]:
    """Return (mode, auto_detected) for an agent.
    `auto_detected` is True when no manual override is active OR the
    override has expired."""
    now = now or datetime.utcnow()
    owned_db = False
    if db is None:
        db = SessionLocal()
        owned_db = True
    try:
        setting = (
            db.query(ChatbotAgentSetting)
            .filter(ChatbotAgentSetting.agent_id == agent_id)
            .first()
        )
        if setting and setting.mode_override:
            # Has the override expired?
            expired = (
                setting.mode_expires_at is not None
                and setting.mode_expires_at <= now
            )
            if not expired:
                return (setting.mode_override, False)  # type: ignore[return-value]
            # Expired — fall through to auto-detect (also clear the row so we
            # don't keep checking on every request)
            setting.mode_override = None
            setting.mode_reason = None
            setting.mode_reason_note = None
            setting.mode_expires_at = None
            db.commit()

        # Auto-detect path
        if setting and setting.auto_mode_enabled is False:
            # Auto-detect is disabled and no override → default to IN
            # (user must explicitly flip; we'd rather show the inbox to boss
            # than have the bot send autonomous replies without consent)
            return ("in", False)
        return (auto_detect_mode(now), True)
    finally:
        if owned_db:
            db.close()


def set_manual_mode(
    agent_id: str,
    mode: BossMode,
    *,
    reason: Optional[str] = None,
    reason_note: Optional[str] = None,
    expires_in_hours: Optional[float] = None,
    user_id: Optional[UUID] = None,
    db: Optional[Session] = None,
) -> ChatbotAgentSetting:
    """Pin the agent to `mode`. Persistent across restarts.

    Args:
      mode: "in" or "out"
      reason: short reason code (see MODE_REASONS keys)
      reason_note: free text — used when reason="other"
      expires_in_hours: when to auto-revert to auto-detect. None = indefinite.
      user_id: who set this override (for audit log)
    """
    owned_db = False
    if db is None:
        db = SessionLocal()
        owned_db = True
    try:
        expires_at = None
        if expires_in_hours is not None and expires_in_hours > 0:
            expires_at = datetime.utcnow() + timedelta(hours=expires_in_hours)

        setting = (
            db.query(ChatbotAgentSetting)
            .filter(ChatbotAgentSetting.agent_id == agent_id)
            .first()
        )
        if not setting:
            setting = ChatbotAgentSetting(agent_id=agent_id)
            db.add(setting)
        setting.mode_override = mode
        setting.mode_reason = reason
        setting.mode_reason_note = reason_note
        setting.mode_expires_at = expires_at
        setting.updated_by = user_id
        db.commit()
        db.refresh(setting)
        log.info(
            f"chatbot_mode: {agent_id} → {mode} "
            f"(reason={reason or 'none'}, expires={expires_at or 'never'})",
            extra={"action": "chatbot.mode_set"},
        )
        return setting
    finally:
        if owned_db:
            db.close()


def clear_manual_mode(
    agent_id: str,
    *,
    user_id: Optional[UUID] = None,
    db: Optional[Session] = None,
) -> Optional[ChatbotAgentSetting]:
    """Return to auto-detect for an agent. Drops override + reason + expiry."""
    owned_db = False
    if db is None:
        db = SessionLocal()
        owned_db = True
    try:
        setting = (
            db.query(ChatbotAgentSetting)
            .filter(ChatbotAgentSetting.agent_id == agent_id)
            .first()
        )
        if not setting:
            return None
        setting.mode_override = None
        setting.mode_reason = None
        setting.mode_reason_note = None
        setting.mode_expires_at = None
        setting.updated_by = user_id
        db.commit()
        db.refresh(setting)
        log.info(
            f"chatbot_mode: {agent_id} → auto-detect (cleared override)",
            extra={"action": "chatbot.mode_cleared"},
        )
        return setting
    finally:
        if owned_db:
            db.close()


def get_setting(
    agent_id: str, *, db: Optional[Session] = None
) -> Optional[ChatbotAgentSetting]:
    """Read the full settings row — used by the dashboard to display the
    current reason + expiry banner."""
    owned_db = False
    if db is None:
        db = SessionLocal()
        owned_db = True
    try:
        return (
            db.query(ChatbotAgentSetting)
            .filter(ChatbotAgentSetting.agent_id == agent_id)
            .first()
        )
    finally:
        if owned_db:
            db.close()


def auto_detect_mode(now: Optional[datetime] = None) -> BossMode:
    """Korean working hours: Mon-Fri 09:00-18:00 KST → IN, else OUT.

    Hours and timezone are configurable via env for non-KR agents:
      CHATBOT_WORKING_HOURS_START (default 9)
      CHATBOT_WORKING_HOURS_END   (default 18)
      CHATBOT_WORKING_TZ_OFFSET   (default 9 = KST = UTC+9)
    """
    now = now or datetime.utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    tz_offset_hours = int(os.getenv("CHATBOT_WORKING_TZ_OFFSET", "9"))
    start_hour = int(os.getenv("CHATBOT_WORKING_HOURS_START", "9"))
    end_hour = int(os.getenv("CHATBOT_WORKING_HOURS_END", "18"))

    local_time = now + timedelta(hours=tz_offset_hours)
    weekday = local_time.weekday()              # Mon=0 ... Sun=6
    hour = local_time.hour

    is_weekday = weekday < 5
    is_working_hour = start_hour <= hour < end_hour
    return "in" if (is_weekday and is_working_hour) else "out"


def is_urgent_keyword(text: str) -> bool:
    """Quick check for Korean + English keywords that immediately
    flag a message as urgent. Caller can combine with LLM classification
    for nuanced cases."""
    if not text:
        return False
    lower = text.lower()
    urgent_kr = ["계약금", "긴급", "지금 당장", "당장", "응급", "사고", "위급", "법적", "고소", "민원"]
    urgent_en = ["urgent", "asap", "right now", "emergency", "legal", "lawsuit", "complaint"]
    for k in urgent_kr + urgent_en:
        if k in lower:
            return True
    return False


# ============================================================================
#  Scheduler tick — clear expired overrides
# ============================================================================

def expire_overdue_overrides() -> int:
    """Scheduler entry point. Clears mode_override on rows where
    mode_expires_at has passed. Returns count of rows cleared.

    Runs frequently (every minute) so end-of-meeting transitions feel
    immediate. Lightweight — single UPDATE."""
    db = SessionLocal()
    cleared = 0
    try:
        now = datetime.utcnow()
        expired_rows = (
            db.query(ChatbotAgentSetting)
            .filter(
                ChatbotAgentSetting.mode_override.isnot(None),
                ChatbotAgentSetting.mode_expires_at.isnot(None),
                ChatbotAgentSetting.mode_expires_at <= now,
            )
            .all()
        )
        for row in expired_rows:
            row.mode_override = None
            row.mode_reason = None
            row.mode_reason_note = None
            row.mode_expires_at = None
            cleared += 1
        if cleared:
            db.commit()
            log.info(
                f"chatbot_mode: cleared {cleared} expired overrides",
                extra={"action": "chatbot.mode_expired_cleared"},
            )
            # Broadcast mode.changed events so subscribed dashboards refresh
            try:
                from routers.chatbot_inbox import get_broker
                broker = get_broker()
                for row in expired_rows:
                    new_mode = auto_detect_mode()
                    broker.publish_sync(
                        row.agent_id,
                        {"type": "mode.changed", "mode": new_mode, "autoDetected": True},
                    )
            except Exception:
                pass
    finally:
        db.close()
    return cleared
