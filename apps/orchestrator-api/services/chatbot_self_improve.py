"""
Chatbot SELF-IMPROVEMENT pillar — the chatbot learns from every interaction.

Phase 1 (foundation):
  - log_interaction()      : record every /chatbot/talk call
  - detect_correction()    : did the user just say "no, wrong, you got it wrong"?
  - record_correction()    : store the correction so it's applied next time
  - load_auto_examples()   : pull learned phrasings to boost intent matching
  - register_auto_example(): when LLM matches a new phrasing, persist it

Phase 2 (personalization):
  - get_or_create_profile(): per-user preference profile
  - update_topic_affinity(): track which topics this user asks about most
  - infer_length_pref()    : detect "tldr" / "shorter" signals

Phase 3 (advanced):
  - score_response()       : LLM grades its own reply
  - cluster_failures()     : run by self-improve cron, finds gaps to fill
  - suggest_new_intents()  : proposes missing intents based on fallbacks
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from typing import Any, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from services.logger import log


# ---------------------------------------------------------------------------
# Phase 1.2 — Interaction logging
# ---------------------------------------------------------------------------

def log_interaction(
    db: Session,
    *,
    agent_id: str,
    query: str,
    language: str,
    intent: Optional[str],
    source: Optional[str],
    reply: str,
    action_type: Optional[str],
    latency_ms: int,
    user_id: Optional[Any] = None,
) -> Optional[Any]:
    """Persist a single /chatbot/talk turn. Returns the interaction id."""
    try:
        from db.models import ChatbotInteraction
        row = ChatbotInteraction(
            agent_id=agent_id,
            user_id=_coerce_uuid(user_id),
            query=query[:2000],
            language=language[:8] if language else "auto",
            intent=intent[:80] if intent else None,
            source=source[:40] if source else None,
            reply=(reply or "")[:4000],
            action_type=action_type[:40] if action_type else None,
            latency_ms=int(latency_ms),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id
    except Exception as e:
        log.warning(f"chatbot.self_improve.log_interaction failed: {e}")
        try: db.rollback()
        except: pass
        return None


def _coerce_uuid(v: Any) -> Optional[UUID]:
    if v is None:
        return None
    if isinstance(v, UUID):
        return v
    try:
        return UUID(str(v))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Phase 1.3 — Correction detection
# ---------------------------------------------------------------------------

CORRECTION_PATTERNS_EN = [
    r"\bno+,?\s+(that'?s?\s+(not|wrong|incorrect)|you('?re|\s+are)\s+wrong)\b",
    r"\b(that'?s?\s+(not|wrong|incorrect))\b",
    r"\b(you (got|have got|have) (it|that) wrong)\b",
    r"\b(wrong|incorrect)\b.*\b(answer|reply|response)\b",
    r"\b(i\s+meant|actually,?\s+i\s+meant)\b",
    r"\b(no,?\s+i\s+meant)\b",
    r"\b(not\s+(that|this),?\s+i\s+meant)\b",
    r"^no\s*[,.!]",
    r"^wrong[\s.,!]?",
]
CORRECTION_PATTERNS_KO = [
    r"아니야", r"아니에요", r"틀렸", r"잘못", r"내 말은", r"그게 아니라",
]


def detect_correction(query: str) -> bool:
    """Return True if the user's message looks like a correction of the previous reply."""
    if not query:
        return False
    q = query.lower().strip()
    if len(q) > 200:  # corrections are usually short
        return False
    if any(re.search(p, q) for p in CORRECTION_PATTERNS_EN):
        return True
    if any(p in q for p in CORRECTION_PATTERNS_KO):
        return True
    return False


