"""
Kakao Channel webhook — receives incoming messages from KakaoTalk customers.

Flow:
  Customer → KakaoTalk Channel → Kakao server → THIS endpoint
       ↓
  1. Verify webhook signature (HMAC against KAKAO_WEBHOOK_SECRET_<AGENT>)
  2. Resolve agent_id from Channel ID via chatbot_channel_mappings
  3. Find or create the Customer + Conversation rows
  4. Append the incoming message
  5. Run the chatbot_reply_service (Boss-IN: draft / Boss-OUT: send)
  6. Broadcast updates to dashboard WebSocket subscribers
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from db.base import get_db
from services import chatbot_conversation_service as conv_service
from services.logger import log


router = APIRouter(prefix="/api/chatbot", tags=["chatbot"])


# In-process caches for the fast path — eliminate per-message cross-region DB
# round-trips. The channel→agent mapping changes rarely; mode changes are rare
# and fine to apply within a few seconds. This keeps the Kakao reply well
# inside the ~5s skill timeout (the DB queries were eating the LLM's budget).
import time as _time

_CHANNEL_AGENT_CACHE: dict[str, tuple[float, str]] = {}
_CHANNEL_AGENT_TTL = 3600.0  # 1 hour — channel→agent mapping never changes
                             # mid-session; keeps post-pause messages fast.
_MODE_CACHE: dict[str, tuple[float, str]] = {}
_MODE_TTL = 120.0            # 2 minutes — long enough that a paused customer's
                             # next message skips the mode DB query, short
                             # enough that a boss mode change still applies fast.


def _resolve_agent_cached(db: Session, channel: str, channel_id: str) -> Optional[str]:
    key = f"{channel}:{channel_id}"
    hit = _CHANNEL_AGENT_CACHE.get(key)
    if hit and (_time.time() - hit[0]) < _CHANNEL_AGENT_TTL:
        return hit[1] or None
    agent_id = conv_service.resolve_agent_id_from_channel(db, channel, channel_id)
    _CHANNEL_AGENT_CACHE[key] = (_time.time(), agent_id or "")
    return agent_id


def _mode_cached(db: Session, agent_id: str) -> str:
    hit = _MODE_CACHE.get(agent_id)
    if hit and (_time.time() - hit[0]) < _MODE_TTL:
        return hit[1]
    try:
        from services import chatbot_mode_detector
        mode, _ = chatbot_mode_detector.get_mode(agent_id, db=db)
    except Exception:
        mode = "out"
    _MODE_CACHE[agent_id] = (_time.time(), mode)
    return mode


def invalidate_business_caches() -> None:
    """Clear the channel→agent and mode caches so a newly-added/edited business
    routes immediately (instead of waiting for the TTL). Called by the admin
    'Add business' endpoint."""
    _CHANNEL_AGENT_CACHE.clear()
    _MODE_CACHE.clear()


def warm_kakao_caches(db: Session) -> None:
    """Force-refresh the fast-path caches (channel→agent, mode, realty KB).

    Called by /health, which UptimeRobot pings every 5 minutes — so the caches
    stay perpetually warm and a customer's FIRST message after ANY pause still
    hits warm caches and replies inside Kakao's 5s window. Best-effort."""
    from sqlalchemy import text as _sa_text
    now = _time.time()
    try:
        rows = db.execute(_sa_text(
            "SELECT channel, provider_channel_id, agent_id "
            "FROM chatbot_channel_mappings WHERE active = true"
        )).fetchall()
        seen_agents = set()
        for ch, pid, agent in rows:
            _CHANNEL_AGENT_CACHE[f"{ch}:{pid}"] = (now, agent or "")
            if agent and agent not in seen_agents:
                seen_agents.add(agent)
                try:
                    from services import chatbot_mode_detector
                    mode, _ = chatbot_mode_detector.get_mode(agent, db=db)
                    _MODE_CACHE[agent] = (now, mode)
                except Exception:
                    pass
                try:
                    # Warm the per-tenant profile-card cache so the fast path
                    # stays DB-free for white-label tenants too.
                    from services import tenant_config
                    tenant_config.get_tenant_config(agent, db=db)
                except Exception:
                    pass
    except Exception as e:
        log.warning(f"warm_kakao_caches: channel/mode warm failed: {e}")
    try:
        # Warm the realty KB (Excel parse) so it isn't re-parsed on the hot path.
        from services.chatbot_talk import _triple_h_realty_knowledge_base
        _triple_h_realty_knowledge_base()
    except Exception as e:
        log.warning(f"warm_kakao_caches: KB warm failed: {e}")


