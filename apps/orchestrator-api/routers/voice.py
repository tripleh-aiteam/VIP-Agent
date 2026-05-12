"""
Voice / Calling Agent API.

Endpoints scoped per agent_id from the URL path:

  GET    /api/voice/{agent_id}/calls?limit=N
  GET    /api/voice/{agent_id}/calls/active
  GET    /api/voice/{agent_id}/calls/{call_id}
  GET    /api/voice/{agent_id}/daily-report
  POST   /api/voice/{agent_id}/outbound
  POST   /api/voice/{agent_id}/calls/{call_id}/take-over
  POST   /api/voice/{agent_id}/calls/{call_id}/escalate
  POST   /api/voice/{agent_id}/calls/{call_id}/review
  POST   /api/voice/{agent_id}/campaigns
  GET    /api/voice/{agent_id}/campaigns/{campaign_id}
  POST   /api/voice/{agent_id}/campaigns/{campaign_id}/{pause|resume|stop}

Provider-side:
  POST   /api/voice/webhook                    (Vapi → us)
  WS     /ws/voice/{agent_id}/calls            (us → admin dashboard)

The webhook handler resolves `agent_id` by looking up the inbound
`assistant_id` in voice_provider_assistants. The path-scoped REST
endpoints filter every query by the path's {agent_id} so cross-tenant
reads are impossible.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from db.base import get_db
from services import voice_service
from services.logger import log


router = APIRouter(prefix="/api/voice", tags=["voice"])


# ============================================================================
#  WebSocket broadcaster — multi-tenant pub/sub for live call updates
# ============================================================================

class _VoiceWsBroker:
    """In-memory per-agent broadcaster. One process for now; if we scale
    the orchestrator horizontally, swap this for a Redis pub/sub layer
    using the same publish/subscribe contract."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, agent_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self._subscribers.setdefault(agent_id, set()).add(ws)

    async def unsubscribe(self, agent_id: str, ws: WebSocket) -> None:
        async with self._lock:
            subs = self._subscribers.get(agent_id)
            if subs:
                subs.discard(ws)
                if not subs:
                    self._subscribers.pop(agent_id, None)

    async def publish(self, agent_id: str, event: dict[str, Any]) -> None:
        """Send the event JSON to every subscriber of this agent_id.
        Dead sockets are removed silently on send error."""
        subs = list(self._subscribers.get(agent_id, ()))
        if not subs:
            return
        payload = json.dumps(event, default=str)
        for ws in subs:
            try:
                await ws.send_text(payload)
            except Exception:
                await self.unsubscribe(agent_id, ws)

    def publish_sync(self, agent_id: str, event: dict[str, Any]) -> None:
        """Sync entry point — schedules the publish on the running event
        loop. Used by webhook handlers + campaign runner which may run
        in non-async contexts."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.publish(agent_id, event))
        except RuntimeError:
            # No running loop (rare — e.g. scheduler thread before lifespan).
            # Drop the event; reconnecting clients hydrate via REST.
            pass


_broker = _VoiceWsBroker()


def get_broker() -> _VoiceWsBroker:
    """Module-level singleton accessor — campaign_runner.py + the webhook
    handler grab this to publish."""
    return _broker


# ============================================================================
#  GET — reads (call history, active call, single call, daily report)
# ============================================================================

@router.get("/{agent_id}/calls")
def list_calls(agent_id: str, limit: int = 50, db: Session = Depends(get_db)):
    rows = voice_service.list_calls(db, agent_id, limit=limit)
    return [voice_service.serialize_call(c) for c in rows]


@router.get("/{agent_id}/calls/active")
def get_active_call(agent_id: str, db: Session = Depends(get_db)):
    call = voice_service.get_active_call(db, agent_id)
    if not call:
        # Dashboard treats 404 as "no active call right now"
        raise HTTPException(status_code=404, detail="No active call")
    turns = voice_service.list_turns(db, agent_id, call.id)
    return voice_service.serialize_call(call, turns)


@router.get("/{agent_id}/calls/{call_id}")
def get_call(agent_id: str, call_id: UUID, db: Session = Depends(get_db)):
    call = voice_service.get_call(db, agent_id, call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    turns = voice_service.list_turns(db, agent_id, call.id)
    return voice_service.serialize_call(call, turns)


@router.get("/{agent_id}/daily-report")
def daily_report(agent_id: str, db: Session = Depends(get_db)):
    return voice_service.daily_report_summary(db, agent_id)


# ============================================================================
#  POST — single outbound call
# ============================================================================

class OutboundDraft(BaseModel):
    to: str = Field(..., description="E.164 phone number")
    callerName: Optional[str] = None
    reason: str = Field(..., description="Reason ID from AgentConfig.voice.outboundReasons")
    context: Optional[dict[str, Any]] = None
    scheduledFor: Optional[str] = Field(
        None, description="ISO 8601 timestamp — omit for immediate dial"
    )


@router.post("/{agent_id}/outbound")
def place_outbound(agent_id: str, body: OutboundDraft, db: Session = Depends(get_db)):
    # Rate-limit check via per-recipient cap
    block_reason = voice_service.check_recipient_eligibility(db, agent_id, body.to)
    if block_reason:
        raise HTTPException(status_code=409, detail=f"Rate-limited: {block_reason}")

    # Look up the agent's Vapi assistant + phone number from voice_provider_assistants.
    # Without this row, we can't place real calls — fall back to "record intent only"
    # so smoke-testing the UI doesn't require a configured Vapi account.
    from db.models import VoiceProviderAssistant
    mapping = (
        db.query(VoiceProviderAssistant)
        .filter(
            VoiceProviderAssistant.agent_id == agent_id,
            VoiceProviderAssistant.provider == "vapi",
            VoiceProviderAssistant.active.is_(True),
        )
        .first()
    )

    # Create the call row in status=ringing — written BEFORE Vapi POST so a
    # webhook event that races back finds it.
    call = voice_service.start_call(
        db,
        agent_id,
        provider="vapi",
        provider_call_id=None,    # filled below once Vapi confirms
        direction="outbound",
        caller_number=body.to,
        caller_name=body.callerName,
    )

    if mapping:
        try:
            provider_call_id = _dispatch_outbound(
                provider=mapping.provider,
                provider_assistant_id=mapping.provider_assistant_id,
                agent_id=agent_id,
                to_number=body.to,
                customer_name=body.callerName,
                metadata={
                    "agent_id": agent_id,
                    "call_id": str(call.id),
                    "reason": body.reason,
                    "context": body.context or {},
                },
            )
            voice_service.patch_call(db, agent_id, call.id, provider_call_id=provider_call_id)
            call = voice_service.get_call(db, agent_id, call.id)
        except Exception as e:
            log.warning(
                f"voice.outbound: provider place_outbound failed: {e}",
                extra={"action": "voice.outbound_provider_failed"},
            )
            voice_service.patch_call(db, agent_id, call.id, status="failed")
            raise HTTPException(status_code=502, detail=f"Outbound call failed: {e}")
    else:
        log.info(
            f"voice.outbound: no voice_provider_assistants row for {agent_id} — "
            "recording intent only (configure provider to actually dial)",
            extra={"action": "voice.outbound_no_provider"},
        )

    # Broadcast call.started so any open dashboard sees it appear
    _broker.publish_sync(
        agent_id,
        {"type": "call.started", "call": voice_service.serialize_call(call, [])},
    )
    log.info(
        f"voice: outbound queued for {agent_id} → {body.to} (reason={body.reason})",
        extra={"action": "voice.outbound_queued", "agent_id": agent_id},
    )
    return voice_service.serialize_call(call, [])


# ============================================================================
#  POST — live-call actions
# ============================================================================

@router.post("/{agent_id}/calls/{call_id}/take-over")
def take_over(agent_id: str, call_id: UUID, db: Session = Depends(get_db)):
    call = voice_service.get_call(db, agent_id, call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    # TODO(Step 22): services.vapi_client.transfer_call(call.provider_call_id, target_number)
    return {"ok": True, "transferredTo": "human (TODO when vapi_client lands)"}


class EscalateBody(BaseModel):
    reason: Optional[str] = "Marked urgent by operator"


@router.post("/{agent_id}/calls/{call_id}/escalate")
def escalate(agent_id: str, call_id: UUID, body: EscalateBody, db: Session = Depends(get_db)):
    call = voice_service.get_call(db, agent_id, call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    from services import voice_escalation
    target = voice_escalation.escalate(db, call, reason=body.reason or "")
    return {"ok": True, "escalatedTo": target}


class ReviewBody(BaseModel):
    verdict: str = Field(..., pattern="^(correct|improve)$")
    note: Optional[str] = None


@router.post("/{agent_id}/calls/{call_id}/review")
def review(agent_id: str, call_id: UUID, body: ReviewBody, db: Session = Depends(get_db)):
    call = voice_service.get_call(db, agent_id, call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    voice_service.patch_call(db, agent_id, call.id, needs_review=False)
    # Feed the improvement back into the agent's knowledge — reuse the
    # chatbot's auto-knowledge pipeline so the bot doesn't repeat the mistake.
    if body.verdict == "improve" and body.note:
        try:
            from services.chatbot_self_improve import register_auto_example
            register_auto_example(
                db,
                agent_id=agent_id,
                intent="voice_correction",
                example_text=body.note,
                source="correction",
                confidence=1.0,
            )
        except Exception:
            pass    # don't fail the review on telemetry hiccup
    return {"ok": True}


# ============================================================================
#  POST/GET — batch campaigns
# ============================================================================

class CreateCampaignBody(BaseModel):
    name: str
    reason: str
    recipients: list[dict[str, Any]]
    pacing: Optional[int] = None
    workingHours: Optional[dict[str, int]] = None


@router.post("/{agent_id}/campaigns")
def create_campaign(agent_id: str, body: CreateCampaignBody, db: Session = Depends(get_db)):
    campaign, skipped = voice_service.create_campaign(
        db,
        agent_id,
        name=body.name,
        reason=body.reason,
        recipients=body.recipients,
        pacing=body.pacing or 12,
        working_hours=body.workingHours,
    )
    recipients = voice_service.list_recipients(db, campaign.id)
    return {
        "campaign": voice_service.serialize_campaign(campaign, recipients),
        "skipped": skipped,
    }


@router.get("/{agent_id}/campaigns/{campaign_id}")
def get_campaign(agent_id: str, campaign_id: UUID, db: Session = Depends(get_db)):
    campaign = voice_service.get_campaign(db, agent_id, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    recipients = voice_service.list_recipients(db, campaign.id)
    return voice_service.serialize_campaign(campaign, recipients)


@router.post("/{agent_id}/campaigns/import")
async def import_campaign_csv(
    agent_id: str,
    name: str = Form(...),
    reason: str = Form(...),
    pacing: int = Form(12),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Bulk-import a recipient list from CSV.

    Expected columns (header row required):
        name, number, amount, lease, dueDate
    Only `name` + `number` are required. Any additional columns are
    folded into the recipient's `context` dict (used by the script
    template as `{amount}` / `{dueDate}` / etc.).

    Per-recipient rate-limit rejections are returned in `skipped[]`.
    """
    import csv
    import io

    raw = await file.read()
    if len(raw) > 2 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="CSV too large (max 2 MB)")

    try:
        text = raw.decode("utf-8-sig")     # handle BOM from Excel exports
    except UnicodeDecodeError:
        try:
            text = raw.decode("cp949")     # Korean Excel default
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="Could not decode CSV (try UTF-8 or CP949)")

    reader = csv.DictReader(io.StringIO(text))
    fieldnames = {(f or "").strip().lower() for f in (reader.fieldnames or [])}
    if "number" not in fieldnames:
        raise HTTPException(
            status_code=400,
            detail="CSV must include a 'number' column (phone number, E.164 or 010-XXXX format)",
        )

    recipients: list[dict[str, Any]] = []
    for row in reader:
        # Lowercase keys for tolerance — accept Number / number / NUMBER
        normalized = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        number = normalized.pop("number", "")
        name_value = normalized.pop("name", "")
        if not number:
            continue
        recipients.append({
            "name": name_value,
            "number": number,
            "context": normalized,        # all remaining columns become context
        })

    if not recipients:
        raise HTTPException(status_code=400, detail="No recipients found in CSV")

    campaign, skipped = voice_service.create_campaign(
        db, agent_id,
        name=name, reason=reason,
        recipients=recipients,
        pacing=pacing,
    )
    full_recipients = voice_service.list_recipients(db, campaign.id)
    return {
        "campaign": voice_service.serialize_campaign(campaign, full_recipients),
        "skipped": skipped,
        "imported": len(full_recipients),
    }