def record_correction(
    db: Session,
    *,
    agent_id: str,
    user_id: Optional[Any],
    original_query: str,
    wrong_intent: Optional[str],
    correction_text: str,
    last_interaction_id: Optional[Any] = None,
) -> Optional[Any]:
    """
    Record an explicit correction. Mark the previous interaction as `was_corrected`.
    The `correct_intent` is filled in later when we figure out what the user really wanted.
    """
    try:
        from db.models import ChatbotCorrection, ChatbotInteraction
        c = ChatbotCorrection(
            agent_id=agent_id,
            user_id=_coerce_uuid(user_id),
            original_query=original_query[:2000],
            wrong_intent=wrong_intent[:80] if wrong_intent else None,
            correction_text=correction_text[:1000],
            applied=False,
        )
        db.add(c)
        db.commit()
        db.refresh(c)

        # Mark the previous interaction
        if last_interaction_id:
            db.query(ChatbotInteraction).filter(
                ChatbotInteraction.id == _coerce_uuid(last_interaction_id)
            ).update({"was_corrected": True, "correction_id": c.id})
            db.commit()
        log.info(f"chatbot.self_improve recorded correction: {original_query[:40]} (was {wrong_intent})",
                 extra={"action": "chatbot.correction.recorded"})
        return c.id
    except Exception as e:
        log.warning(f"chatbot.self_improve.record_correction failed: {e}")
        try: db.rollback()
        except: pass
        return None


# ---------------------------------------------------------------------------
# Phase 1.4 + 1.5 — Auto-vocabulary expansion
# ---------------------------------------------------------------------------

def register_auto_example(
    db: Session,
    *,
    agent_id: str,
    intent: str,
    example_text: str,
    source: str = "auto_vocab",
    confidence: float = 0.7,
) -> bool:
    """When the LLM successfully classifies a new phrasing, add it to the agent's
    intent examples so future fast-path keyword matches catch it instantly."""
    try:
        from db.models import ChatbotAutoExample
        text = (example_text or "").strip()
        if len(text) < 4 or len(text) > 200:
            return False
        # Dedup by (agent_id, intent, lowercased text)
        existing = db.query(ChatbotAutoExample).filter(
            ChatbotAutoExample.agent_id == agent_id,
            ChatbotAutoExample.intent == intent,
        ).all()
        if any((e.example_text or "").lower() == text.lower() for e in existing):
            return False
        # Cap auto-examples per intent so they don't grow unbounded
        if len(existing) >= 30:
            return False
        ae = ChatbotAutoExample(
            agent_id=agent_id, intent=intent, example_text=text,
            source=source, confidence=float(confidence),
        )
        db.add(ae)
        db.commit()
        log.info(f"chatbot.self_improve auto-vocab added: '{text[:60]}' → {intent}",
                 extra={"action": "chatbot.auto_vocab.added"})
        return True
    except Exception as e:
        log.warning(f"chatbot.self_improve.register_auto_example failed: {e}")
        try: db.rollback()
        except: pass
        return False


def load_auto_examples(db: Session, agent_id: str) -> dict[str, list[str]]:
    """Return { intent_name: [auto-learned phrasings] } for fast-path matching."""
    try:
        from db.models import ChatbotAutoExample
        rows = db.query(ChatbotAutoExample).filter(
            ChatbotAutoExample.agent_id == agent_id,
        ).all()
        out: dict[str, list[str]] = {}
        for r in rows:
            out.setdefault(r.intent, []).append(r.example_text)
        return out
    except Exception:
        return {}


def bump_auto_example_use(db: Session, agent_id: str, intent: str, matched_text: str) -> None:
    """Increment use_count when an auto-example helps make a match."""
    try:
        from db.models import ChatbotAutoExample
        row = db.query(ChatbotAutoExample).filter(
            ChatbotAutoExample.agent_id == agent_id,
            ChatbotAutoExample.intent == intent,
            ChatbotAutoExample.example_text == matched_text,
        ).first()
        if row:
            row.use_count = (row.use_count or 0) + 1
            db.commit()
    except Exception:
        try: db.rollback()
        except: pass


# ---------------------------------------------------------------------------
# Phase 1.6 — Health metrics
# ---------------------------------------------------------------------------

