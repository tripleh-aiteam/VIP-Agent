"""
kakao_client — REST wrapper for the KakaoTalk Channel Message API.

Send text / voice / image / file from your bot to a customer's KakaoTalk.
Same shape as services/vapi_client.py and services/elevenlabs_client.py
(provider clients in the voice domain) — drop-in pattern.

Required env vars (typically set per-agent via the channel mapping):
  KAKAO_REST_API_KEY                 — your Kakao app's REST API key
  KAKAO_ADMIN_KEY (optional)          — for AlimTalk template sends
  KAKAO_CHANNEL_ID_<AGENT_UPPER>      — Channel ID for this agent (e.g.
                                        KAKAO_CHANNEL_ID_VIP)
  KAKAO_ACCESS_TOKEN_<AGENT_UPPER>    — OAuth token for the channel
                                        (returned when admin authorizes
                                        the channel under the Kakao app)

For the actual Kakao Channel Message API, the endpoints and auth flow
require admin authorization via the Kakao Developer Console. This client
wraps the common patterns; specifics map to Kakao's "channel message"
service (the 1:1 chat-bot product).

Auth header: Authorization: Bearer <ACCESS_TOKEN>
Base URL:    https://kapi.kakao.com
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx

from services.logger import log


def _api_base() -> str:
    return os.getenv("KAKAO_API_BASE", "https://kapi.kakao.com").rstrip("/")


def _access_token(agent_id: str) -> str:
    """Per-agent access token. The channel mapping table tells us which
    env var holds the token for this specific agent."""
    var_name = f"KAKAO_ACCESS_TOKEN_{agent_id.upper()}"
    token = os.getenv(var_name, "")
    if not token:
        raise KakaoClientError(
            f"{var_name} env var not set — register the agent's Kakao access token first"
        )
    return token


def _auth_headers(agent_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_access_token(agent_id)}",
        "Content-Type": "application/x-www-form-urlencoded",
    }


class KakaoClientError(RuntimeError):
    """Raised when Kakao returns a non-2xx response or is misconfigured."""


# ============================================================================
#  Send text — primary outbound path
# ============================================================================

def send_text(
    *,
    agent_id: str,
    conversation_id: str,
    text: str,
    receiver_uuid: Optional[str] = None,
) -> dict[str, Any]:
    """Send a plain text message to the channel's chat.

    For the conversational Channel API, KakaoTalk uses a "default" template
    type which accepts free-text bodies. The receiver is identified by
    `receiver_uuid` (the Kakao user UUID we got from a previous inbound
    message in this conversation).

    Note: for production AlimTalk (template-based notifications), use
    `send_alimtalk_template()` instead — those require pre-approved
    templates.
    """
    if not text:
        return {"skipped": "empty text"}

    payload: dict[str, Any] = {
        "template_object": _build_text_template(text),
    }
    if receiver_uuid:
        payload["receiver_uuids"] = f'["{receiver_uuid}"]'

    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                f"{_api_base()}/v1/api/talk/friends/message/default/send",
                headers=_auth_headers(agent_id),
                data=payload,
            )
    except httpx.HTTPError as e:
        raise KakaoClientError(f"network error sending Kakao text: {e}") from e

    if resp.status_code not in (200, 201):
        raise KakaoClientError(
            f"Kakao send_text returned {resp.status_code}: {resp.text[:300]}"
        )
    data = resp.json()
    log.info(
        f"kakao_client: sent text to conv {conversation_id} ({len(text)} chars)",
        extra={"action": "kakao.text_sent"},
    )
    return data


def _build_text_template(text: str) -> str:
    """KakaoTalk message templates are JSON — we wrap text in the default
    template type. Bot's message has no link; for clickable CTA buttons
    in future, extend this to a 'buttons' template."""
    import json as _json
    template = {
        "object_type": "text",
        "text": text,
        "link": {
            # Kakao requires a link object even for text-only messages
            "web_url": "https://triple-h.example.com",
            "mobile_web_url": "https://triple-h.example.com",
        },
        "button_title": None,
    }
    return _json.dumps(template, ensure_ascii=False)


# ============================================================================
#  Send AlimTalk (template-based notifications) — outbound proactive
# ============================================================================

def send_alimtalk_template(
    *,
    agent_id: str,
    receiver_phone: str,
    template_code: str,
    template_args: dict[str, str],
) -> dict[str, Any]:
    """Send a pre-approved AlimTalk template message.

    AlimTalk requires:
      - Template registered + approved with Kakao (3-5 day approval)
      - Template variables filled at send time
      - Sender's Kakao Channel verified

    Use cases: rent reminder, viewing confirmation, document follow-up
    — proactive notifications, NOT replies to inbound messages.

    Cost: ~₩9-15 per message (vs free for inline channel replies).
    """
    # AlimTalk uses a separate API tier — typically goes through the
    # 비즈톡 (BizMessage) service rather than the chat-bot API.
    # Placeholder until the user's channel + AlimTalk templates are
    # approved by Kakao.
    raise NotImplementedError(
        "AlimTalk template send not yet wired. Submit templates via "
        "https://business.kakao.com → 알림톡 → 템플릿 등록, then enable here."
    )


# ============================================================================
#  Send image / file (multipart upload)
# ============================================================================

def send_image(
    *,
    agent_id: str,
    conversation_id: str,
    image_url: str,
    caption: Optional[str] = None,
    receiver_uuid: Optional[str] = None,
) -> dict[str, Any]:
    """Send an image to the channel. The image must be hosted publicly
    (typically uploaded to your CDN/Storage first, then linked here)."""
    payload: dict[str, Any] = {
        "template_object": _build_image_template(image_url, caption),
    }
    if receiver_uuid:
        payload["receiver_uuids"] = f'["{receiver_uuid}"]'

    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                f"{_api_base()}/v1/api/talk/friends/message/default/send",
                headers=_auth_headers(agent_id),
                data=payload,
            )
    except httpx.HTTPError as e:
        raise KakaoClientError(f"network error: {e}") from e

    if resp.status_code not in (200, 201):
        raise KakaoClientError(
            f"Kakao send_image returned {resp.status_code}: {resp.text[:300]}"
        )
    return resp.json()


def _build_image_template(image_url: str, caption: Optional[str]) -> str:
    import json as _json
    template = {
        "object_type": "feed",
        "content": {
            "title": caption or "",
            "description": "",
            "image_url": image_url,
            "link": {
                "web_url": image_url,
                "mobile_web_url": image_url,
            },
        },
    }
    return _json.dumps(template, ensure_ascii=False)


def send_voice_message(
    *,
    agent_id: str,
    conversation_id: str,
    audio_url: str,
    duration_sec: int,
    receiver_uuid: Optional[str] = None,
) -> dict[str, Any]:
    """Send a voice message (audio file). KakaoTalk Channel typically
    handles voice notes via the file attachment path with audio/mpeg or
    audio/m4a mime type.

    Not all Kakao Channel API tiers support voice — confirm with your
    business verification. For now, raise so misconfiguration is loud
    rather than silent."""
    raise NotImplementedError(
        "Kakao voice message send not yet wired — requires verified business "
        "channel + audio file attachment support."
    )


# ============================================================================
#  Download incoming media — caller saves to our storage
# ============================================================================

def download_incoming_media(
    *,
    agent_id: str,
    media_url: str,
) -> bytes:
    """Fetch an incoming attachment URL from Kakao + return raw bytes.
    Caller uploads to our Supabase Storage for archival + display."""
    try:
        with httpx.Client(timeout=30, headers=_auth_headers(agent_id)) as client:
            resp = client.get(media_url)
    except httpx.HTTPError as e:
        raise KakaoClientError(f"download error: {e}") from e
    if resp.status_code != 200:
        raise KakaoClientError(
            f"Kakao media download returned {resp.status_code}: {resp.text[:200]}"
        )
    return resp.content
