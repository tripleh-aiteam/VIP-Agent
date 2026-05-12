"""
Chatbot Module API — endpoints consumed by @triple-h/chatbot.

POST /chatbot/talk        — natural-language Q&A (TALK pillar)
POST /chatbot/transcribe  — audio → text (Whisper / Gemini fallback)

Each consuming agent calls these with their `agentId`. Today only "vip" is
fully wired; future agents (meeting, asset, helmet, health) will register
their intent lists + knowledge sources via a backend SDK.
"""

from __future__ import annotations

import os
import base64
import httpx
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from db.base import get_db


router = APIRouter(prefix="/chatbot", tags=["chatbot"])


# ---------------------------------------------------------------------------
# Module versioning — semver. See packages/chatbot/CHANGELOG.md for contract.
# ---------------------------------------------------------------------------
MODULE_VERSION = "1.0.0"
SUPPORTED_CLIENT_RANGE = "1.x"


@router.get("/version")
def chatbot_version():
    """
    Return the backend module version + compatibility info.
    Frontend consumers should compare against `MODULE_VERSION` exported from
    `@triple-h/chatbot` and warn if MAJOR diverges.
    """
    return {
        "module_version": MODULE_VERSION,
        "supported_client_range": SUPPORTED_CLIENT_RANGE,
        "pillars": {
            "talk": True,
            "action": True,
            "perception": True,
            "proactive": True,
            "self_improve": True,
        },
        "stable_endpoints": [
            "POST /chatbot/talk",
            "POST /chatbot/transcribe",
            "POST /chatbot/perceive",
            "POST /chatbot/proactive/emit",
            "POST /chatbot/admin/retention",
            "GET  /chatbot/health",
            "GET  /chatbot/skill-suggestions",
            "GET  /chatbot/version",
        ],
        "privacy_features": [
            "logQueries / logReplies opt-out per agent",
            "redactPatterns regex applied before persistence",
            "disableSelfImprove kills entire learning pipeline",
            "POST /chatbot/admin/retention drops old rows on demand",
        ],
    }


# ---------------------------------------------------------------------------
# /chatbot/talk — natural-language Q&A
# ---------------------------------------------------------------------------

class TalkRequest(BaseModel):
    query: str = Field(..., description="What the user said or typed (natural language)")
    language: Optional[str] = Field("auto", description="'en' / 'ko' / 'auto'")
    agentId: str = Field(..., description="Stable agent identifier — 'vip' / 'meeting' / 'asset' / ...")
    # OPTIONAL: agent's own config — when provided, the backend uses these
    # instead of any hardcoded defaults. This is what makes the module truly
    # reusable: each agent's frontend ships its own intents + knowledge base.
    intents: Optional[list[dict]] = Field(None, description="Agent's intent list (overrides backend defaults)")
    knowledgeBase: Optional[dict] = Field(None, description="Agent's static knowledge (purpose, menus, features, faq)")
    # NEW: conversational context so the chatbot can resolve pronouns ("close it",
    # "again", "do it for stocks too") and remember what just happened.
    history: Optional[list[dict]] = Field(None, description="Last N turns: [{role, text, intent?}]")
    currentPath: Optional[str] = Field(None, description="Page path the user is currently viewing")
    confirmed: Optional[bool] = Field(False, description="Set to true when re-issuing after user clicks 'Confirm' on a risky action")
    privacy: Optional[dict] = Field(None, description="Privacy controls: {logQueries, logReplies, redactPatterns[], disableSelfImprove}")


