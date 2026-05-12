"""
selfhosted_voice_client — outbound call origination via Asterisk ARI.

Inbound calls hit voice_pipeline.py via AudioSocket directly — no client
involvement. Outbound calls need someone to TELL Asterisk to dial a
number, and that's this module's job.

Asterisk Rest Interface (ARI):
  POST /ari/channels  with {endpoint, extension, context, callerId, ...}
  → Asterisk originates the call, dials KT, bridges audio to AudioSocket

Auth: HTTP basic against ARI_USERNAME / ARI_PASSWORD.
Base URL: ARI_BASE_URL, defaults to http://localhost:8088/ari.

The dialplan context that handles the originated channel is
`outbound-to-kt` in infra/asterisk/extensions.conf.template.
"""

from __future__ import annotations

import os
import uuid as _uuid
from typing import Any, Optional

import httpx

from services.logger import log


def _ari_base() -> str:
    return os.getenv("ARI_BASE_URL", "http://localhost:8088/ari").rstrip("/")


def _ari_auth() -> tuple[str, str]:
    return (
        os.getenv("ARI_USERNAME", "asterisk"),
        os.getenv("ARI_PASSWORD", ""),
    )


class SelfHostedClientError(RuntimeError):
    """Raised when Asterisk ARI returns an error or is misconfigured."""


def originate_outbound_call(
    *,
    agent_id: str,
    to_number: str,
    customer_name: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """Tell Asterisk to dial `to_number` via the KT trunk. Returns the
    Asterisk channel ID (used as `voice_calls.provider_call_id`).

    Asterisk's ARI POST /channels takes:
      endpoint:  the destination (we use Local/outbound-call@outbound-to-kt)
      extension: passed to the dialplan as ${EXTEN} — we put the dialed number here
      context:   dialplan context to enter (outbound-to-kt)
      callerId:  caller-ID for the outbound leg
      variables: kv map exposed to the dialplan as channel variables —
                 we forward agent_id, call_id, reason etc. so the pipeline
                 can correlate events back to our voice_calls row
    """
    password = _ari_auth()[1]
    if not password:
        raise SelfHostedClientError(
            "ARI_PASSWORD env var not set — Asterisk ARI auth missing. "
            "Add an ARI user to infra/asterisk and set ARI_USERNAME + ARI_PASSWORD."
        )

    channel_id = str(_uuid.uuid4())
    variables: dict[str, str] = {
        "AGENT_ID": agent_id,
        "OUTBOUND_TARGET": to_number,
    }
    if customer_name:
        variables["CALLER_NAME"] = customer_name
    if metadata:
        for k, v in metadata.items():
            # Channel variables must be strings; flatten nested dicts to JSON
            if isinstance(v, (str, int, float, bool)):
                variables[f"META_{k.upper()}"] = str(v)

    try:
        with httpx.Client(timeout=15, auth=_ari_auth()) as client:
            resp = client.post(
                f"{_ari_base()}/channels",
                params={
                    "endpoint": "Local/outbound-call@outbound-to-kt",
                    "extension": to_number,
                    "context": "outbound-to-kt",
                    "priority": "1",
                    "channelId": channel_id,
                    "callerId": f'"{customer_name or "AI"}" <{to_number}>',
                    "timeout": "60",
                },
                json={"variables": variables},
            )
    except httpx.HTTPError as e:
        raise SelfHostedClientError(f"network error calling Asterisk ARI: {e}") from e

    if resp.status_code not in (200, 201):
        raise SelfHostedClientError(
            f"Asterisk ARI POST /channels returned {resp.status_code}: {resp.text[:300]}"
        )
    log.info(
        f"selfhosted_voice_client: originated outbound channel {channel_id} → {to_number}",
        extra={"action": "selfhosted.outbound_originated"},
    )
    return channel_id


def hangup_channel(channel_id: str) -> None:
    """Hang up an Asterisk channel by ID. Used for end-call from the dashboard."""
    try:
        with httpx.Client(timeout=10, auth=_ari_auth()) as client:
            resp = client.delete(f"{_ari_base()}/channels/{channel_id}")
        if resp.status_code not in (200, 204, 404):
            raise SelfHostedClientError(
                f"ARI DELETE /channels/{channel_id} returned {resp.status_code}: {resp.text[:200]}"
            )
    except httpx.HTTPError as e:
        raise SelfHostedClientError(f"network error: {e}") from e
