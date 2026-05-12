"""
voice_service — multi-tenant CRUD + business logic for the Calling Agent.

EVERY public function takes `agent_id: str` as its first argument. The
service never assumes which agent it's serving. Callers (router + webhook
handler) resolve the agent_id from the URL path or the provider mapping
table BEFORE invoking these functions.

Pairs with:
  - db/models.py        — 6 SQLAlchemy models
  - routers/voice.py    — REST endpoints
  - services/campaign_runner.py — background dialer
  - services/voice_escalation.py — urgency → Telegram/Slack/email/webhook
  - services/voice_summary.py    — LLM-generated call summaries
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import and_, desc
from sqlalchemy.orm import Session

from db.models import (
    BatchCampaign,
    BatchRecipient,
    VoiceCall,
    VoiceCallTurn,
    VoiceProviderAssistant,
    VoiceRecording,
)


# ============================================================================
#  Provider mapping — webhook handler uses this to resolve agent_id
# ============================================================================

def resolve_agent_id_from_provider(
    db: Session, provider: str, provider_assistant_id: str
) -> Optional[str]:
    """
    Given a telephony provider + assistant ID (from a webhook payload),
    return our internal agent_id. Returns None if no active mapping exists
    — the webhook handler must reject the event in that case (likely a
    misconfigured Vapi assistant or a forged request).
    """
    row = (
        db.query(VoiceProviderAssistant)
        .filter(
            VoiceProviderAssistant.provider == provider,
            VoiceProviderAssistant.provider_assistant_id == provider_assistant_id,
            VoiceProviderAssistant.active.is_(True),
        )
        .first()
    )
    return row.agent_id if row else None


def register_provider_assistant(
    db: Session,
    agent_id: str,
    provider: str,
    provider_assistant_id: str,
    phone_number: str,
) -> VoiceProviderAssistant:
    """Idempotent: re-registering the same provider+assistant_id updates
    the row instead of creating a duplicate."""
    existing = (
        db.query(VoiceProviderAssistant)
        .filter(
            VoiceProviderAssistant.provider == provider,
            VoiceProviderAssistant.provider_assistant_id == provider_assistant_id,
        )
        .first()
    )
    if existing:
        existing.agent_id = agent_id
        existing.phone_number = phone_number
        existing.active = True
        db.commit()
        return existing
    row = VoiceProviderAssistant(
        agent_id=agent_id,
        provider=provider,
        provider_assistant_id=provider_assistant_id,
        phone_number=phone_number,
        active=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ============================================================================
#  Calls — list / get / start / end / patch
# ============================================================================

def list_calls(
    db: Session,
    agent_id: str,
    limit: int = 50,
    direction: Optional[str] = None,
    status: Optional[str] = None,
) -> list[VoiceCall]:
    """Recent call history for an agent. Returned newest-first."""
    q = db.query(VoiceCall).filter(VoiceCall.agent_id == agent_id)
    if direction:
        q = q.filter(VoiceCall.direction == direction)
    if status:
        q = q.filter(VoiceCall.status == status)
    return q.order_by(desc(VoiceCall.started_at)).limit(limit).all()


def get_active_call(db: Session, agent_id: str) -> Optional[VoiceCall]:
    """The currently-ringing or active call for this agent, if any."""
    return (
        db.query(VoiceCall)
        .filter(
            VoiceCall.agent_id == agent_id,
            VoiceCall.status.in_(["ringing", "active"]),
        )
        .order_by(desc(VoiceCall.started_at))
        .first()
    )


def get_call(db: Session, agent_id: str, call_id: UUID | str) -> Optional[VoiceCall]:
    return (
        db.query(VoiceCall)
        .filter(VoiceCall.agent_id == agent_id, VoiceCall.id == call_id)
        .first()
    )


def get_call_by_provider_id(
    db: Session, agent_id: str, provider: str, provider_call_id: str
) -> Optional[VoiceCall]:
    return (
        db.query(VoiceCall)
        .filter(
            VoiceCall.agent_id == agent_id,
            VoiceCall.provider == provider,
            VoiceCall.provider_call_id == provider_call_id,
        )
        .first()
    )


def start_call(
    db: Session,
    agent_id: str,
    *,
    provider: str,
    provider_call_id: Optional[str],
    direction: str,
    caller_number: str,
    caller_name: Optional[str] = None,
    caller_tag: Optional[str] = None,
    campaign_id: Optional[UUID] = None,
    recipient_id: Optional[UUID] = None,
    started_at: Optional[datetime] = None,
) -> VoiceCall:
    """Create a new VoiceCall in status=ringing. Idempotent on
    (agent_id, provider, provider_call_id) — duplicate webhook events
    return the existing row instead of inserting twice."""
    if provider_call_id:
        existing = get_call_by_provider_id(db, agent_id, provider, provider_call_id)
        if existing:
            return existing
    row = VoiceCall(
        agent_id=agent_id,
        provider=provider,
        provider_call_id=provider_call_id,
        direction=direction,
        status="ringing",
        caller_number=caller_number,
        caller_name=caller_name,
        caller_tag=caller_tag,
        started_at=started_at or datetime.utcnow(),
        campaign_id=campaign_id,
        recipient_id=recipient_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def mark_call_active(db: Session, agent_id: str, call_id: UUID | str) -> Optional[VoiceCall]:
    call = get_call(db, agent_id, call_id)
    if not call:
        return None
    call.status = "active"
    db.commit()
    db.refresh(call)
    return call


def end_call(
    db: Session,
    agent_id: str,
    call_id: UUID | str,
    *,
    status: str = "completed",
    duration_sec: Optional[int] = None,
    summary: Optional[str] = None,
    recording_url: Optional[str] = None,
    raw_event: Optional[dict[str, Any]] = None,
) -> Optional[VoiceCall]:
    call = get_call(db, agent_id, call_id)
    if not call:
        return None
    call.status = status
    call.ended_at = datetime.utcnow()
    if duration_sec is not None:
        call.duration_sec = duration_sec
    elif call.started_at:
        call.duration_sec = int((call.ended_at - call.started_at).total_seconds())
    if summary is not None:
        call.summary = summary
    if recording_url is not None:
        call.recording_url = recording_url
    if raw_event is not None:
        call.raw_provider_event = raw_event
    db.commit()
    db.refresh(call)
    return call


def patch_call(db: Session, agent_id: str, call_id: UUID | str, **fields) -> Optional[VoiceCall]:
    """Generic field patch — used by webhook handler for partial updates
    (urgency classification, needs_review flag, etc.)."""
    call = get_call(db, agent_id, call_id)
    if not call:
        return None
    for k, v in fields.items():
        if hasattr(call, k):
            setattr(call, k, v)
    db.commit()
    db.refresh(call)
    return call


# ============================================================================
#  Transcript turns
# ============================================================================

def list_turns(db: Session, agent_id: str, call_id: UUID | str) -> list[VoiceCallTurn]:
    """All transcript turns for a call. Scoped via call.agent_id check."""
    call = get_call(db, agent_id, call_id)
    if not call:
        return []
    return (
        db.query(VoiceCallTurn)
        .filter(VoiceCallTurn.call_id == call.id)
        .order_by(VoiceCallTurn.at.asc())
        .all()
    )


def upsert_turn(
    db: Session,
    agent_id: str,
    call_id: UUID | str,
    *,
    role: str,
    text: str,
    at: Optional[datetime] = None,
    confidence: Optional[float] = None,
    partial: bool = False,
    provider_turn_id: Optional[str] = None,
) -> Optional[VoiceCallTurn]:
    """Insert a new turn, or replace an existing partial turn with the
    same `provider_turn_id`. Vapi sends multiple `transcript.partial`
    events before the `transcript.final` — we keep one row per turn id
    and overwrite text/partial as they refine."""
    call = get_call(db, agent_id, call_id)
    if not call:
        return None
    existing: Optional[VoiceCallTurn] = None
    if provider_turn_id:
        existing = (
            db.query(VoiceCallTurn)
            .filter(
                VoiceCallTurn.call_id == call.id,
                VoiceCallTurn.provider_turn_id == provider_turn_id,
            )
            .first()
        )
    if existing:
        existing.text = text
        existing.partial = partial
        if confidence is not None:
            existing.confidence = confidence
        db.commit()
        db.refresh(existing)
        return existing
    row = VoiceCallTurn(
        call_id=call.id,
        role=role,
        text=text,
        at=at or datetime.utcnow(),
        confidence=confidence,
        partial=partial,
        provider_turn_id=provider_turn_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ============================================================================
#  Recordings
# ============================================================================

def upsert_recording(
    db: Session,
    agent_id: str,
    call_id: UUID | str,
    *,
    storage_path: str,
    size_bytes: Optional[int] = None,
    duration_sec: Optional[int] = None,
    format_: str = "mp3",
    signed_url: Optional[str] = None,
    signed_url_expires_at: Optional[datetime] = None,
    retention_expires_at: Optional[datetime] = None,
) -> Optional[VoiceRecording]:
    call = get_call(db, agent_id, call_id)
    if not call:
        return None
    existing = (
        db.query(VoiceRecording).filter(VoiceRecording.call_id == call.id).first()
    )
    if existing:
        existing.storage_path = storage_path
        if size_bytes is not None:
            existing.size_bytes = size_bytes
        if duration_sec is not None:
            existing.duration_sec = duration_sec
        if signed_url is not None:
            existing.signed_url = signed_url
        if signed_url_expires_at is not None:
            existing.signed_url_expires_at = signed_url_expires_at
        if retention_expires_at is not None:
            existing.retention_expires_at = retention_expires_at
        db.commit()
        db.refresh(existing)
        return existing
    row = VoiceRecording(
        call_id=call.id,
        agent_id=agent_id,
        storage_path=storage_path,
        size_bytes=size_bytes,
        duration_sec=duration_sec,
        format=format_,
        signed_url=signed_url,
        signed_url_expires_at=signed_url_expires_at,
        retention_expires_at=retention_expires_at,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ============================================================================
#  Batch campaigns
# ============================================================================

def list_campaigns(
    db: Session, agent_id: str, status: Optional[str] = None
) -> list[BatchCampaign]:
    q = db.query(BatchCampaign).filter(BatchCampaign.agent_id == agent_id)
    if status:
        q = q.filter(BatchCampaign.status == status)
    return q.order_by(desc(BatchCampaign.created_at)).all()


def list_running_campaigns_all_agents(db: Session) -> list[BatchCampaign]:
    """Used by campaign_runner — gets every running campaign across all
    agents in one query to amortize the scheduler tick cost."""
    return (
        db.query(BatchCampaign)
        .filter(BatchCampaign.status == "running")
        .all()
    )


def get_campaign(db: Session, agent_id: str, campaign_id: UUID | str) -> Optional[BatchCampaign]:
    return (
        db.query(BatchCampaign)
        .filter(BatchCampaign.agent_id == agent_id, BatchCampaign.id == campaign_id)
        .first()
    )


def create_campaign(
    db: Session,
    agent_id: str,
    *,
    name: str,
    reason: str,
    recipients: list[dict[str, Any]],
    pacing: int = 12,
    working_hours: Optional[dict[str, int]] = None,
    created_by: Optional[UUID] = None,
) -> tuple[BatchCampaign, list[dict[str, str]]]:
    """Create a campaign + queue all recipients. Returns the campaign
    plus a list of `skipped` recipients (those rejected by the
    per-recipient weekly cap — see check_recipient_eligibility).
    """
    campaign = BatchCampaign(
        agent_id=agent_id,
        name=name,
        reason=reason,
        status="idle",
        pacing=pacing,
        working_hours_json=working_hours or {"start": 9, "end": 21},
        created_by=created_by,
    )
    db.add(campaign)
    db.flush()       # need campaign.id before adding recipients

    skipped: list[dict[str, str]] = []
    for i, r in enumerate(recipients):
        number = (r.get("number") or "").strip()
        if not number:
            skipped.append({"number": "", "reason": "missing phone number"})
            continue
        # Rate-limit check — see Step 20
        eligible_reason = check_recipient_eligibility(db, agent_id, number)
        if eligible_reason:
            skipped.append({"number": number, "reason": eligible_reason})
            continue
        rec = BatchRecipient(
            campaign_id=campaign.id,
            name=r.get("name") or "",
            number=number,
            context_json=r.get("context") or {},
            status="queued",
            queue_order=i,
        )
        db.add(rec)
    db.commit()
    db.refresh(campaign)
    return campaign, skipped


def set_campaign_status(
    db: Session, agent_id: str, campaign_id: UUID | str, status: str
) -> Optional[BatchCampaign]:
    """Status transitions: idle → running → paused | running → completed.
    `stop` collapses to status=completed with a completed_at timestamp."""
    campaign = get_campaign(db, agent_id, campaign_id)
    if not campaign:
        return None
    campaign.status = status
    if status == "running" and not campaign.started_at:
        campaign.started_at = datetime.utcnow()
    if status == "completed":
        campaign.completed_at = datetime.utcnow()
    db.commit()
    db.refresh(campaign)
    return campaign


# ============================================================================
#  Batch recipients (queue ops)
# ============================================================================

def list_recipients(db: Session, campaign_id: UUID | str) -> list[BatchRecipient]:
    return (
        db.query(BatchRecipient)
        .filter(BatchRecipient.campaign_id == campaign_id)
        .order_by(BatchRecipient.queue_order.asc(), BatchRecipient.created_at.asc())
        .all()
    )


def next_queued_recipient(db: Session, campaign_id: UUID | str) -> Optional[BatchRecipient]:
    return (
        db.query(BatchRecipient)
        .filter(
            BatchRecipient.campaign_id == campaign_id,
            BatchRecipient.status == "queued",
        )
        .order_by(BatchRecipient.queue_order.asc(), BatchRecipient.created_at.asc())
        .first()
    )


def mark_recipient_calling(
    db: Session, recipient_id: UUID | str, call_id: UUID
) -> Optional[BatchRecipient]:
    rec = db.query(BatchRecipient).filter(BatchRecipient.id == recipient_id).first()
    if not rec:
        return None
    rec.status = "calling"
    rec.attempted_at = datetime.utcnow()
    rec.call_id = call_id
    db.commit()
    db.refresh(rec)
    return rec


def mark_recipient_outcome(
    db: Session,
    recipient_id: UUID | str,
    *,
    outcome: str,
    notes: Optional[str] = None,
    status: str = "completed",
) -> Optional[BatchRecipient]:
    rec = db.query(BatchRecipient).filter(BatchRecipient.id == recipient_id).first()
    if not rec:
        return None
    rec.status = status
    rec.outcome = outcome
    if notes is not None:
        rec.notes = notes
    db.commit()
    db.refresh(rec)
    return rec


def skip_recipient(db: Session, recipient_id: UUID | str) -> Optional[BatchRecipient]:
    rec = db.query(BatchRecipient).filter(BatchRecipient.id == recipient_id).first()
    if not rec or rec.status != "queued":
        return None
    rec.status = "skipped"
    db.commit()
    db.refresh(rec)
    return rec


# ============================================================================
#  Hard limits — see Step 20 for full enforcement
# ============================================================================

def check_recipient_eligibility(
    db: Session,
    agent_id: str,
    number: str,
    *,
    window_days: int = 7,
    max_calls: int = 1,
) -> Optional[str]:
    """Returns None if the recipient can be called now, or a string
    reason if they're blocked by the per-recipient rate limit.

    Default: max 1 outbound call from this agent to this number within
    7 days. Counts only outbound calls (inbound from the same person
    doesn't burn the quota).
    """
    since = datetime.utcnow() - timedelta(days=window_days)
    count = (
        db.query(VoiceCall)
        .filter(
            VoiceCall.agent_id == agent_id,
            VoiceCall.caller_number == number,
            VoiceCall.direction == "outbound",
            VoiceCall.started_at >= since,
        )
        .count()
    )
    if count >= max_calls:
        return f"already called {count}/{max_calls} times within last {window_days} days"
    return None


def is_within_working_hours(
    now_hour: int, working_hours: dict[str, int]
) -> bool:
    """Whether outbound calls are allowed at this hour. Inbound is
    always allowed regardless. `now_hour` is the agent's local hour
    (0-23) — caller is responsible for timezone conversion using
    AgentConfig.voice.workingHours.timezone."""
    start = working_hours.get("start", 9)
    end = working_hours.get("end", 21)
    return start <= now_hour < end


# ============================================================================
#  Stats — daily report card
# ============================================================================

def daily_report_summary(db: Session, agent_id: str) -> dict[str, Any]:
    """Last 24 hours summary for the daily-report card on the dashboard."""
    since = datetime.utcnow() - timedelta(hours=24)
    calls = (
        db.query(VoiceCall)
        .filter(VoiceCall.agent_id == agent_id, VoiceCall.started_at >= since)
        .all()
    )
    total = len(calls)
    resolved = sum(1 for c in calls if c.status == "completed")
    escalated = sum(1 for c in calls if c.status == "escalated")
    missed = sum(1 for c in calls if c.status == "missed")
    needs_review_count = sum(1 for c in calls if c.needs_review)
    longest = max(calls, key=lambda c: c.duration_sec or 0, default=None)

    # Top topics: not yet — would need LLM-extracted topic labels on each call.
    # Step 17's call-summary step will populate a `topic` field we can group on.
    top_topics: list[dict[str, Any]] = []
    return {
        "totalCalls": total,
        "resolved": resolved,
        "escalated": escalated,
        "missed": missed,
        "topTopics": top_topics,
        "longestCall": {
            "caller": (longest.caller_name or "Unknown") if longest else "",
            "durationSec": longest.duration_sec or 0 if longest else 0,
        },
        "needsReviewCount": needs_review_count,
    }


# ============================================================================
#  Serializers — convert ORM rows → wire-format dicts matching @triple-h/chatbot/voice-ui types
# ============================================================================

def serialize_call(call: VoiceCall, turns: Optional[list[VoiceCallTurn]] = None) -> dict[str, Any]:
    """Match the CallEvent shape in packages/chatbot/src/voice-ui/types.ts.
    Times serialized as Unix ms — JS expects that for the dashboard's
    duration math.
    """
    return {
        "id": str(call.id),
        "direction": call.direction,
        "status": call.status,
        "urgency": call.urgency,
        "caller": {
            "number": call.caller_number,
            "name": call.caller_name,
            "tag": call.caller_tag,
        },
        "startedAt": int(call.started_at.timestamp() * 1000) if call.started_at else 0,
        "endedAt": int(call.ended_at.timestamp() * 1000) if call.ended_at else None,
        "durationSec": call.duration_sec,
        "transcript": [serialize_turn(t) for t in (turns or [])],
        "summary": call.summary,
        "recordingUrl": call.recording_url,
        "escalation": call.escalation_json,
        "needsReview": bool(call.needs_review),
    }


def serialize_turn(turn: VoiceCallTurn) -> dict[str, Any]:
    return {
        "id": str(turn.id),
        "role": turn.role,
        "text": turn.text,
        "at": int(turn.at.timestamp() * 1000) if turn.at else 0,
        "confidence": turn.confidence,
        "partial": bool(turn.partial),
    }


def serialize_campaign(
    campaign: BatchCampaign, recipients: Optional[list[BatchRecipient]] = None
) -> dict[str, Any]:
    """Match the BatchCampaign shape in voice-ui/types.ts."""
    return {
        "id": str(campaign.id),
        "name": campaign.name,
        "reason": campaign.reason,
        "status": campaign.status,
        "recipients": [serialize_recipient(r) for r in (recipients or [])],
        "createdAt": int(campaign.created_at.timestamp() * 1000) if campaign.created_at else 0,
        "startedAt": int(campaign.started_at.timestamp() * 1000) if campaign.started_at else None,
        "completedAt": int(campaign.completed_at.timestamp() * 1000) if campaign.completed_at else None,
        "pacing": campaign.pacing,
        "workingHours": campaign.working_hours_json or {"start": 9, "end": 21},
    }


def serialize_recipient(rec: BatchRecipient) -> dict[str, Any]:
    return {
        "id": str(rec.id),
        "name": rec.name,
        "number": rec.number,
        "context": rec.context_json or {},
        "status": rec.status,
        "outcome": rec.outcome,
        "notes": rec.notes,
        "callId": str(rec.call_id) if rec.call_id else None,
        "attemptedAt": int(rec.attempted_at.timestamp() * 1000) if rec.attempted_at else None,
    }
