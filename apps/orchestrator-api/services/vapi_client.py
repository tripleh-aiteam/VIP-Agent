"""
vapi_client — typed wrapper around Vapi's REST API.

Wraps the small slice of Vapi's API we actually use:

  POST /call         place an outbound call against an assistant
  GET  /call/{id}    fetch a call's current state (status + recordingUrl + summary)
  PATCH /call/{id}   end an active call (status → ended)

Mid-call transfer ("take over → human") is intentionally NOT a direct
REST call here: Vapi's model is that the assistant decides to transfer
using its `forwardingPhoneNumber` config or a `transfer-call` tool. The
take-over UX in the dashboard works by invoking that tool via a Vapi
function-call control message — landed once we hit the operator
ergonomics phase. For now, the dashboard button surfaces as disabled
when `transfer_call()` raises NotImplementedError.

Auth: Bearer token via `VAPI_API_KEY` env var.
Base URL: `VAPI_API_BASE` env var, defaults to `https://api.vapi.ai`.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx

from services.logger import log


def _api_base() -> str:
    return os.getenv("VAPI_API_BASE", "https://api.vapi.ai").rstrip("/")


def _auth_headers() -> dict[str, str]:
    key = os.getenv("VAPI_API_KEY", "")
    if not key:
        raise VapiClientError("VAPI_API_KEY env var not set")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


class VapiClientError(RuntimeError):
    """Raised when Vapi returns a non-2xx response or is misconfigured."""


# ============================================================================
#  Outbound call
# ============================================================================

def place_outbound_call(
    *,
    assistant_id: str,
    phone_number_id: str,
    to_number: str,
    customer_name: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Place an outbound phone call.

    Returns Vapi's `Call` object — most importantly `id` (which we store
    as `voice_calls.provider_call_id`). The actual call rings the recipient
    asynchronously; the webhook handler at `/api/voice/webhook` fires
    `call-start` / `status-update` events as the call progresses.

    Args:
      assistant_id: AgentConfig.voice.assistantId — the Vapi assistant UUID
      phone_number_id: Vapi's ID for the originating number (NOT the E.164
        string). Look this up in the Vapi console once per agent.
      to_number: E.164 destination, e.g. "+82-10-1234-5678"
      customer_name: optional display name (Vapi uses it for caller-ID)
      metadata: arbitrary key-value bag forwarded to the assistant —
        we pass `agent_id`, `campaign_id`, `recipient_id` here so the
        webhook can reconcile against our rows
    """
    body: dict[str, Any] = {
        "assistantId": assistant_id,
        "phoneNumberId": phone_number_id,
        "customer": {
            "number": to_number,
        },
    }
    if customer_name:
        body["customer"]["name"] = customer_name
    if metadata:
        body["metadata"] = metadata

    try:
        with httpx.Client(timeout=20) as client:
            resp = client.post(
                f"{_api_base()}/call",
                headers=_auth_headers(),
                json=body,
            )
    except httpx.HTTPError as e:
        raise VapiClientError(f"network error calling Vapi: {e}") from e

    if resp.status_code not in (200, 201):
        raise VapiClientError(
            f"Vapi /call returned {resp.status_code}: {resp.text[:300]}"
        )
    data = resp.json()
    log.info(
        f"vapi_client: placed outbound call {data.get('id')} → {to_number}",
        extra={"action": "vapi.outbound_placed"},
    )
    return data


# ============================================================================
#  Fetch call state — used for reconciliation
# ============================================================================

def get_call(provider_call_id: str) -> dict[str, Any]:
    """Fetch a single call's current state. Useful if a webhook event was
    dropped and we need to reconcile on the next request.
    """
    with httpx.Client(timeout=10) as client:
        resp = client.get(
            f"{_api_base()}/call/{provider_call_id}",
            headers=_auth_headers(),
        )
    if resp.status_code == 404:
        raise VapiClientError(f"call {provider_call_id} not found in Vapi")
    if resp.status_code != 200:
        raise VapiClientError(
            f"Vapi GET /call/{provider_call_id} returned {resp.status_code}: {resp.text[:300]}"
        )
    return resp.json()


# ============================================================================
#  End an active call programmatically
# ============================================================================

def end_call(provider_call_id: str) -> dict[str, Any]:
    """Hang up an active call. Returns Vapi's updated Call object.

    Vapi's pattern for server-driven end is `PATCH /call/{id}` with
    `status: "ended"` — this sets the assistant to end the call gracefully
    on its next turn. If the call is already ended, returns the current state.
    """
    with httpx.Client(timeout=10) as client:
        resp = client.patch(
            f"{_api_base()}/call/{provider_call_id}",
            headers=_auth_headers(),
            json={"status": "ended"},
        )
    if resp.status_code not in (200, 201, 204):
        raise VapiClientError(
            f"Vapi PATCH /call/{provider_call_id} returned {resp.status_code}: {resp.text[:300]}"
        )
    return resp.json() if resp.text else {"status": "ended"}


# ============================================================================
#  Transfer / take over — placeholder until assistant config UX lands
# ============================================================================

def transfer_call(provider_call_id: str, destination_number: str) -> None:
    """Mid-call transfer to a human number.

    Not implemented as a direct REST call: Vapi expects this to flow
    through the assistant's `transfer-call` tool (configured per assistant)
    rather than a server-issued command. Wiring it up requires:

      1. Add a `transfer-call` tool entry to the Vapi assistant config
         pointing at a destination dial-plan (the operator's number)
      2. Add a server-issued function-call control message that invokes
         that tool on the active call ID

    Tracked as a TODO for the "operator ergonomics" follow-up phase.
    The dashboard's Take-over button stays greyed until this lands.
    """
    raise NotImplementedError(
        "Vapi transfer requires assistant tool config + control message — "
        "see vapi_client.py docstring for the implementation plan"
    )