def health_dashboard(db: Session, agent_id: Optional[str] = None, hours: int = 168) -> dict[str, Any]:
    """
    Return chatbot performance metrics for the last N hours (default 1 week).
    Used by GET /chatbot/health to power the self-improvement dashboard.
    """
    try:
        from db.models import ChatbotInteraction, ChatbotCorrection, ChatbotAutoExample
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        q = db.query(ChatbotInteraction).filter(ChatbotInteraction.created_at >= cutoff)
        if agent_id:
            q = q.filter(ChatbotInteraction.agent_id == agent_id)
        rows = q.all()
        total = len(rows)

        if total == 0:
            return {"total_interactions": 0, "agent_id": agent_id, "hours_back": hours}

        matched = sum(1 for r in rows if r.intent and r.intent != "fallback" and r.source != "fallback")
        fallback = sum(1 for r in rows if r.source == "fallback" or r.intent == "fallback")
        corrected = sum(1 for r in rows if r.was_corrected)
        avg_latency = round(sum((r.latency_ms or 0) for r in rows) / total, 1)
        accuracy = round(100.0 * matched / total, 1) if total else 0.0
        fallback_pct = round(100.0 * fallback / total, 1) if total else 0.0

        # Intent distribution
        by_intent: dict[str, int] = {}
        for r in rows:
            key = r.intent or "fallback"
            by_intent[key] = by_intent.get(key, 0) + 1
        top_intents = sorted(by_intent.items(), key=lambda x: -x[1])[:10]

        # Source distribution (keyword vs llm vs workflow)
        by_source: dict[str, int] = {}
        for r in rows:
            key = r.source or "unknown"
            by_source[key] = by_source.get(key, 0) + 1

        # Top fallback queries (the most common things we couldn't answer)
        fallback_queries: dict[str, int] = {}
        for r in rows:
            if r.source == "fallback" or r.intent == "fallback":
                k = (r.query or "")[:80]
                fallback_queries[k] = fallback_queries.get(k, 0) + 1
        top_fallbacks = sorted(fallback_queries.items(), key=lambda x: -x[1])[:10]

        # Corrections + auto-examples
        corrections = db.query(ChatbotCorrection).filter(ChatbotCorrection.created_at >= cutoff)
        if agent_id:
            corrections = corrections.filter(ChatbotCorrection.agent_id == agent_id)
        correction_count = corrections.count()

        auto_q = db.query(ChatbotAutoExample)
        if agent_id:
            auto_q = auto_q.filter(ChatbotAutoExample.agent_id == agent_id)
        auto_count = auto_q.count()

        return {
            "agent_id": agent_id,
            "hours_back": hours,
            "total_interactions": total,
            "matched": matched,
            "fallback": fallback,
            "corrected": corrected,
            "accuracy_pct": accuracy,
            "fallback_pct": fallback_pct,
            "avg_latency_ms": avg_latency,
            "top_intents": [{"intent": k, "count": v} for k, v in top_intents],
            "by_source": by_source,
            "top_fallback_queries": [{"query": k, "count": v} for k, v in top_fallbacks],
            "total_corrections": correction_count,
            "total_auto_examples": auto_count,
            "as_of": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        log.warning(f"chatbot.self_improve health_dashboard failed: {e}")
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Phase 2 — Per-user profile + topic affinity
# ---------------------------------------------------------------------------

def get_or_create_profile(db: Session, agent_id: str, user_id: Optional[Any]) -> Optional[Any]:
    if not user_id:
        return None
    try:
        from db.models import ChatbotUserProfile
        uid = _coerce_uuid(user_id)
        p = db.query(ChatbotUserProfile).filter(
            ChatbotUserProfile.agent_id == agent_id,
            ChatbotUserProfile.user_id == uid,
        ).first()
        if p:
            return p
        p = ChatbotUserProfile(agent_id=agent_id, user_id=uid)
        db.add(p)
        db.commit()
        db.refresh(p)
        return p
    except Exception as e:
        log.warning(f"chatbot.self_improve get_profile failed: {e}")
        try: db.rollback()
        except: pass
        return None


def update_topic_affinity(db: Session, agent_id: str, user_id: Optional[Any], intent: str) -> None:
    """Track which intents/topics this user asks about most."""
    if not user_id or not intent:
        return
    try:
        p = get_or_create_profile(db, agent_id, user_id)
        if not p:
            return
        # Group intents into broad topics
        topic = _intent_to_topic(intent)
        affinity = dict(p.topic_affinity or {})
        affinity[topic] = affinity.get(topic, 0) + 1
        p.topic_affinity = affinity
        p.interaction_count = (p.interaction_count or 0) + 1
        db.commit()
    except Exception:
        try: db.rollback()
        except: pass


def _intent_to_topic(intent: str) -> str:
    """Group intents into broad topics for affinity tracking."""
    if not intent:
        return "other"
    if intent.startswith("nav_"):
        return "navigation"
    if intent.startswith("query_"):
        # query_stock → stock, query_asset → asset
        return intent[6:].split("_")[0]
    if intent.startswith("trigger_"):
        return "triggers"
    if intent.startswith("ui_"):
        return "ui"
    if intent in ("send_twin_message", "broadcast"):
        return "messaging"
    return "other"


def infer_length_pref(message: str) -> Optional[str]:
    """Detect length preference signals: 'tldr', 'shorter', 'be brief', '간단히'."""
    if not message:
        return None
    q = message.lower()
    if any(p in q for p in ["tldr", "tl;dr", "shorter", "be brief", "be concise",
                            "too long", "shorter please", "간단히", "짧게", "간략히"]):
        return "terse"
    if any(p in q for p in ["explain in detail", "tell me more", "elaborate", "longer",
                            "더 자세히", "자세히", "상세히"]):
        return "detailed"
    return None


# ---------------------------------------------------------------------------
# Phase 3 — Skill discovery (run by cron)
# ---------------------------------------------------------------------------

def maybe_apply_length_pref(db: Session, agent_id: str, query: str) -> Optional[str]:
    """
    Phase 2 — detect length-preference signals in the user's query AND
    save them on the agent-level profile (we use a null user_id for the
    'anonymous boss' since we don't yet have full auth on each request).
    Returns the detected preference if any.
    """
    pref = infer_length_pref(query)
    if not pref:
        return None
    try:
        from db.models import ChatbotUserProfile
        # Use null-user agent-wide profile as a fallback for unauthenticated requests
        p = db.query(ChatbotUserProfile).filter(
            ChatbotUserProfile.agent_id == agent_id,
            ChatbotUserProfile.user_id.is_(None),
        ).first()
        if not p:
            p = ChatbotUserProfile(agent_id=agent_id, user_id=None, preferred_length=pref)
            db.add(p)
        else:
            p.preferred_length = pref
        db.commit()
        log.info(f"chatbot.self_improve set length_pref={pref} for agent={agent_id}",
                 extra={"action": "chatbot.length_pref.set"})
        return pref
    except Exception as e:
        log.warning(f"chatbot.self_improve length_pref save failed: {e}")
        try: db.rollback()
        except: pass
        return None


def get_length_pref(db: Session, agent_id: str, user_id: Optional[Any] = None) -> str:
    """Return the user's preferred length: terse / normal / detailed."""
    try:
        from db.models import ChatbotUserProfile
        q = db.query(ChatbotUserProfile).filter(ChatbotUserProfile.agent_id == agent_id)
        if user_id:
            q = q.filter(ChatbotUserProfile.user_id == _coerce_uuid(user_id))
        else:
            q = q.filter(ChatbotUserProfile.user_id.is_(None))
        p = q.first()
        return (p.preferred_length if p else "normal") or "normal"
    except Exception:
        return "normal"


def get_topic_affinity(db: Session, agent_id: str, user_id: Optional[Any] = None) -> dict[str, int]:
    """Return {topic: count} — used to boost ambiguous intent picks."""
    try:
        from db.models import ChatbotUserProfile
        q = db.query(ChatbotUserProfile).filter(ChatbotUserProfile.agent_id == agent_id)
        if user_id:
            q = q.filter(ChatbotUserProfile.user_id == _coerce_uuid(user_id))
        else:
            q = q.filter(ChatbotUserProfile.user_id.is_(None))
        p = q.first()
        return dict(p.topic_affinity or {}) if p else {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Phase 2.3 — Auto-FAQ: repeated identical-ish questions become canned answers
# ---------------------------------------------------------------------------

def find_canned_reply(db: Session, agent_id: str, query: str, threshold: int = 3) -> Optional[str]:
    """
    If this exact query (lowercased, trimmed) has been asked >= threshold
    times AND the latest reply was not a fallback, return that latest reply.
    Saves an LLM call entirely.
    """
    try:
        from db.models import ChatbotInteraction
        q = (query or "").lower().strip()
        if len(q) < 6:
            return None
        rows = db.query(ChatbotInteraction).filter(
            ChatbotInteraction.agent_id == agent_id,
            ChatbotInteraction.intent.isnot(None),
            ChatbotInteraction.source != "fallback",
            ChatbotInteraction.was_corrected == False,
        ).order_by(ChatbotInteraction.created_at.desc()).limit(500).all()
        # Find rows with the same lowercased query
        matches = [r for r in rows if (r.query or "").lower().strip() == q]
        if len(matches) >= threshold and matches[0].reply:
            return matches[0].reply
    except Exception:
        pass
    return None


def apply_retention(db: Session, agent_id: str, days: int) -> int:
    """
    PRIVACY — delete chatbot_interactions rows older than `days` for an agent.
    Called from the daily retention cron when an agent's privacy.dropAfterDays
    is set. Returns the number of rows deleted.
    """
    if not days or days <= 0:
        return 0
    try:
        from db.models import ChatbotInteraction
        cutoff = datetime.utcnow() - timedelta(days=days)
        deleted = db.query(ChatbotInteraction).filter(
            ChatbotInteraction.agent_id == agent_id,
            ChatbotInteraction.created_at < cutoff,
        ).delete(synchronize_session=False)
        db.commit()
        if deleted:
            log.info(f"chatbot.privacy retention: deleted {deleted} rows for {agent_id} older than {days}d",
                     extra={"action": "chatbot.privacy.retention"})
        return deleted
    except Exception as e:
        log.warning(f"chatbot.privacy.apply_retention failed: {e}")
        try: db.rollback()
        except: pass
        return 0


def cluster_failures(db: Session, agent_id: str, hours: int = 168, min_count: int = 3) -> list[dict]:
    """Find queries that fall back repeatedly — likely missing intents."""
    try:
        from db.models import ChatbotInteraction
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        rows = db.query(ChatbotInteraction).filter(
            ChatbotInteraction.agent_id == agent_id,
            ChatbotInteraction.created_at >= cutoff,
            ChatbotInteraction.source == "fallback",
        ).all()
        # Naive clustering by lowercase normalized first 5 words
        clusters: dict[str, list[str]] = {}
        for r in rows:
            q = (r.query or "").lower().strip()
            words = re.findall(r"[\w]+", q)
            key = " ".join(words[:5])
            clusters.setdefault(key, []).append(r.query)
        out = []
        for key, queries in clusters.items():
            if len(queries) >= min_count:
                out.append({
                    "key": key,
                    "count": len(queries),
                    "samples": queries[:3],
                })
        return sorted(out, key=lambda x: -x["count"])[:20]
    except Exception as e:
        log.warning(f"chatbot.self_improve cluster_failures failed: {e}")
        return []
