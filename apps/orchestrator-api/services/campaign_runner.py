"""
campaign_runner — background dialer for batch outbound campaigns.

Runs every 30s via the scheduler. Each tick:
  1. Pulls every campaign in status=running across ALL agents.
  2. For each, checks working-hours window + per-campaign pacing.
  3. If allowed, picks the next queued recipient and "dials" it
     (placeholder until services/vapi_client.py lands).
  4. Marks the campaign completed when all recipients are done.

Multi-tenant aware — every action goes through voice_service with the
campaign's own agent_id. Two agents' campaigns coexist on the same tick.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from db.base import SessionLocal
from db.models import BatchRecipient
from routers.voice import get_broker
from services import voice_service
from services.logger import log


# KST is UTC+9 — agents typically declare workingHours in KST. If we
# expand to multi-timezone agents later, this constant is replaced with
# per-campaign tz resolution from AgentConfig.voice.workingHours.timezone.
_KST_OFFSET_HOURS = 9


def _current_kst_hour() -> int:
    return (datetime.now(timezone.utc).hour + _KST_OFFSET_HOURS) % 24


def _campaign_can_dial_now(campaign) -> tuple[bool, Optional[str]]:
    """Returns (allowed, reason_if_blocked). Two checks:
    1. Working hours window from campaign.working_hours_json
    2. Pacing — at most `campaign.pacing` calls per hour
    """
    hours = campaign.working_hours_json or {"start": 9, "end": 21}
    now_hour = _current_kst_hour()
    if not voice_service.is_within_working_hours(now_hour, hours):
        return False, f"outside working hours (current KST hour={now_hour}, window={hours['start']}-{hours['end']})"

    # Pacing: count calls placed by this campaign in the last 60 minutes
    # via voice_calls.campaign_id. If >= pacing, defer.
    db = SessionLocal()
    try:
        from db.models import VoiceCall
        since = datetime.utcnow() - timedelta(hours=1)
        count = (
            db.query(VoiceCall)
            .filter(
                VoiceCall.campaign_id == campaign.id,
                VoiceCall.started_at >= since,
            )
            .count()
        )
        if count >= campaign.pacing:
            return False, f"pacing limit reached ({count}/{campaign.pacing} this hour)"
    finally:
        db.close()
    return True, None


def _dial_next_recipient(campaign) -> Optional[BatchRecipient]:
    """Dial the next queued recipient. Returns the recipient row on
    success, or None when the queue is empty (caller marks the campaign
    completed in that case).

    Placeholder for the actual Vapi outbound API call — that lands when
    services/vapi_client.py is built (Step 22 ish). For now this records
    the intent in voice_calls + voice_service.mark_recipient_calling().
    """
    db = SessionLocal()
    try:
        recipient = voice_service.next_queued_recipient(db, campaign.id)
        if not recipient:
            return None

        # Pre-flight per-recipient cap (the campaign-create check is
        # snapshot-in-time; re-check now in case other channels dialed
        # this person in between).
        block_reason = voice_service.check_recipient_eligibility(
            db, campaign.agent_id, recipient.number
        )
        if block_reason:
            voice_service.mark_recipient_outcome(
                db,
                recipient.id,
                outcome="technical_failure",
                notes=f"Skipped — {block_reason}",
                status="skipped",
            )
            return recipient

        # Create the voice_calls row in status=ringing BEFORE the Vapi call —
        # so a webhook event racing back finds the row.
        call = voice_service.start_call(
            db,
            campaign.agent_id,
            provider="vapi",
            provider_call_id=None,
            direction="outbound",
            caller_number=recipient.number,
            caller_name=recipient.name or None,
            campaign_id=campaign.id,
            recipient_id=recipient.id,
        )
        voice_service.mark_recipient_calling(db, recipient.id, call.id)

        # Place the actual Vapi outbound call. Resolve the assistant +
        # phone_number_id mapping from voice_provider_assistants;
        # skip the real dial (mark as "intent recorded") if not configured —
        # useful for testing campaign mechanics without burning Vapi credit.
        import os as _os
        from db.models import VoiceProviderAssistant
        mapping = (
            db.query(VoiceProviderAssistant)
            .filter(
                VoiceProviderAssistant.agent_id == campaign.agent_id,
                VoiceProviderAssistant.provider == "vapi",
                VoiceProviderAssistant.active.is_(True),
            )
            .first()
        )
        if mapping:
            try:
                # Use the same dispatcher as routers/voice.py:place_outbound so
                # the per-provider env-var convention stays single-sourced.
                from routers.voice import _dispatch_outbound
                provider_call_id = _dispatch_outbound(
                    provider=mapping.provider,
                    provider_assistant_id=mapping.provider_assistant_id,
                    agent_id=campaign.agent_id,
                    to_number=recipient.number,
                    customer_name=recipient.name or None,
                    metadata={
                        "agent_id": campaign.agent_id,
                        "call_id": str(call.id),
                        "campaign_id": str(campaign.id),
                        "recipient_id": str(recipient.id),
                        "reason": campaign.reason,
                        "context": recipient.context_json or {},
                    },
                )
                voice_service.patch_call(
                    db, campaign.agent_id, call.id,
                    provider_call_id=provider_call_id,
                )
            except Exception as e:
                log.warning(
                    f"campaign_runner: provider outbound failed for {recipient.number}: {e}",
                    extra={"action": "campaign_runner.provider_failed"},
                )
                voice_service.patch_call(db, campaign.agent_id, call.id, status="failed")
                voice_service.mark_recipient_outcome(
                    db,
                    recipient.id,
                    outcome="technical_failure",
                    notes=f"Outbound failed: {str(e)[:200]}",
                    status="failed",
                )

        # Broadcast to dashboard so the campaign UI updates immediately
        broker = get_broker()
        recipients = voice_service.list_recipients(db, campaign.id)
        broker.publish_sync(
            campaign.agent_id,
            {
                "type": "campaign.progress",
                "campaign": voice_service.serialize_campaign(campaign, recipients),
            },
        )
        broker.publish_sync(
            campaign.agent_id,
            {"type": "call.started", "call": voice_service.serialize_call(call, [])},
        )

        log.info(
            f"campaign_runner: dialed {recipient.number} for campaign {campaign.id}",
            extra={
                "action": "campaign_runner.dial",
                "agent_id": campaign.agent_id,
                "campaign_id": str(campaign.id),
                "recipient_id": str(recipient.id),
            },
        )
        return recipient
    finally:
        db.close()


def _maybe_complete_campaign(campaign) -> None:
    """Mark the campaign completed if no queued or calling recipients remain."""
    db = SessionLocal()
    try:
        from db.models import BatchRecipient as _BR
        remaining = (
            db.query(_BR)
            .filter(
                _BR.campaign_id == campaign.id,
                _BR.status.in_(["queued", "calling"]),
            )
            .count()
        )
        if remaining == 0:
            voice_service.set_campaign_status(
                db, campaign.agent_id, campaign.id, "completed"
            )
            recipients = voice_service.list_recipients(db, campaign.id)
            updated = voice_service.get_campaign(db, campaign.agent_id, campaign.id)
            get_broker().publish_sync(
                campaign.agent_id,
                {
                    "type": "campaign.progress",
                    "campaign": voice_service.serialize_campaign(updated, recipients),
                },
            )
            log.info(
                f"campaign_runner: completed campaign {campaign.id}",
                extra={"action": "campaign_runner.completed"},
            )
    finally:
        db.close()


def tick() -> None:
    """One scheduler tick — process every running campaign across all agents."""
    db = SessionLocal()
    try:
        running = voice_service.list_running_campaigns_all_agents(db)
    finally:
        db.close()

    for campaign in running:
        try:
            allowed, reason = _campaign_can_dial_now(campaign)
            if not allowed:
                log.info(
                    f"campaign_runner: deferring {campaign.id} — {reason}",
                    extra={"action": "campaign_runner.defer"},
                )
                continue
            dialed = _dial_next_recipient(campaign)
            if dialed is None:
                _maybe_complete_campaign(campaign)
        except Exception as e:
            log.warning(
                f"campaign_runner: tick failed for {campaign.id}: {e}",
                extra={"action": "campaign_runner.tick_error"},
            )
