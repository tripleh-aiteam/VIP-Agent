"""
voice_escalation — route urgent calls to the agent's notification channel.

Reads AgentConfig.voice.escalationChannel (a discriminated union):
  { kind: "telegram", chatId, botEnvKey? }
  { kind: "slack",    channel,  botEnvKey? }
  { kind: "email",    to }
  { kind: "webhook",  url, method? }
  { kind: "none" }

Each agent's config lives in its frontend (e.g.
`apps/admin-dashboard/src/chatbot.config.ts`), but the backend needs
the channel info too. We keep a small registry mapping agent_id →
escalation config — populated from env in production. For VIP today,
we read VIP_VOICE_ESCALATION_* env vars.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Optional

import httpx
from sqlalchemy.orm import Session

from db.models import VoiceCall
from services import voice_service
from services.logger import log


# ============================================================================
#  Agent → escalation config lookup
# ============================================================================

def _vip_default_escalation() -> dict[str, Any]:
    """Default VIP wiring — reads from env so secrets stay out of code.
    Add per-agent overrides here as more consumers come online."""
    return {
        "kind": "telegram",
        "chatId": os.getenv("VIP_VOICE_ESCALATION_CHAT_ID", "")
                  or os.getenv("TELEGRAM_BOSS_CHAT_ID", ""),
        "botEnvKey": "TELEGRAM_BOT_TOKEN",
    }


_AGENT_ESCALATION_REGISTRY: dict[str, dict[str, Any]] = {
    "vip": _vip_default_escalation(),
    # Real Estate goes here once its config lands:
    # "realty": {"kind": "slack", "channel": "#realestate", "botEnvKey": "SLACK_BOT_TOKEN"},
}


def get_escalation_channel(agent_id: str) -> dict[str, Any]:
    """Returns the agent's escalation config, defaulting to {"kind":"none"}
    if no mapping exists (no escalation will fire)."""
    return _AGENT_ESCALATION_REGISTRY.get(agent_id, {"kind": "none"})


# ============================================================================
#  Channel dispatchers
# ============================================================================

def _format_message(call: VoiceCall, reason: str) -> str:
    """Plain-text message body sent over any channel."""
    caller = call.caller_name or "Unknown"
    summary = call.summary or "(no summary yet)"
    started = call.started_at.strftime("%H:%M") if call.started_at else "?"
    return (
        f"🚨 URGENT CALL — {call.agent_id.upper()}\n"
        f"Caller: {caller} ({call.caller_number})\n"
        f"Started: {started}\n"
        f"Reason: {reason}\n"
        f"\n"
        f"Summary: {summary}"
    )


def _dispatch_telegram(call: VoiceCall, message: str, channel: dict[str, Any]) -> str:
    """Send via existing telegram_service. Returns a descriptor string."""
    from services.telegram_service import send_message
    chat_id = channel.get("chatId") or ""
    if not chat_id:
        log.warning(
            "voice.escalation: telegram channel has no chatId",
            extra={"action": "voice.escalation_no_chat_id"},
        )
        return "telegram: no chatId configured"
    ok = send_message(chat_id, message, parse_mode=None)
    return f"Telegram chat {chat_id}{' (sent)' if ok else ' (failed)'}"


def _dispatch_slack(call: VoiceCall, message: str, channel: dict[str, Any]) -> str:
    token = os.getenv(channel.get("botEnvKey") or "SLACK_BOT_TOKEN", "")
    if not token:
        return "slack: no bot token in env"
    target = channel.get("channel") or ""
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {token}"},
                json={"channel": target, "text": message},
            )
            if resp.status_code == 200 and resp.json().get("ok"):
                return f"Slack {target} (sent)"
            return f"Slack {target} (failed: {resp.text[:100]})"
    except Exception as e:
        return f"Slack error: {e}"


def _dispatch_email(call: VoiceCall, message: str, channel: dict[str, Any]) -> str:
    # Minimal stub — real implementation would call SES / SMTP / etc.
    to = channel.get("to") or ""
    log.info(
        f"voice.escalation: would email {to} — {message[:80]}",
        extra={"action": "voice.escalation_email_stub"},
    )
    return f"email {to} (stubbed)"


def _dispatch_webhook(call: VoiceCall, message: str, channel: dict[str, Any]) -> str:
    url = channel.get("url") or ""
    method = (channel.get("method") or "POST").upper()
    if not url:
        return "webhook: no url"
    try:
        payload = {
            "agentId": call.agent_id,
            "callId": str(call.id),
            "caller": call.caller_name,
            "callerNumber": call.caller_number,
            "summary": call.summary,
            "message": message,
        }
        with httpx.Client(timeout=10) as client:
            resp = client.request(method, url, json=payload)
            return f"Webhook {url} → {resp.status_code}"
    except Exception as e:
        return f"Webhook error: {e}"


# ============================================================================
#  Public entry point
# ============================================================================

def escalate(db: Session, call: VoiceCall, reason: str) -> str:
    """Dispatch the escalation via the agent's configured channel + persist
    the escalation_json on the call row. Returns a descriptor string for
    the caller (used in the REST response)."""
    channel = get_escalation_channel(call.agent_id)
    kind = channel.get("kind", "none")
    message = _format_message(call, reason)

    target_descriptor = ""
    if kind == "telegram":
        target_descriptor = _dispatch_telegram(call, message, channel)
    elif kind == "slack":
        target_descriptor = _dispatch_slack(call, message, channel)
    elif kind == "email":
        target_descriptor = _dispatch_email(call, message, channel)
    elif kind == "webhook":
        target_descriptor = _dispatch_webhook(call, message, channel)
    else:
        target_descriptor = "none (no escalation configured)"
        log.info(
            f"voice.escalation: no channel for agent {call.agent_id}",
            extra={"action": "voice.escalation_no_channel"},
        )

    # Persist on the call row
    voice_service.patch_call(
        db,
        call.agent_id,
        call.id,
        status="escalated",
        escalation_json={
            "to": target_descriptor,
            "reason": reason,
            "at": int(datetime.utcnow().timestamp() * 1000),
        },
    )
    log.info(
        f"voice.escalation: escalated {call.id} → {target_descriptor}",
        extra={"action": "voice.escalation_sent", "agent_id": call.agent_id},
    )
    return target_descriptor