# ============================================================================
#  HMAC signature verification — Kakao signs every webhook payload
# ============================================================================

def _verify_kakao_signature(
    agent_id: Optional[str], raw_body: bytes, signature: Optional[str]
) -> bool:
    """Verify the X-Kakao-Signature header. Returns True when no secret
    is configured (dev mode, smoke testing). Production sets:
        KAKAO_WEBHOOK_SECRET_<AGENT_UPPER>
    The webhook secret comes from the Kakao Developer Console when you
    register the webhook URL."""
    if not agent_id:
        # Can't pick the right secret without agent_id — caller is
        # responsible for resolving agent_id before calling this. For
        # the initial dispatch (before resolution), use a global fallback.
        secret = os.getenv("KAKAO_WEBHOOK_SECRET", "")
    else:
        # Multi-tenant: per-tenant secret stored in DB (buyer self-entered)
        # takes priority; env vars remain the fallback for existing agents.
        secret = ""
        try:
            secret = (conv_service.get_agent_kakao_credentials(agent_id) or {}).get("webhook_secret", "")
        except Exception:
            secret = ""
        if not secret:
            secret = os.getenv(f"KAKAO_WEBHOOK_SECRET_{agent_id.upper()}", "") or os.getenv(
                "KAKAO_WEBHOOK_SECRET", ""
            )
    if not secret:
        return True
    if not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


# ============================================================================
#  Rate-limit admin endpoints (boss can inspect / unblock)
# ============================================================================

@router.get("/webhook/kakao/security/stats")
def kakao_rate_limit_stats():
    """Snapshot of current rate-limiter state."""
    from services.rate_limiter import stats
    return stats()


@router.get("/webhook/kakao/security/check/{ip}")
def kakao_rate_limit_check_ip(ip: str):
    """Is this IP currently blocked? Returns block info or null."""
    from services.rate_limiter import get_block_status
    s = get_block_status(ip)
    return s or {"ip": ip, "blocked": False}


@router.post("/webhook/kakao/security/unblock/{ip}")
def kakao_rate_limit_unblock(ip: str):
    """Manually lift a block (boss override)."""
    from services.rate_limiter import unblock_ip
    return {"ip": ip, "unblocked": unblock_ip(ip)}


# ============================================================================
#  Webhook entry point — single endpoint for ALL agents
# ============================================================================

