"""
voice_summary — LLM-generated one-line summary on call end.

Triggered from the Vapi webhook's `end-of-call-report` branch in
routers/voice.py. Reads the call's transcript, asks the LLM to produce:
  - a one-sentence summary (stored in voice_calls.summary)
  - an urgency classification (low | medium | high) — saved on the row
  - a needs_review flag when STT confidence dropped low

Agent-aware: pulls the consuming agent's `knowledgeBase` from chatbot
config (if available) so the summary uses domain-specific vocabulary
(e.g. "lease", "viewing", "deposit" for VIP — different for Health).
"""

from __future__ import annotations

import json
from typing import Any, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from services import voice_service
from services.llm_client import chat_completion_sync
from services.logger import log


_SYSTEM_PROMPT = """\
You are a call-summary assistant for an AI receptionist service.
Given a phone-call transcript between a caller and the AI agent, produce:

  1. A one-sentence summary capturing the caller's intent + resolution. Plain
     declarative — no bullets, no markdown. Keep it under 200 characters.
  2. An urgency level: "high" | "medium" | "low".
     - high   = caller explicitly mentioned deposit money / 계약금 / urgent
                / legal threat / safety concern, OR the AI escalated mid-call.
     - medium = real business intent, callback expected, money discussed.
     - low    = general inquiry, info-only, no commitment.
  3. needs_review = true if the AI's responses look uncertain, off-topic, or
     if average STT confidence on user turns was below 0.7. Otherwise false.

Output ONLY valid JSON in this exact shape:
{"summary":"...","urgency":"low|medium|high","needsReview":true|false}
No prose before or after. No code fences.
"""


def _format_transcript(turns: list) -> str:
    """Render the transcript as plain text for the LLM."""
    lines: list[str] = []
    for t in turns:
        speaker = "AI" if t.role == "bot" else "Caller"
        marker = " (partial)" if getattr(t, "partial", False) else ""
        lines.append(f"{speaker}{marker}: {t.text}")
    return "\n".join(lines) if lines else "[empty transcript]"


def _avg_user_confidence(turns: list) -> Optional[float]:
    user_turns = [t for t in turns if t.role == "user" and t.confidence is not None]
    if not user_turns:
        return None
    return sum(t.confidence for t in user_turns) / len(user_turns)


def generate_and_store_summary(
    db: Session, agent_id: str, call_id: UUID | str
) -> dict[str, Any]:
    """Generate + persist summary, urgency, needs_review on the call row.
    Returns the LLM-decoded dict (also persisted). Safe to call on a
    call without transcript — falls back to a neutral summary."""
    call = voice_service.get_call(db, agent_id, call_id)
    if not call:
        return {}

    turns = voice_service.list_turns(db, agent_id, call.id)
    transcript_text = _format_transcript(turns)

    user_msg = (
        f"Agent: {agent_id}\n"
        f"Call direction: {call.direction}\n"
        f"Caller: {call.caller_name or 'Unknown'} ({call.caller_number})\n"
        f"Duration: {call.duration_sec or 0}s\n\n"
        f"Transcript:\n{transcript_text}"
    )

    try:
        raw = chat_completion_sync(
            _SYSTEM_PROMPT,
            [{"role": "user", "content": user_msg}],
            model="claude-haiku-4-5",   # cheap + fast — summary is short
        )
        # Be defensive — strip code fences if the model added them anyway.
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.strip("`").lstrip("json").strip()
        data = json.loads(raw)
    except Exception as e:
        log.warning(f"voice.summary: LLM call/parse failed: {e}", extra={"action": "voice.summary_failed"})
        # Fallback: derive a minimal summary from the last bot turn
        last_bot = next((t.text for t in reversed(turns) if t.role == "bot"), "")
        data = {
            "summary": last_bot[:200] if last_bot else "Call ended (no transcript).",
            "urgency": "low",
            "needsReview": True,    # uncertain — flag it so a human reads it
        }

    summary = str(data.get("summary") or "")[:1000]
    urgency = data.get("urgency") if data.get("urgency") in ("low", "medium", "high") else None

    # Override needs_review if STT confidence was low
    needs_review = bool(data.get("needsReview"))
    avg_conf = _avg_user_confidence(turns)
    if avg_conf is not None and avg_conf < 0.7:
        needs_review = True

    voice_service.patch_call(
        db,
        agent_id,
        call.id,
        summary=summary,
        urgency=urgency,
        needs_review=needs_review,
    )
    log.info(
        f"voice.summary: stored for {call.id} (urgency={urgency}, needs_review={needs_review})",
        extra={"action": "voice.summary_stored", "agent_id": agent_id},
    )
    return {"summary": summary, "urgency": urgency, "needsReview": needs_review}