@router.post("/{agent_id}/campaigns/{campaign_id}/pause")
def pause_campaign(agent_id: str, campaign_id: UUID, db: Session = Depends(get_db)):
    campaign = voice_service.set_campaign_status(db, agent_id, campaign_id, "paused")
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    recipients = voice_service.list_recipients(db, campaign.id)
    payload = voice_service.serialize_campaign(campaign, recipients)
    _broker.publish_sync(agent_id, {"type": "campaign.progress", "campaign": payload})
    return payload


@router.post("/{agent_id}/campaigns/{campaign_id}/resume")
def resume_campaign(agent_id: str, campaign_id: UUID, db: Session = Depends(get_db)):
    campaign = voice_service.set_campaign_status(db, agent_id, campaign_id, "running")
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    recipients = voice_service.list_recipients(db, campaign.id)
    payload = voice_service.serialize_campaign(campaign, recipients)
    _broker.publish_sync(agent_id, {"type": "campaign.progress", "campaign": payload})
    return payload


@router.post("/{agent_id}/campaigns/{campaign_id}/stop")
def stop_campaign(agent_id: str, campaign_id: UUID, db: Session = Depends(get_db)):
    campaign = voice_service.set_campaign_status(db, agent_id, campaign_id, "completed")
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    recipients = voice_service.list_recipients(db, campaign.id)
    payload = voice_service.serialize_campaign(campaign, recipients)
    _broker.publish_sync(agent_id, {"type": "campaign.progress", "campaign": payload})
    return payload


