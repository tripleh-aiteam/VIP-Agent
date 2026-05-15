"""
chatbot_boss_observer — the bot's "watching boss work" pipeline.

Every time the boss sends a reply in Boss-IN mode, this module:
  1. Pairs the boss's reply with the customer's preceding message
  2. Persists the pair as a training example (auto-vocab + auto-knowledge)
  3. Extracts factual claims from the reply ("월세 120만원", "주차 무료") and
     stores them as knowledge-base entries the bot can use later
  4. Updates per-agent style profile (tone, average length, common phrases)
  5. Future: trains a per-agent fine-tune target via the LLM provider

The goal: over weeks of usage, the bot's autonomous replies (Boss-OUT)
and its on-demand suggestions (Boss-IN AI button) progressively sound
more like the boss — same phrasing, same facts, same tone.

Triggered from routers/chatbot_inbox.py:reply (boss manually replies)
and routers/chatbot_inbox.py:approve_draft (boss approves an AI draft —
treated as a positive signal).

All side-effects are best-effort: a failed observer call NEVER breaks
the user-facing reply path. Boss's reply lands either way.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from db.models import (
    ChatbotConversation,
    ChatbotCustomer,
    ChatbotMessage,
)
from services.logger import log


# ============================================================================
#  Public entry point — called by the REST router after a boss reply
# ============================================================================

async def observe_boss_reply(
    db: Session,
    agent_id: str,
    conversation: ChatbotConversation,
    boss_reply_text: str,
    *,
    edited_from_draft: Optional[str] = None,
) -> dict[str, Any]:
    """Process a boss reply for learning.

    Args:
      conversation: the conversation the boss replied in
      boss_reply_text: what the boss said
      edited_from_draft: if boss approved + edited an AI draft, the original
                         draft text — diff between draft and final reply is
                         a strong correction signal

    Returns a summary of what was learned. Side-effects:
      - chatbot_interactions row (via chatbot_self_improve.log_interaction)
      - chatbot_auto_examples row when the customer message is novel
      - chatbot_knowledge_base entries when boss revealed facts
      - chatbot_user_profiles update (tone + length stats per agent)
    """
    # Find the customer message the boss was replying to
    msgs = (
        db.query(ChatbotMessage)
        .filter(ChatbotMessage.conversation_id == conversation.id)
        .order_by(desc(ChatbotMessage.at))
        .limit(20)
        .all()
    )
    # Walk backwards from latest non-boss message
    customer_msg: Optional[ChatbotMessage] = None
    for m in msgs:
        if m.author == "customer":
            customer_msg = m
            break
    if not customer_msg:
        return {"skipped": "no preceding customer message"}

    customer_text = (
        customer_msg.text or customer_msg.voice_transcript or customer_msg.image_caption or ""
    ).strip()
    if not customer_text:
        return {"skipped": "customer message empty"}

    learned: dict[str, Any] = {
        "pair": True,
        "facts": [],
        "style_updated": False,
        "correction": edited_from_draft is not None and edited_from_draft != boss_reply_text,
    }

    # 1. Log the pair as a training interaction
    try:
        from services.chatbot_self_improve import log_interaction
        await asyncio.to_thread(
            log_interaction,
            db,
            agent_id=agent_id,
            query=customer_text,
            language="ko",        # default; could detect
            intent="boss_reply_observed",
            source="boss_observation",
            reply=boss_reply_text,
            action_type=None,
            latency_ms=0,
        )
    except Exception as e:
        log.warning(f"boss_observer: log_interaction failed: {e}")

    # 2. If boss edited an AI draft, that's a high-value correction
    if learned["correction"]:
        try:
            from services.chatbot_self_improve import register_auto_example
            register_auto_example(
                db,
                agent_id=agent_id,
                intent="correction",
                example_text=f"{customer_text} → {boss_reply_text}",
                source="correction",
                confidence=1.0,
            )
            log.info(
                f"boss_observer: correction recorded — boss edited AI draft "
                f"for conv {conversation.id}",
                extra={"action": "chatbot.correction_learned"},
            )
        except Exception as e:
            log.warning(f"boss_observer: correction logging failed: {e}")

    # 3. Extract factual claims from the boss's reply
    facts = _extract_facts(boss_reply_text)
    if facts:
        learned["facts"] = facts
        await _persist_facts(db, agent_id, facts, source_conv=str(conversation.id))

    # 4. Update style profile (tone + length statistics)
    try:
        await _update_style_profile(db, agent_id, boss_reply_text)
        learned["style_updated"] = True
    except Exception as e:
        log.warning(f"boss_observer: style profile update failed: {e}")

    log.info(
        f"boss_observer: learned from conv {conversation.id} — "
        f"facts={len(facts)}, correction={learned['correction']}",
        extra={"action": "chatbot.boss_observed"},
    )
    return learned


# ============================================================================
#  Fact extraction — regex + heuristics catch the most common patterns
# ============================================================================

# Korean + English patterns the boss might mention in casual replies.
# We're being conservative — only extract clear, structured facts.
FACT_PATTERNS = [
    # Money: "월세 120만원" / "보증금 1000만원" / "₩50,000"
    (re.compile(r"(월세|보증금|관리비|계약금)\s*([\d,]+\s*(?:만원|원|KRW|₩))", re.IGNORECASE),
     lambda m: f"{m.group(1)}: {m.group(2).strip()}"),
    # Properties: "A-303호" / "B-201호" / "C-Tower"
    (re.compile(r"([A-Z]-\d{3}호|[A-Z]+-?[A-Za-z]+(?:Tower|타워|빌딩))", re.IGNORECASE),
     lambda m: f"Property: {m.group(1)}"),
    # Date/time references: "내일 오후 2시" / "5월 15일" / "다음 주 월요일"
    (re.compile(r"(\d{1,2}월\s*\d{1,2}일|다음\s*(?:주|달)|내일|모레|이번\s*주말?|평일|주말)"),
     lambda m: f"Time: {m.group(1).strip()}"),
    # Phone numbers (E.164 + KR formats)
    (re.compile(r"(\+82-?\d{2,3}-?\d{3,4}-?\d{4}|01\d-\d{3,4}-\d{4})"),
     lambda m: f"Phone: {m.group(1)}"),
    # Yes/No policies: "가능합니다" / "불가능합니다" / "안 됩니다"
    (re.compile(r"(주차|반려동물|애완동물|흡연|음주|보일러|에어컨)\s*[^.]*?(가능|불가|안\s*돼|없습니다|있습니다)"),
     lambda m: f"Policy: {m.group(0).strip()}"),
]


def _extract_facts(text: str) -> list[str]:
    """Extract factual claims from text. Returns list of structured facts.

    Conservative — false positives in knowledge base are worse than missing
    facts, so we only catch high-confidence patterns. Boss can manually add
    other facts via the dashboard."""
    if not text:
        return []
    facts: list[str] = []
    seen: set[str] = set()
    for pattern, formatter in FACT_PATTERNS:
        for match in pattern.finditer(text):
            try:
                fact = formatter(match)
                if fact and fact not in seen:
                    seen.add(fact)
                    facts.append(fact)
            except Exception:
                continue
    return facts[:10]   # cap per reply


async def _persist_facts(
    db: Session, agent_id: str, facts: list[str], source_conv: str
) -> None:
    """Save extracted facts to chatbot_knowledge_base via existing pipeline.
    The chatbot_talk LLM picks these up as KB context on future replies."""
    try:
        from services.chatbot_self_improve import register_auto_example
        for fact in facts:
            register_auto_example(
                db,
                agent_id=agent_id,
                intent="boss_revealed_fact",
                example_text=fact,
                source="boss_observation",
                confidence=0.85,
            )
    except Exception as e:
        log.warning(f"boss_observer: fact persistence failed: {e}")


# ============================================================================
#  Style profile — tracks boss's tone over time
# ============================================================================

async def _update_style_profile(db: Session, agent_id: str, reply_text: str) -> None:
    """Update the agent's style profile based on a fresh boss reply.

    Tracks:
      - Average reply length (chars + sentences)
      - Tone signals (formal endings 합니다/입니다 vs casual ~요/~지)
      - Common phrasings (top N tokens that appear)

    Stored in chatbot_user_profiles.topic_affinity JSON (reusing the existing
    structure). Future: the LLM system prompt injects this profile so
    autonomous replies sound progressively more like the boss."""
    try:
        from db.models import ChatbotUserProfile

        # Find or create the per-agent style profile
        profile = (
            db.query(ChatbotUserProfile)
            .filter(
                ChatbotUserProfile.agent_id == agent_id,
                ChatbotUserProfile.user_id.is_(None),       # agent-level profile, not user-level
            )
            .first()
        )
        if not profile:
            profile = ChatbotUserProfile(
                agent_id=agent_id,
                user_id=None,
                language_preference="ko",
                interaction_count=0,
                topic_affinity={},
            )
            db.add(profile)

        affinity = dict(profile.topic_affinity or {})
        affinity["boss_reply_count"] = int(affinity.get("boss_reply_count", 0)) + 1

        # Running average of length
        prev_avg = float(affinity.get("avg_reply_chars", 0))
        n = affinity["boss_reply_count"]
        affinity["avg_reply_chars"] = round(
            (prev_avg * (n - 1) + len(reply_text)) / n, 1
        )

        # Tone detection — formal vs casual ending counts
        formal_endings = len(re.findall(r"(?:합니다|입니다|있습니다|드립니다)\.", reply_text))
        casual_endings = len(re.findall(r"(?:해요|예요|이에요|네요|죠)\.", reply_text))
        affinity["formal_count"] = int(affinity.get("formal_count", 0)) + formal_endings
        affinity["casual_count"] = int(affinity.get("casual_count", 0)) + casual_endings

        # Determine preferred tone
        if affinity["formal_count"] + affinity["casual_count"] > 0:
            ratio = affinity["formal_count"] / (
                affinity["formal_count"] + affinity["casual_count"]
            )
            profile.preferred_tone = "formal" if ratio > 0.6 else (
                "casual" if ratio < 0.4 else "neutral"
            )

        # Preferred length
        avg = affinity["avg_reply_chars"]
        profile.preferred_length = "terse" if avg < 60 else ("detailed" if avg > 200 else "normal")

        profile.topic_affinity = affinity
        profile.interaction_count = (profile.interaction_count or 0) + 1
        db.commit()
    except Exception as e:
        log.warning(f"boss_observer: style profile error: {e}")


# ============================================================================
#  Style injection — used by chatbot_reply_service to make bot sound like boss
# ============================================================================

def build_style_hint(db: Session, agent_id: str) -> str:
    """Returns a short system-prompt fragment describing boss's style.
    Injected into the LLM call so autonomous replies imitate the boss.

    Returns "" if there's not enough data yet (cold start).
    """
    try:
        from db.models import ChatbotUserProfile
        profile = (
            db.query(ChatbotUserProfile)
            .filter(
                ChatbotUserProfile.agent_id == agent_id,
                ChatbotUserProfile.user_id.is_(None),
            )
            .first()
        )
        if not profile or not profile.topic_affinity:
            return ""
        affinity = profile.topic_affinity
        count = int(affinity.get("boss_reply_count", 0))
        if count < 5:
            return ""    # not enough samples yet
        avg_chars = affinity.get("avg_reply_chars", 100)
        tone = profile.preferred_tone or "neutral"
        length = profile.preferred_length or "normal"

        fragments = [
            "## 사장님 스타일 가이드 (학습된 패턴)",
            f"- 평균 답변 길이: {avg_chars}자 ({length})",
            f"- 톤: {tone}",
        ]
        if tone == "formal":
            fragments.append("- 사장님은 '~합니다', '~드립니다' 같은 정중한 어미를 선호합니다.")
        elif tone == "casual":
            fragments.append("- 사장님은 '~해요', '~예요' 같은 친근한 어미를 선호합니다.")
        fragments.append(
            f"- 사장님이 평소 작성하는 길이와 톤을 최대한 모방하여 답변하세요. "
            f"지금까지 {count}건의 답변에서 학습한 패턴입니다."
        )
        return "\n".join(fragments)
    except Exception as e:
        log.warning(f"boss_observer: build_style_hint error: {e}")
        return ""