@router.post("/webhook/kakao")
async def kakao_webhook(request: Request, db: Session = Depends(get_db)):
    """All Kakao Channel events arrive here.

    Kakao webhook payload (canonical shape varies slightly by event):
    {
      "user_request": {
        "user": { "id": "kakao_uuid", "type": "appUserId" },
        "utterance": "안녕하세요"        # text the user typed
      },
      "bot": { "id": "channel_id" },
      "channel": { "id": "channel_id", "name": "..." },
      "action": { "name": "..." },
      "params": { ... },
      ...
    }

    Custom skill servers (which we are) receive POST requests here whenever
    a user types in the Channel chat. We respond with the bot's reply
    (Boss-OUT) OR persist a draft for boss approval (Boss-IN).
    """
    # ────────────────────────────────────────────────────────────────────
    # SECURITY — Rate limit by IP to block abusers / brute-force attempts.
    # Default: 30 req/min per IP; exceeding it blocks the IP for 10 min.
    # Kakao's legitimate webhook traffic is far below this threshold.
    # Anyone pinging us faster than that is either probing the URL or
    # attacking. Returns 429 Too Many Requests with Retry-After header.
    # ────────────────────────────────────────────────────────────────────
    from services.rate_limiter import rate_limit_ip, rate_limit_user
    # X-Forwarded-For takes precedence (Render/Cloudflare prepend client IP)
    client_ip = (
        request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (request.client.host if request.client else "")
        or "unknown"
    )
    allowed, retry = rate_limit_ip(
        client_ip,
        limit_per_min=int(os.getenv("KAKAO_RATE_LIMIT_IP_PER_MIN", "30")),
        block_minutes=int(os.getenv("KAKAO_RATE_LIMIT_BLOCK_MIN", "10")),
    )
    if not allowed:
        log.warning(
            f"kakao.webhook: IP rate-limited {client_ip} (retry in {retry}s)",
            extra={"action": "kakao.rate_limit_ip", "ip": client_ip, "retry_after": retry},
        )
        raise HTTPException(
            status_code=429,
            detail="Too many requests",
            headers={"Retry-After": str(retry)},
        )

    raw = await request.body()
    signature = request.headers.get("x-kakao-signature") or request.headers.get(
        "X-Kakao-Signature"
    )

    try:
        payload = json.loads(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed JSON")

    # ────────────────────────────────────────────────────────────────────
    # SECURITY — Per-Kakao-user throttle. Even from a valid IP, a single
    # customer shouldn't be able to flood. Default: 12 msgs/min/user.
    # Beyond that, we drop the message (no reply) — gentler than blocking
    # the entire IP because Kakao IPs are shared.
    # ────────────────────────────────────────────────────────────────────
    try:
        kakao_uid = ((payload.get("userRequest") or payload.get("user_request") or {})
                     .get("user") or {}).get("id", "")
    except Exception:
        kakao_uid = ""
    if kakao_uid:
        ok_user, retry_u = rate_limit_user(
            kakao_uid,
            limit_per_min=int(os.getenv("KAKAO_RATE_LIMIT_USER_PER_MIN", "12")),
        )
        if not ok_user:
            log.warning(
                f"kakao.webhook: user-throttled {kakao_uid[:12]} (retry in {retry_u}s)",
                extra={"action": "kakao.rate_limit_user", "user_id": kakao_uid, "retry_after": retry_u},
            )
            # Return a quiet OK so Kakao doesn't retry — we silently drop.
            return {"version": "2.0", "template": {"outputs": []}}

    # Step 1 — Resolve channel → agent_id
    channel_id = (
        (payload.get("channel") or {}).get("id")
        or (payload.get("bot") or {}).get("id")
        or ""
    )
    agent_id = _resolve_agent_cached(db, "kakao", channel_id)
    if not agent_id:
        log.warning(
            f"kakao.webhook: unknown channel {channel_id}",
            extra={"action": "kakao.webhook_unknown_channel"},
        )
        return {"ok": True, "skipped": "unknown channel"}

    # Step 2 — Verify signature now that we know the agent
    if not _verify_kakao_signature(agent_id, raw, signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # Step 3 — Extract user identity + message.
    # Kakao i 오픈빌더 sends `userRequest` (camelCase). Older internal
    # callers may send `user_request` (snake_case). Accept both so this
    # handler works with the real Kakao payload AND our own integration
    # tests / internal forwarders.
    user_request = payload.get("userRequest") or payload.get("user_request") or {}
    user = user_request.get("user") or {}
    user_id = user.get("id") or ""
    user_phone = user.get("phone")            # may be absent unless customer shared

    # Customer display name. Kakao's skill payload usually carries only an
    # anonymized user.id; a real nickname appears under user.properties ONLY
    # when the channel collects profile info (i 오픈빌더 → 봇 설정 → 사용자
    # 정보). Use it when present, else fall back to a friendly, stable label
    # so the inbox shows something nicer than "Customer".
    user_props = user.get("properties") or {}
    cust_name = (
        user_props.get("nickname")
        or user_props.get("name")
        or user_props.get("profile_nickname")
        or ""
    ).strip()
    if not cust_name and user_id:
        cust_name = f"카카오 고객 {user_id[-4:]}"

    utterance = (user_request.get("utterance") or "").strip()
    attachment_type = (user_request.get("type") or "text").lower()

    # ── FAST PATH: plain text + auto-reply (Boss-OUT), no callback ─────────
    # The full pipeline does several cross-region DB round-trips before the
    # reply, pushing total time to ~4.5-5.5s — past Kakao's ~5s skill timeout —
    # so LLM answers were computed (visible in the dashboard) but never reached
    # the customer's phone. Here we compute the reply with essentially NO DB
    # work (one mode check + cached KB + one Groq call) and return it
    # immediately, then persist customer/conversation/messages in the
    # background. Callback requests and non-text messages use the full path.
    _callback_url_fp = (
        user_request.get("callbackUrl") or payload.get("callbackUrl") or ""
    )
    if attachment_type == "text" and utterance and not _callback_url_fp:
        _mode = _mode_cached(db, agent_id)
        if _mode == "out":
            from services.chatbot_reply_service import generate_quick_reply
            _pid = (
                payload.get("message_id") or payload.get("messageId")
                or (user_request.get("message") or {}).get("id")
            )
            reply_text = await generate_quick_reply(agent_id, utterance)
            asyncio.create_task(_persist_text_exchange_bg(
                agent_id=agent_id, kakao_user_id=user_id, phone=user_phone,
                name=cust_name or None, utterance=utterance,
                reply_text=reply_text, provider_msg_id=_pid,
            ))
            if reply_text:
                return {
                    "version": "2.0",
                    "template": {"outputs": [{"simpleText": {"text": reply_text}}]},
                }
            return {"ok": True}
    # ── end fast path ──────────────────────────────────────────────────────

    # Step 4 — Find or create customer + conversation
    customer = conv_service.find_or_create_customer(
        db,
        agent_id,
        name=cust_name or None,
        kakao_user_id=user_id,
        phone=user_phone,
    )
    conv = conv_service.find_or_create_conversation(
        db, agent_id, channel="kakao", customer_id=customer.id
    )

    # Step 5 — Append the incoming message (idempotent on provider_message_id if Kakao sends one)
    provider_msg_id = (
        payload.get("message_id")
        or payload.get("messageId")
        or (user_request.get("message") or {}).get("id")
    )

    # Determine message kind based on Kakao's payload type
    if attachment_type in ("audio", "voice"):
        # Voice message — see services/kakao_voice_handler.py (Phase A15)
        await _handle_voice_message(
            db, agent_id, conv, customer, payload, provider_msg_id
        )
    elif attachment_type in ("image", "photo"):
        await _handle_image_message(
            db, agent_id, conv, customer, payload, provider_msg_id
        )
    elif attachment_type in ("file", "document"):
        await _handle_file_message(
            db, agent_id, conv, customer, payload, provider_msg_id
        )
    else:
        # Default: text — use Kakao's callback pattern because the LLM call
        # takes 5-8 seconds, which exceeds Kakao's 5-second skill timeout.
        # Callback extends the timeout to 60 seconds: we return immediately
        # with `useCallback: true`, then POST the real reply to Kakao's
        # callback URL when the LLM call finishes.
        msg = conv_service.append_message(
            db, agent_id, conv.id,
            author="customer",
            kind="text",
            text=utterance,
            provider_message_id=provider_msg_id,
        )

        # Kakao supplies a callbackUrl per request when callback is enabled
        # in the bot's 시나리오/스킬 settings. If absent, fall through to
        # synchronous reply (best-effort, may time out on slow LLM calls).
        callback_url = (
            user_request.get("callbackUrl")
            or payload.get("callbackUrl")
            or ""
        )

        if callback_url:
            import asyncio as _asyncio

            async def _send_callback_reply() -> None:
                """Compute the reply, then POST it to Kakao's callback URL."""
                try:
                    reply = await _process_text_message(
                        db, agent_id, conv, customer, utterance
                    )
                    body = {
                        "version": "2.0",
                        "template": {
                            "outputs": [
                                {"simpleText": {"text": reply or "잠시 후 다시 답변드리겠습니다."}}
                            ],
                        },
                    }
                    import httpx as _httpx
                    async with _httpx.AsyncClient(timeout=30) as client:
                        await client.post(callback_url, json=body)
                except Exception as e:
                    log.warning(
                        f"kakao.webhook: callback dispatch failed: {e}",
                        extra={"action": "kakao.callback_failed"},
                    )

            # Fire-and-forget — Kakao's 5s ack window doesn't wait on this.
            _asyncio.create_task(_send_callback_reply())

            # Broadcast inbound now (the reply broadcast happens after the
            # callback completes; that's good enough for the dashboard).
            try:
                from routers.chatbot_inbox import get_broker
                updated = conv_service.get_conversation(db, agent_id, conv.id)
                customer_obj = conv_service.get_customer(db, agent_id, updated.customer_id)
                get_broker().publish_sync(
                    agent_id,
                    {
                        "type": "conversation.updated",
                        "conversation": conv_service.serialize_conversation(
                            updated, customer=customer_obj
                        ),
                    },
                )
            except Exception as e:
                log.warning(f"kakao.webhook: broadcast failed: {e}")

            # Tell Kakao we'll send the reply asynchronously via callback.
            return {"version": "2.0", "useCallback": True}

        # Fallback path (no callbackUrl supplied) — synchronous reply.
        # Will time out on slow LLM calls; configure callback in i 오픈빌더
        # to avoid this.
        reply_text = await _process_text_message(db, agent_id, conv, customer, utterance)

    # Step 6 — Broadcast updated conversation to dashboard subscribers
    try:
        from routers.chatbot_inbox import get_broker
        updated = conv_service.get_conversation(db, agent_id, conv.id)
        customer = conv_service.get_customer(db, agent_id, updated.customer_id)
        get_broker().publish_sync(
            agent_id,
            {
                "type": "conversation.updated",
                "conversation": conv_service.serialize_conversation(
                    updated, customer=customer
                ),
            },
        )
    except Exception as e:
        log.warning(f"kakao.webhook: broadcast failed: {e}")

    # Step 7 — Return reply inline in Kakao i 오픈빌더 skill format. This is
    # what i 오픈빌더 expects from a skill webhook: it shows the `simpleText`
    # to the customer in the channel. Returning `{"ok": True}` alone causes
    # the bot to show no reply to the customer.
    if attachment_type not in ("audio", "voice", "image", "photo", "file", "document"):
        reply_text = locals().get("reply_text") or ""
        if reply_text:
            return {
                "version": "2.0",
                "template": {
                    "outputs": [
                        {"simpleText": {"text": reply_text}}
                    ],
                },
            }
    return {"ok": True}


# ============================================================================
#  Per-message-type handlers
# ============================================================================

import re as _re


def _extract_customer_name(text: str) -> Optional[str]:
    """Best-effort: pull a name out of a customer's self-introduction so the
    inbox can show a real name instead of '카카오 고객 ab12'. Conservative — only
    fires on clear 'my name is …' / '저는 …입니다' style intros."""
    t = (text or "").strip()
    if not t or len(t) > 60:
        return None
    # English: "my name is David", "I'm David Kim", "this is David", "call me David"
    m = _re.search(
        r"(?:my name is|i am|i'm|this is|call me|name's)\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)",
        t, _re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()[:40]
    # Korean self-intro + ending: "저는 김철수입니다", "제 이름은 박영희예요"
    m = _re.search(
        r"(?:제\s*이름은|저는|나는|이름은)\s*([가-힣]{2,4})"
        r"(?:입니다|이에요|예요|이라고\s*합니다|라고\s*합니다|이라고|라고|이고|임|야)", t)
    if m:
        return m.group(1).strip()
    # Korean self-intro, name at end (no ending): "저는 김철수", "제 이름은 박영희"
    m = _re.search(r"(?:제\s*이름은|저는|나는|이름은)\s*([가-힣]{2,4})\s*$", t)
    if m:
        return m.group(1).strip()
    # Bare "김철수입니다" / "김철수라고 합니다"
    m = _re.search(r"^([가-힣]{2,4})\s*(?:입니다|이에요|예요|라고\s*합니다|이라고\s*합니다)$", t)
    if m:
        return m.group(1).strip()
    return None


async def _persist_text_exchange_bg(
    *, agent_id: str, kakao_user_id: str, phone, name,
    utterance: str, reply_text: str, provider_msg_id,
) -> None:
    """Persist a FAST-PATH text exchange AFTER the reply was already returned
    to Kakao. Best-effort, off the hot path: creates the customer +
    conversation, appends the customer's message and the bot's reply, and
    broadcasts to the dashboard so the inbox stays accurate."""
    from db.base import SessionLocal
    db_bg = SessionLocal()
    try:
        customer = conv_service.find_or_create_customer(
            db_bg, agent_id, name=name, kakao_user_id=kakao_user_id, phone=phone,
        )
        # If the customer introduced themselves ("저는 김철수입니다" / "my name is
        # David"), upgrade the placeholder name to the real one.
        try:
            real_name = _extract_customer_name(utterance)
            if real_name:
                conv_service.maybe_capture_customer_name(
                    db_bg, agent_id, customer.id, real_name)
        except Exception:
            pass
        conv = conv_service.find_or_create_conversation(
            db_bg, agent_id, channel="kakao", customer_id=customer.id,
        )
        conv_service.append_message(
            db_bg, agent_id, conv.id, author="customer", kind="text",
            text=utterance, provider_message_id=provider_msg_id,
        )
        if reply_text:
            conv_service.append_message(
                db_bg, agent_id, conv.id, author="bot", kind="text",
                text=reply_text, bot_meta={"status": "auto", "source": "fast-path"},
            )
            try:
                conv_service.patch_conversation(
                    db_bg, agent_id, conv.id,
                    status="bot_handling", suggested_reply_json=None,
                )
            except Exception:
                pass
        try:
            from routers.chatbot_inbox import get_broker
            updated = conv_service.get_conversation(db_bg, agent_id, conv.id)
            cust_obj = conv_service.get_customer(db_bg, agent_id, updated.customer_id)
            get_broker().publish_sync(agent_id, {
                "type": "conversation.updated",
                "conversation": conv_service.serialize_conversation(updated, customer=cust_obj),
            })
        except Exception as e:
            log.warning(f"kakao.fastpath: broadcast failed: {e}")
    except Exception as e:
        log.warning(f"kakao.fastpath: persist failed: {e}")
    finally:
        db_bg.close()


async def _process_text_message(
    db: Session, agent_id: str, conv, customer, utterance: str
) -> str:
    """Run the reply pipeline for a text message. Returns the bot's reply
    text (empty string if no reply was generated, e.g. Boss-IN mode where
    bot stays silent and waits for the boss to respond manually).

    The reply is also dispatched via `on_send` (Kakao Channel Message API)
    as a redundant outbound — but the primary delivery path is the inline
    return value, which i 오픈빌더 picks up from the webhook response."""
    if not utterance:
        return ""
    try:
        from services import chatbot_reply_service
        from services import kakao_client

        async def _send(text: str, _agent: str, _conv) -> None:
            try:
                kakao_client.send_text(
                    agent_id=agent_id,
                    conversation_id=str(_conv.id),
                    text=text,
                    receiver_uuid=customer.kakao_user_id,
                )
            except Exception as e:
                # Outbound send may fail until the channel message-send
                # permission is approved by Kakao — that's OK, the inline
                # webhook return still delivers the reply.
                log.warning(f"kakao.send via reply_service failed: {e}")

        result = await chatbot_reply_service.handle_incoming_message(
            db, agent_id, conv, utterance, customer=customer, on_send=_send
        )
        if isinstance(result, dict):
            return (result.get("reply") or "").strip()
        return ""
    except Exception as e:
        log.warning(
            f"kakao.webhook: reply pipeline error: {e}",
            extra={"action": "kakao.webhook_reply_failed"},
        )
        return ""


async def _handle_voice_message(
    db: Session, agent_id: str, conv, customer, payload: dict, provider_msg_id: Optional[str]
) -> None:
    """Voice message: download audio → Whisper transcribe → reply pipeline.

    Flow:
      1. Persist the incoming voice message row immediately (empty transcript)
         so the dashboard renders the bubble even before transcription completes
      2. Download the audio file from Kakao (via the customer's auth scope)
      3. Transcribe via OpenAI Whisper API (handles mp3/m4a/wav directly,
         language="ko" hint for KR-first)
      4. Update the message row with the transcript + STT confidence
      5. Run the chatbot_reply_service with the transcript as the user's
         "utterance" — same path as text messages
    """
    media = (payload.get("userRequest") or payload.get("user_request") or {}).get("media") or {}
    audio_url = media.get("url") or ""
    duration = media.get("duration_sec") or 0
    if not audio_url:
        log.warning("kakao.webhook: voice message missing media URL", extra={"action": "kakao.voice_no_url"})
        return

    # 1. Persist the message row first (so dashboard sees it immediately)
    msg = conv_service.append_message(
        db, agent_id, conv.id,
        author="customer",
        kind="voice",
        voice_url=audio_url,
        voice_duration_sec=int(duration) if duration else None,
        provider_message_id=provider_msg_id,
    )

    # 2. Download + transcribe (best-effort — failures don't break the inbox)
    transcript = ""
    confidence: Optional[float] = None
    try:
        from services import kakao_client
        audio_bytes = await asyncio.to_thread(
            kakao_client.download_incoming_media,
            agent_id=agent_id,
            media_url=audio_url,
        )
        transcript, confidence = await _transcribe_audio_bytes(audio_bytes)
    except Exception as e:
        log.warning(
            f"kakao.webhook: voice transcription failed: {e}",
            extra={"action": "kakao.voice_stt_failed"},
        )

    # 3. Update the persisted message with the transcript (if we got one)
    if transcript and msg:
        from db.models import ChatbotMessage as _M
        m_row = db.query(_M).filter(_M.id == msg.id).first()
        if m_row:
            m_row.voice_transcript = transcript
            if confidence is not None:
                m_row.confidence = confidence
            db.commit()

    # 4. Run the reply pipeline using the transcript as the "utterance"
    if transcript:
        await _process_text_message(db, agent_id, conv, customer, transcript)
    else:
        # Fallback: empty transcript → tell the customer we didn't catch it
        await _process_text_message(
            db, agent_id, conv, customer,
            "(음성 메시지를 받았지만 명확히 인식하지 못했습니다.)"
        )


async def _transcribe_audio_bytes(audio_bytes: bytes) -> tuple[str, Optional[float]]:
    """Transcribe arbitrary audio bytes via OpenAI Whisper API.

    Kakao voice notes are typically MP3/M4A. Whisper auto-detects format
    when uploaded via multipart — no codec conversion needed on our side.
    Language hint "ko" biases toward Korean (Whisper falls back to detection
    if the audio is actually English).

    Returns (transcript, confidence). Whisper's `transcriptions` endpoint
    doesn't return confidence directly, so we return None for it.
    """
    import os as _os
    if not audio_bytes or len(audio_bytes) < 200:
        return "", None
    api_key = _os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return "", None

    import httpx as _httpx
    try:
        async with _httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": ("kakao_voice.mp3", audio_bytes, "audio/mpeg")},
                data={"model": "whisper-1", "language": "ko"},
            )
            if resp.status_code != 200:
                log.warning(
                    f"kakao.webhook: Whisper API {resp.status_code}: {resp.text[:200]}",
                    extra={"action": "kakao.whisper_failed"},
                )
                return "", None
            text = (resp.json().get("text") or "").strip()
            return text, None
    except Exception as e:
        log.warning(f"kakao.webhook: Whisper transcribe error: {e}")
        return "", None


async def _handle_image_message(
    db: Session, agent_id: str, conv, customer, payload: dict, provider_msg_id: Optional[str]
) -> None:
    """Image message: download → Gemini Vision describes it → combine with
    customer's caption → run reply pipeline.

    The Gemini-described content + the caption together form the "utterance"
    that goes into the LLM brain. E.g. a leaking-ceiling photo with caption
    "어제부터 이래요" gives the LLM enough context to draft a maintenance
    response without the customer needing to type a detailed description.
    """
    media = (payload.get("userRequest") or payload.get("user_request") or {}).get("media") or {}
    image_url = media.get("url") or ""
    caption = (payload.get("userRequest") or payload.get("user_request") or {}).get("utterance", "") or None
    image_width = media.get("width") or None
    image_height = media.get("height") or None
    if not image_url:
        log.warning("kakao.webhook: image message missing media URL")
        return

    # Persist row first so dashboard renders immediately
    conv_service.append_message(
        db, agent_id, conv.id,
        author="customer",
        kind="image",
        image_url=image_url,
        image_caption=caption,
        image_width=int(image_width) if image_width else None,
        image_height=int(image_height) if image_height else None,
        provider_message_id=provider_msg_id,
    )

    # Download + Gemini Vision describe (best-effort)
    vision_description = ""
    try:
        from services import kakao_client
        from services.chatbot_perceive import perceive_image
        image_bytes = await asyncio.to_thread(
            kakao_client.download_incoming_media,
            agent_id=agent_id,
            media_url=image_url,
        )
        # Guess mime from URL extension; default to jpeg
        mime = "image/jpeg"
        url_lower = image_url.lower()
        if ".png" in url_lower:
            mime = "image/png"
        elif ".webp" in url_lower:
            mime = "image/webp"
        elif ".gif" in url_lower:
            mime = "image/gif"
        result = await perceive_image(
            image_bytes, mime, user_hint=caption or ""
        )
        vision_description = (result.get("content") or "").strip()
        log.info(
            f"kakao.webhook: image perceived ({len(vision_description)} chars)",
            extra={"action": "kakao.image_perceived"},
        )
    except Exception as e:
        log.warning(
            f"kakao.webhook: image perception failed: {e}",
            extra={"action": "kakao.image_perceive_failed"},
        )

    # Compose utterance: caption + vision description
    parts: list[str] = []
    if caption:
        parts.append(f"고객 메시지: {caption}")
    if vision_description:
        parts.append(f"[이미지 분석]\n{vision_description}")
    utterance = "\n\n".join(parts) if parts else "(이미지를 받았습니다.)"

    # Run reply pipeline
    await _process_text_message(db, agent_id, conv, customer, utterance)


async def _handle_file_message(
    db: Session, agent_id: str, conv, customer, payload: dict, provider_msg_id: Optional[str]
) -> None:
    """File attachment: persist metadata; downstream processing in Phase A16."""
    media = (payload.get("userRequest") or payload.get("user_request") or {}).get("media") or {}
    file_url = media.get("url") or ""
    file_name = media.get("name") or "file"
    file_mime = media.get("mime_type") or "application/octet-stream"
    file_size = media.get("size_bytes") or 0
    conv_service.append_message(
        db, agent_id, conv.id,
        author="customer",
        kind="file",
        file_url=file_url,
        file_name=file_name,
        file_mime=file_mime,
        file_size_bytes=int(file_size) if file_size else None,
        provider_message_id=provider_msg_id,
    )