# ============================================================================
#  Provider dispatch — same shape, different telephony backend
# ============================================================================

def _dispatch_outbound(
    *,
    provider: str,
    provider_assistant_id: str,
    agent_id: str,
    to_number: str,
    customer_name: Optional[str],
    metadata: dict[str, Any],
) -> Optional[str]:
    """Place an outbound call via the agent's configured provider. Returns
    the provider's call/conversation ID for storage in
    voice_calls.provider_call_id. Raises on failure (caller marks the
    call row as failed).
    """
    env_suffix = agent_id.upper()
    if provider == "elevenlabs":
        from services import elevenlabs_client
        phone_number_id = os.getenv(f"ELEVENLABS_PHONE_NUMBER_ID_{env_suffix}", "")
        if not phone_number_id:
            raise elevenlabs_client.ElevenLabsClientError(
                f"ELEVENLABS_PHONE_NUMBER_ID_{env_suffix} env var not set "
                "(look it up in the ElevenLabs console once per agent)"
            )
        result = elevenlabs_client.place_outbound_call(
            agent_id=provider_assistant_id,
            agent_phone_number_id=phone_number_id,
            to_number=to_number,
            customer_name=customer_name,
            dynamic_variables=metadata,
        )
        return result.get("conversation_id")

    if provider == "vapi":
        from services import vapi_client
        phone_number_id = os.getenv(f"VAPI_PHONE_NUMBER_ID_{env_suffix}", "")
        if not phone_number_id:
            raise vapi_client.VapiClientError(
                f"VAPI_PHONE_NUMBER_ID_{env_suffix} env var not set "
                "(look it up in the Vapi console once per agent)"
            )
        result = vapi_client.place_outbound_call(
            assistant_id=provider_assistant_id,
            phone_number_id=phone_number_id,
            to_number=to_number,
            customer_name=customer_name,
            metadata=metadata,
        )
        return result.get("id")

    if provider == "selfhosted":
        # Outbound for self-hosted goes through Asterisk's ARI (REST control
        # interface). Asterisk originates a channel into the outbound-to-kt
        # context (see infra/asterisk/extensions.conf), which dials KT and
        # bridges audio through AudioSocket back into voice_pipeline.py.
        from services import selfhosted_voice_client
        return selfhosted_voice_client.originate_outbound_call(
            agent_id=agent_id,
            to_number=to_number,
            customer_name=customer_name,
            metadata=metadata,
        )

    raise HTTPException(status_code=500, detail=f"Unsupported voice provider: {provider}")


