"""
elevenlabs_client — typed wrapper around ElevenLabs Conversational AI's REST API.

Parallel to vapi_client.py — same shape, different provider. The router +
campaign runner dispatch by `voice_provider_assistants.provider` so VIP
(ElevenLabs) and a future agent on Vapi can coexist.

Endpoints we use:
  POST /v1/convai/twilio/outbound_call   place an outbound call
  GET  /v1/convai/conversations/{id}     fetch conversation state for reconciliation

Auth: `xi-api-key` header against `ELEVENLABS_API_KEY` env var.
Base URL: `ELEVENLABS_API_BASE` env var, defaults to `https://api.elevenlabs.io`.

The post-call `post_call_transcription` webhook arrives at our
`/api/voice/webhook/elevenlabs` route in routers/voice.py.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx

from services.logger import log


def _api_base() -> str:
    return os.getenv("ELEVENLABS_API_BASE", "https://api.elevenlabs.io").rstrip("/")


def _auth_headers() -> dict[str, str]:
    key = os.getenv("ELEVENLABS_API_KEY", "")
    if not key:
        raise ElevenLabsClientError("ELEVENLABS_API_KEY env var not set")
    return {
        "xi-api-key": key,
        "Content-Type": "application/json",
    }


class ElevenLabsClientError(RuntimeError):
    """Raised when ElevenLabs returns a non-2xx response or is misconfigured."""


# ============================================================================
#  Outbound call (via ElevenLabs-managed Twilio)
# ============================================================================

def place_outbound_call(
    *,
    agent_id: str,
    agent_phone_number_id: str,
    to_number: str,
    customer_name: Optional[str] = None,
    dynamic_variables: Optional[dict[str, Any]] = None,
    call_recording_enabled: bool = True,
    ringing_timeout_secs: int = 60,
) -> dict[str, Any]:
    """Place an outbound phone call via the agent's configured Twilio number.

    Returns ElevenLabs' response which includes `conversation_id` (stored as
    `voice_calls.provider_call_id`) and `callSid` (Twilio's call ID).

    Args:
      agent_id: ElevenLabs Conversational AI agent ID — NOT the same as
        our internal AgentConfig.agentId. Comes from the ElevenLabs console.
      agent_phone_number_id: the phone-number ID registered with the agent
        in the ElevenLabs console (an opaque string, NOT the E.164 number).
      to_number: E.164 destination, e.g. "+82-10-1234-5678"
      customer_name: forwarded to the assistant via `dynamic_variables.customer_name`
      dynamic_variables: arbitrary key-value forwarded to the assistant —
        usable as `{customer_name}`, `{amount}`, `{lease_id}` etc. in the
        agent's first-message and prompt templates
      call_recording_enabled: whether Twilio should record the call
      ringing_timeout_secs: how long to ring before giving up (max 60 typically)
    """
    merged_vars: dict[str, Any] = dict(dynamic_variables or {})
    if customer_name and "customer_name" not in merged_vars:
        merged_vars["customer_name"] = customer_name

    body: dict[str, Any] = {
        "agent_id": agent_id,
        "agent_phone_number_id": agent_phone_number_id,
        "to_number": to_number,
        "call_recording_enabled": call_recording_enabled,
        "telephony_call_config": {"ringing_timeout_secs": ringing_timeout_secs},
    }
    if merged_vars:
        body["conversation_initiation_client_data"] = {
            "dynamic_variables": merged_vars,
        }

    try:
        with httpx.Client(timeout=20) as client:
            resp = client.post(
                f"{_api_base()}/v1/convai/twilio/outbound_call",
                headers=_auth_headers(),
                json=body,
            )
    except httpx.HTTPError as e:
        raise ElevenLabsClientError(f"network error calling ElevenLabs: {e}") from e

    if resp.status_code not in (200, 201):
        raise ElevenLabsClientError(
            f"ElevenLabs outbound_call returned {resp.status_code}: {resp.text[:300]}"
        )
    data = resp.json()
    if not data.get("success"):
        raise ElevenLabsClientError(
            f"ElevenLabs outbound_call refused: {data.get('message', 'no reason given')}"
        )
    log.info(
        f"elevenlabs_client: placed outbound conversation {data.get('conversation_id')} → {to_number}",
        extra={"action": "elevenlabs.outbound_placed"},
    )
    return data


# ============================================================================
#  Reconciliation — fetch a conversation's current state
# ============================================================================

def get_conversation(conversation_id: str) -> dict[str, Any]:
    """Fetch a conversation's current state. Useful if a webhook was lost
    and we need to reconcile on demand."""
    with httpx.Client(timeout=10) as client:
        resp = client.get(
            f"{_api_base()}/v1/convai/conversations/{conversation_id}",
            headers=_auth_headers(),
        )
    if resp.status_code == 404:
        raise ElevenLabsClientError(f"conversation {conversation_id} not found in ElevenLabs")
    if resp.status_code != 200:
        raise ElevenLabsClientError(
            f"ElevenLabs GET conversation returned {resp.status_code}: {resp.text[:300]}"
        )
    return resp.json()


# ============================================================================
#  End / transfer — placeholder until ElevenLabs exposes server-side controls
# ============================================================================

def end_call(conversation_id: str) -> None:
    """Hang up an active call. ElevenLabs Conversational AI doesn't expose
    a server-side end-conversation REST call today — the agent ends the
    call by emitting the `end_call` tool, or the caller hangs up.

    For our "Take over" UX, we'd configure the ElevenLabs agent to listen
    for a `transfer_call` server-tool invocation. Tracked for v1.3.
    """
    raise NotImplementedError(
        "ElevenLabs server-side end-call not yet implemented — "
        "see elevenlabs_client.py for the implementation plan"
    )


def transfer_call(conversation_id: str, destination_number: str) -> None:
    """Same story as `end_call` — ElevenLabs routes transfer through the
    agent's tool system, not a direct REST call. The dashboard's Take-over
    button stays greyed until the agent's tool config + server-tool
    invocation lands."""
    raise NotImplementedError(
        "ElevenLabs server-side transfer not yet implemented — "
        "configure the agent with a transfer_call tool in the console"
    )