@router.post("/talk")
def talk(body: TalkRequest, db: Session = Depends(get_db)):
    """
    Natural-language Q&A endpoint. Tier 1 keyword/fuzzy match → Tier 2 LLM
    classifier-or-answerer using the agent's intent list + live knowledge.

    The agent passes its own intents + knowledgeBase in the request body.
    Backend has no agent-specific code — it just uses what was sent. This is
    what makes the chatbot module truly reusable.

    SELF-IMPROVE pillar — every call is logged and, when the LLM classifies a
    new phrasing into a known intent, that phrasing is stored as an auto-example
    so the next user typing it will hit the fast keyword path (no LLM call).

    Returns a TalkResponse with reply, language, intent, action, source.
    """
    import time
    from services.chatbot_talk import handle_talk
    from services.chatbot_self_improve import (
        log_interaction, register_auto_example, update_topic_affinity,
    )

    t0 = time.time()
    result = handle_talk(
        db, body.query, body.language or "auto", body.agentId,
        intents=body.intents, knowledge_base=body.knowledgeBase,
        history=body.history, current_path=body.currentPath,
        confirmed=bool(body.confirmed),
    )
    latency_ms = int((time.time() - t0) * 1000)

    # Self-improve hooks — fire-and-forget; never break the response on error.
    # PRIVACY: respect agent-provided privacy settings before persisting anything.
    try:
        privacy = body.privacy or {}
        log_queries = privacy.get("logQueries", True)
        log_replies = privacy.get("logReplies", True)
        redact_patterns = privacy.get("redactPatterns") or []
        disable_self_improve = bool(privacy.get("disableSelfImprove", False))

        intent = result.get("intent") if isinstance(result, dict) else None
        source = result.get("source") if isinstance(result, dict) else None
        action = (result.get("action") or {}) if isinstance(result, dict) else {}
        action_type = action.get("type") if isinstance(action, dict) else None
        reply = result.get("reply", "") if isinstance(result, dict) else ""

        # Apply redaction patterns to query + reply BEFORE persistence
        def _redact(text: str) -> str:
            if not text or not redact_patterns:
                return text
            import re as _re
            for p in redact_patterns:
                try:
                    text = _re.sub(p, "[REDACTED]", text, flags=_re.IGNORECASE)
                except _re.error:
                    pass  # bad pattern from agent config — skip
            return text

        query_to_log = _redact(body.query) if log_queries else "[NOT LOGGED]"
        reply_to_log = _redact(reply) if log_replies else "[NOT LOGGED]"

        if not disable_self_improve:
            # Phase 1.2 — log the interaction (with privacy applied)
            log_interaction(
                db,
                agent_id=body.agentId,
                query=query_to_log,
                language=result.get("language", body.language or "auto") if isinstance(result, dict) else (body.language or "auto"),
                intent=intent,
                source=source,
                reply=reply_to_log,
                action_type=action_type,
                latency_ms=latency_ms,
            )

            # Phase 1.4 — auto-vocab: only if logging queries is enabled. We never
            # promote a redacted query into intent examples (would break fast match).
            redacted_query = query_to_log
            original_query = body.query.strip()
            if (
                log_queries
                and redacted_query == original_query   # nothing was redacted
                and source == "llm"
                and intent
                and intent not in ("free_answer", "fallback", "workflow", "generated_script")
                and not intent.startswith("proactive_")
                and 4 <= len(original_query) <= 100
            ):
                register_auto_example(
                    db,
                    agent_id=body.agentId,
                    intent=intent,
                    example_text=original_query,
                    source="auto_vocab",
                    confidence=0.7,
                )

            # Phase 2.2 — topic affinity (no PII — just intent name)
            if intent:
                update_topic_affinity(db, body.agentId, None, intent)

    except Exception as _:
        pass  # never let analytics break the user-visible response

    return result


# ---------------------------------------------------------------------------
# /chatbot/transcribe — same implementation as /chat/transcribe but on the
# new module's namespace. Devs using @triple-h/chatbot expect this path.
# ---------------------------------------------------------------------------

@router.get("/health")
def chatbot_health(agentId: Optional[str] = None, hours: int = 168, db: Session = Depends(get_db)):
    """
    SELF-IMPROVE pillar — chatbot performance dashboard.
    Returns accuracy %, fallback rate, top intents, top failing queries,
    counts of corrections received and auto-examples learned.

    Used to track learning progress over time. Pass `agentId=vip` (etc.) to
    scope; omit for a global view across all agents.
    """
    from services.chatbot_self_improve import health_dashboard
    return health_dashboard(db, agent_id=agentId, hours=hours)


@router.post("/admin/retention")
def chatbot_apply_retention(agentId: str, days: int, db: Session = Depends(get_db)):
    """
    PRIVACY — delete this agent's chatbot_interactions rows older than `days`.
    Health agent: call this daily with days=30 from your own scheduler.
    Asset:        days=365.
    Each agent owns its retention policy — we provide the mechanism.
    Returns: { deleted: N }
    """
    from services.chatbot_self_improve import apply_retention
    if days <= 0:
        return {"deleted": 0, "reason": "days must be positive"}
    deleted = apply_retention(db, agent_id=agentId, days=days)
    return {"agent_id": agentId, "days_kept": days, "deleted": deleted}


@router.get("/skill-suggestions")
def chatbot_skill_suggestions(agentId: str, hours: int = 168, db: Session = Depends(get_db)):
    """
    SELF-IMPROVE pillar (Phase 3) — surface clusters of failed queries that
    suggest missing capabilities the developer should add as new intents.
    """
    from services.chatbot_self_improve import cluster_failures
    return {"agent_id": agentId, "hours_back": hours,
            "suggestions": cluster_failures(db, agentId, hours=hours)}