# ============================================================================
#  ElevenLabs webhook — `post_call_transcription` event arrives here
# ============================================================================

def _verify_elevenlabs_signature(raw_body: bytes, signature_header: Optional[str]) -> bool:
    """HMAC-SHA256 verify against ELEVENLABS_WEBHOOK_SECRET.

    ElevenLabs sends a header of the form:
        t=<unix-timestamp>,v0=<hex-signature>
    where the signature is HMAC-SHA256 of `<timestamp>.<raw_body>`.

    Returns True when no secret is configured (dev mode) so local smoke
    tests work without setup; production must set ELEVENLABS_WEBHOOK_SECRET.
    """
    secret = os.getenv("ELEVENLABS_WEBHOOK_SECRET", "")
    if not secret:
        return True
    if not signature_header:
        return False
    parts = dict(p.split("=", 1) for p in signature_header.split(",") if "=" in p)
    timestamp = parts.get("t", "")
    provided = parts.get("v0", "")
    if not timestamp or not provided:
        return False
    signed_payload = f"{timestamp}.".encode("utf-8") + raw_body
    expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)


@router.post("/webhook/elevenlabs")
async def elevenlabs_webhook(request: Request, db: Session = Depends(get_db)):
    """ElevenLabs Conversational AI webhook handler.

    Single event type today — `post_call_transcription` — fires AFTER the
    call ends with the full transcript + LLM-generated summary already in
    the payload. We:
      1. Verify the HMAC signature
      2. Resolve agent_id via voice_provider_assistants (lookup by
         ElevenLabs agent_id from the payload)
      3. Upsert the voice_calls row + transcript turns
      4. Trigger escalation if urgency=high
      5. Broadcast `call.ended` over the WebSocket

    Live transcript streaming during the call is NOT handled here —
    ElevenLabs exposes that via WebSocket, not webhook. Add it in v1.3
    if live mid-call viewing becomes a requirement.
    """
    raw = await request.body()
    signature = request.headers.get("elevenlabs-signature") or request.headers.get("ElevenLabs-Signature")
    if not _verify_elevenlabs_signature(raw, signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        payload = json.loads(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed JSON")

    event_type = payload.get("type")
    data = payload.get("data") or {}
    provider_assistant_id = data.get("agent_id") or ""
    conversation_id = data.get("conversation_id") or ""

    if not provider_assistant_id or not conversation_id:
        return {"ok": True, "skipped": "missing agent_id or conversation_id"}

    agent_id = voice_service.resolve_agent_id_from_provider(
        db, "elevenlabs", provider_assistant_id
    )
    if not agent_id:
        log.warning(
            f"elevenlabs.webhook: unknown agent {provider_assistant_id}",
            extra={"action": "elevenlabs.webhook_unknown_agent"},
        )
        return {"ok": True, "skipped": "unknown agent"}

    if event_type != "post_call_transcription":
        log.info(
            f"elevenlabs.webhook: unhandled event type {event_type}",
            extra={"action": "elevenlabs.webhook_unhandled"},
        )
        return {"ok": True, "skipped": f"unhandled event type {event_type}"}

    # 1. Find or create the call row (it usually exists already from
    #    place_outbound — inbound calls land here for the first time).
    call = voice_service.get_call_by_provider_id(db, agent_id, "elevenlabs", conversation_id)
    metadata = data.get("metadata") or {}
    if not call:
        # Inbound: caller dialed our number, no row exists yet
        started_unix = metadata.get("start_time_unix_secs") or 0
        from datetime import datetime as _dt
        started_at = (
            _dt.utcfromtimestamp(started_unix) if started_unix else _dt.utcnow()
        )
        # Pull caller number from conversation_initiation_client_data if present
        client_data = data.get("conversation_initiation_client_data") or {}
        dyn_vars = (client_data.get("dynamic_variables") or {})
        caller_number = dyn_vars.get("system__caller_id") or dyn_vars.get("caller_number") or ""
        caller_name = dyn_vars.get("customer_name") or ""
        call = voice_service.start_call(
            db,
            agent_id,
            provider="elevenlabs",
            provider_call_id=conversation_id,
            direction="inbound",      # post-call event from inbound caller; outbound rows pre-exist
            caller_number=caller_number,
            caller_name=caller_name or None,
            started_at=started_at,
        )

    # 2. Insert each transcript turn (ElevenLabs sends them in order).
    transcript = data.get("transcript") or []
    base_ts_secs = metadata.get("start_time_unix_secs") or 0
    for idx, t in enumerate(transcript):
        role = "bot" if t.get("role") == "agent" else "user"
        text = (t.get("message") or "").strip()
        if not text:
            continue
        offset_secs = t.get("time_in_call_secs") or 0
        from datetime import datetime as _dt
        at = _dt.utcfromtimestamp(base_ts_secs + offset_secs) if base_ts_secs else _dt.utcnow()
        voice_service.upsert_turn(
            db,
            agent_id,
            call.id,
            role=role,
            text=text,
            at=at,
            partial=False,
            provider_turn_id=f"{conversation_id}:{idx}",
        )

    # 3. Patch call with end metadata + the LLM-generated summary already in payload
    analysis = data.get("analysis") or {}
    duration = metadata.get("call_duration_secs")
    call_status = "completed"
    termination = (metadata.get("termination_reason") or "").lower()
    if "no_answer" in termination or "no-answer" in termination:
        call_status = "missed"
    elif "fail" in termination or "error" in termination:
        call_status = "failed"

    call = voice_service.end_call(
        db,
        agent_id,
        call.id,
        status=call_status,
        duration_sec=int(duration) if duration else None,
        summary=analysis.get("transcript_summary"),
        raw_event=payload,
    )

    # 4. Urgency heuristic — ElevenLabs returns analysis.call_successful + we
    #    look for the same Korean keywords as voice_summary.py to classify.
    try:
        from services import voice_summary
        # The LLM summary is already in payload — we still call our summary
        # service to classify urgency + needs_review against our own thresholds.
        voice_summary.generate_and_store_summary(db, agent_id, call.id)
        call = voice_service.get_call(db, agent_id, call.id)
    except Exception as e:
        log.warning(f"voice.summary: failed for {call.id}: {e}")

    # 5. Escalate if urgency=high
    try:
        if call and call.urgency == "high" and not call.escalation_json:
            from services import voice_escalation
            voice_escalation.escalate(db, call, reason="High urgency on call end")
            call = voice_service.get_call(db, agent_id, call.id)
    except Exception as e:
        log.warning(f"voice.escalation: failed for {call.id}: {e}")

    # 6. Broadcast call.ended so any open dashboard updates immediately
    turns = voice_service.list_turns(db, agent_id, call.id) if call else []
    _broker.publish_sync(
        agent_id,
        {"type": "call.ended", "call": voice_service.serialize_call(call, turns)},
    )
    return {"ok": True}


# ============================================================================
#  Webhook handler — Vapi events arrive here
# ============================================================================

def _verify_vapi_signature(raw_body: bytes, signature: Optional[str]) -> bool:
    """HMAC-SHA256 verify against VAPI_WEBHOOK_SECRET. Returns True when
    no secret is configured (dev mode) so smoke tests work locally without
    setup; production must set VAPI_WEBHOOK_SECRET."""
    secret = os.getenv("VAPI_WEBHOOK_SECRET", "")
    if not secret:
        return True
    if not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    # Constant-time compare
    return hmac.compare_digest(expected, signature)


@router.post("/webhook")
async def vapi_webhook(request: Request, db: Session = Depends(get_db)):
    """Vapi sends `call-start` / `transcript` / `end-of-call-report` events.
    We verify the signature, resolve agent_id via voice_provider_assistants,
    persist via voice_service, and broadcast to dashboard subscribers.
    """
    raw = await request.body()
    signature = request.headers.get("x-vapi-signature") or request.headers.get("x-signature")
    if not _verify_vapi_signature(raw, signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        payload = json.loads(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed JSON")

    # Vapi payload shape: { type: "call-start"|"transcript"|"end-of-call-report"|...,
    #                       call: { id, assistantId, customer: {...}, ... },
    #                       transcript?: "...", message?: {...}, ... }
    event_type = payload.get("type") or payload.get("event")
    call_payload = payload.get("call") or {}
    assistant_id = call_payload.get("assistantId") or call_payload.get("assistant_id")

    if not assistant_id:
        log.warning("voice.webhook: no assistantId in payload", extra={"action": "voice.webhook_no_assistant"})
        return {"ok": True, "skipped": "no assistantId"}

    agent_id = voice_service.resolve_agent_id_from_provider(db, "vapi", assistant_id)
    if not agent_id:
        log.warning(
            f"voice.webhook: unknown assistantId {assistant_id}",
            extra={"action": "voice.webhook_unknown_assistant"},
        )
        return {"ok": True, "skipped": "unknown assistantId"}

    provider_call_id = call_payload.get("id")
    customer = call_payload.get("customer") or {}
    caller_number = customer.get("number") or call_payload.get("phoneNumber") or ""
    caller_name = customer.get("name") or ""
    direction = call_payload.get("type") or call_payload.get("direction") or "inbound"
    # Normalize: Vapi calls inbound calls "inboundPhoneCall", we want just "inbound"
    if "inbound" in direction.lower():
        direction = "inbound"
    elif "outbound" in direction.lower():
        direction = "outbound"

    # Branch by event type
    if event_type in ("call-start", "status-update") and call_payload.get("status") in (None, "ringing", "in-progress"):
        call = voice_service.start_call(
            db,
            agent_id,
            provider="vapi",
            provider_call_id=provider_call_id,
            direction=direction,
            caller_number=caller_number,
            caller_name=caller_name or None,
        )
        if call_payload.get("status") == "in-progress":
            voice_service.mark_call_active(db, agent_id, call.id)
            call = voice_service.get_call(db, agent_id, call.id)
        _broker.publish_sync(
            agent_id,
            {"type": "call.started", "call": voice_service.serialize_call(call, [])},
        )

    elif event_type == "transcript":
        # Look up the call by provider_call_id
        call = voice_service.get_call_by_provider_id(db, agent_id, "vapi", provider_call_id)
        if not call:
            return {"ok": True, "skipped": "call not found"}
        msg = payload.get("message") or payload.get("transcript") or {}
        if isinstance(msg, str):
            msg = {"text": msg, "role": "user"}
        role = msg.get("role") or "user"
        # Vapi uses "user" / "assistant" — we use "user" / "bot"
        if role in ("assistant", "bot"):
            role = "bot"
        else:
            role = "user"
        text = msg.get("text") or msg.get("content") or ""
        is_partial = bool(msg.get("partial") or payload.get("transcriptType") == "partial")
        provider_turn_id = msg.get("id") or payload.get("messageId")
        if text:
            turn = voice_service.upsert_turn(
                db,
                agent_id,
                call.id,
                role=role,
                text=text,
                confidence=msg.get("confidence"),
                partial=is_partial,
                provider_turn_id=provider_turn_id,
            )
            if turn:
                _broker.publish_sync(
                    agent_id,
                    {
                        "type": "transcript.partial" if is_partial else "transcript.final",
                        "callId": str(call.id),
                        "turn": voice_service.serialize_turn(turn),
                    },
                )

    elif event_type in ("end-of-call-report", "call-end", "hangup"):
        call = voice_service.get_call_by_provider_id(db, agent_id, "vapi", provider_call_id)
        if not call:
            return {"ok": True, "skipped": "call not found"}
        ended_status = "completed"
        if call_payload.get("endedReason") in ("customer-no-answer", "no-answer"):
            ended_status = "missed"
        elif call_payload.get("endedReason") == "twilio-failed":
            ended_status = "failed"
        # Pull duration from payload if Vapi provided one
        duration_sec = call_payload.get("duration") or None
        if isinstance(duration_sec, (int, float)):
            duration_sec = int(duration_sec)
        else:
            duration_sec = None
        call = voice_service.end_call(
            db, agent_id, call.id,
            status=ended_status,
            duration_sec=duration_sec,
            recording_url=call_payload.get("recordingUrl"),
            raw_event=payload,
        )
        # LLM-generated summary (Step 17)
        try:
            from services import voice_summary
            voice_summary.generate_and_store_summary(db, agent_id, call.id)
            call = voice_service.get_call(db, agent_id, call.id)
        except Exception as e:
            log.warning(f"voice.summary: failed for {call.id}: {e}")
        # Auto-escalate high-urgency calls (Step 18)
        try:
            if call and call.urgency == "high" and not call.escalation_json:
                from services import voice_escalation
                voice_escalation.escalate(db, call, reason="High urgency on call end")
                call = voice_service.get_call(db, agent_id, call.id)
        except Exception as e:
            log.warning(f"voice.escalation: failed for {call.id}: {e}")
        turns = voice_service.list_turns(db, agent_id, call.id) if call else []
        _broker.publish_sync(
            agent_id,
            {"type": "call.ended", "call": voice_service.serialize_call(call, turns)},
        )

    else:
        log.info(
            f"voice.webhook: unhandled event type {event_type}",
            extra={"action": "voice.webhook_unhandled"},
        )

    return {"ok": True}


# ============================================================================
#  WebSocket — live updates pushed to admin dashboards
# ============================================================================

# Mounted at the top-level (not under the /api/voice router prefix) — see main.py.
ws_router = APIRouter()


@ws_router.websocket("/ws/voice/{agent_id}/calls")
async def voice_calls_ws(websocket: WebSocket, agent_id: str):
    """Subscribe to live call + campaign updates for this agent_id.

    Auth: optional shared-secret check via `?token=` query param matched
    against `VOICE_WS_TOKEN` env var. When the env var is unset, the
    socket accepts any connection (dev mode). Production: set the env var
    and have the dashboard append it to the WS URL (`urlOverride`).

    Browser WebSocket handshakes can't send custom Authorization headers,
    which is why we go via query param. The next iteration upgrades this
    to a short-lived JWT issued by `auth_service.issue_ws_token(user_id,
    agent_id)` — until that lands, the shared secret is the fence.
    """
    required_token = os.getenv("VOICE_WS_TOKEN", "")
    if required_token:
        supplied = websocket.query_params.get("token") or ""
        if not hmac.compare_digest(required_token, supplied):
            await websocket.close(code=4401, reason="unauthorized")
            log.warning(
                f"voice.ws: rejected unauth attempt for {agent_id}",
                extra={"action": "voice.ws_unauthorized"},
            )
            return

    await websocket.accept()
    await _broker.subscribe(agent_id, websocket)
    log.info(
        f"voice.ws: client subscribed to {agent_id}",
        extra={"action": "voice.ws_subscribe", "agent_id": agent_id},
    )
    try:
        # Keep the socket open. We don't expect client→server messages
        # in v1 — just wait for disconnect.
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await _broker.unsubscribe(agent_id, websocket)
        log.info(
            f"voice.ws: client unsubscribed from {agent_id}",
            extra={"action": "voice.ws_unsubscribe", "agent_id": agent_id},
        )