class ProactiveEmit(BaseModel):
    """A proactive notification the chatbot will speak/display unprompted.
    Any backend code can post this to push something into all connected chatbots."""
    title: str = Field(..., description="Short title — shown bold")
    body: Optional[str] = Field("", description="Optional longer body")
    severity: Optional[str] = Field("info", description="info / warning / error / critical")
    agentId: Optional[str] = Field(None, description="Target a specific agent's chatbot only; omit for all")
    speak: Optional[bool] = Field(True, description="Whether the chatbot should speak this aloud (TTS)")
    kind: Optional[str] = Field("alert", description="alert / briefing / reminder / insight")


@router.post("/proactive/emit")
def proactive_emit(body: ProactiveEmit):
    """
    PROACTIVE pillar — push a notification to all connected chatbot panels.
    The chatbot module's WebSocket listener picks this up and:
      - Renders it as an assistant turn with severity styling
      - Speaks it via TTS (if speak=true)
      - Marks the notification as proactive (no user query preceded it)

    Use cases:
      - Scheduler emits the daily briefing → boss hears it on dashboard load
      - Health agent pushes "BP reading high" alert
      - Smart Helmet pushes "fall detected"
      - Any backend code calling this endpoint gets free real-time chatbot delivery
    """
    from services.event_bus import publish
    payload = {
        "kind": body.kind or "alert",
        "title": body.title,
        "body": body.body or "",
        "severity": body.severity or "info",
        "agentId": body.agentId,
        "speak": bool(body.speak) if body.speak is not None else True,
        "ts": __import__("datetime").datetime.utcnow().isoformat(),
    }
    publish("chatbot.proactive", payload)
    return {"ok": True, "broadcast": payload}


@router.post("/perceive")
async def perceive_file_endpoint(
    file: UploadFile = File(...),
    user_hint: str = "",
):
    """
    PERCEPTION pillar — convert an uploaded file (image / PDF / Excel /
    CSV / DOCX / text) into TEXT that the chatbot can reason about.
    Returns: { content: str, kind: str, meta: {...} }

    Flow: frontend uploads file here → gets text → sends text + user
    question to /chatbot/talk for the actual answer.
    """
    from services.chatbot_perceive import perceive_file
    raw = await file.read()
    if len(raw) < 10:
        raise HTTPException(status_code=400, detail="File is empty")
    if len(raw) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 25 MB)")
    return await perceive_file(file.filename or "", file.content_type or "", raw, user_hint=user_hint)


@router.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    """
    Accept an audio blob (webm/ogg/mp3/wav) and transcribe.
    Tries OpenAI Whisper first; falls back to Gemini 2.5 Flash audio.
    """
    audio_bytes = await file.read()
    if len(audio_bytes) < 200:
        return {"transcript": "", "language": "auto", "reason": "audio too short"}

    content_type = file.content_type or "audio/webm"

    # Whisper first
    openai_key = os.getenv("OPENAI_API_KEY", "")
    if openai_key:
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {openai_key}"},
                    files={"file": (file.filename or "audio.webm", audio_bytes, content_type)},
                    data={"model": "whisper-1"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return {"transcript": (data.get("text") or "").strip(),
                            "language": data.get("language", "auto"),
                            "engine": "whisper"}
        except Exception:
            pass

    # Gemini fallback
    gemini_key = os.getenv("GEMINI_API_KEY", "")
    if not gemini_key:
        raise HTTPException(status_code=503, detail="No transcription engine available")

    try:
        b64 = base64.b64encode(audio_bytes).decode("ascii")
        gem_mime = content_type if content_type.startswith("audio/") else "audio/webm"
        gem_body = {
            "contents": [{
                "parts": [
                    {"inlineData": {"mimeType": gem_mime, "data": b64}},
                    {"text": "Output the words spoken in the audio. If the audio contains no speech, return exactly the single word: empty"}
                ]
            }],
            "generationConfig": {"temperature": 0.0, "maxOutputTokens": 500},
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}",
                json=gem_body,
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=f"Gemini error: {resp.text[:300]}")
            j = resp.json()
            try:
                text = j["candidates"][0]["content"]["parts"][0]["text"].strip().strip('"').strip()
            except Exception:
                text = ""
            if text.lower() == "empty":
                text = ""
            return {"transcript": text, "language": "auto", "engine": "gemini"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Transcription failed: {e}")
