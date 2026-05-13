"""
VIP AI Platform — Twin Hand-raise (v3 redesign)
Real-life meeting affordance: when the boss asks a question in a hybrid
meeting room, twins that can answer 'raise their hand' (badge on tile).
Boss clicks a tile to grant the floor; that twin then speaks via the
twin_meeting_orchestrator.

Confidence scoring:
  - If boss names a specific twin in the question -> that twin gets 1.0
  - Else: lightweight keyword-overlap score between question and each
    twin's knowledge titles + skills. Twins above THRESHOLD raise hands.
  - (Optional future: replace with LLM self-scoring call)
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from db.models import (
    DigitalTwin, MeetingParticipant, MeetingHandRaise, TwinKnowledge,
)
from services import twin_meeting_session, twin_meeting_orchestrator
from services.logger import log


_RAISE_THRESHOLD = float(os.getenv("TWIN_HAND_RAISE_THRESHOLD", "0.18"))


# ---------------------------------------------------------------------------
#  Confidence eval
# ---------------------------------------------------------------------------

_STOPWORDS_EN = {"the", "a", "an", "is", "are", "was", "were", "do", "does",
                 "did", "to", "of", "in", "on", "for", "with", "and", "or",
                 "what", "when", "where", "why", "how", "who", "this", "that",
                 "can", "could", "would", "should", "anyone", "someone"}


def _tokenize(text: str) -> set[str]:
    if not text:
        return set()
    tokens = re.findall(r"[가-힣A-Za-z0-9]+", text.lower())
    return {t for t in tokens if len(t) > 1 and t not in _STOPWORDS_EN}


def score_twin(db: Session, twin_id: UUID, question: str) -> tuple[float, str]:
    """Lightweight confidence score for one twin's ability to answer.
    Returns (score 0-1, brief reasoning string).
    """
    twin = db.query(DigitalTwin).filter(DigitalTwin.id == twin_id).first()
    if not twin:
        return 0.0, "twin not found"

    qtokens = _tokenize(question)
    if not qtokens:
        return 0.0, "empty question"

    # Skills overlap (each skill weighted 2x)
    skill_tokens: set[str] = set()
    for s in (twin.skills or []):
        skill_tokens |= _tokenize(s)
    skill_hits = qtokens & skill_tokens

    # Role overlap (the twin's role/department often signals domain)
    role_tokens = _tokenize(f"{twin.role or ''} {twin.department or ''}")
    role_hits = qtokens & role_tokens

    # Knowledge overlap — sample top 20 knowledge titles/contents
    kn_tokens: set[str] = set()
    knowledge = (
        db.query(TwinKnowledge)
        .filter(TwinKnowledge.twin_id == twin_id)
        .order_by(TwinKnowledge.created_at.desc())
        .limit(20)
        .all()
    )
    for k in knowledge:
        kn_tokens |= _tokenize(k.title or "")
        kn_tokens |= _tokenize((k.content or "")[:400])
    knowledge_hits = qtokens & kn_tokens

    raw = len(skill_hits) * 2 + len(role_hits) * 1.5 + len(knowledge_hits)
    # Normalize against question length, capped
    score = min(1.0, raw / max(len(qtokens) * 1.5, 1))

    reasons = []
    if skill_hits:
        reasons.append(f"skills: {', '.join(sorted(skill_hits))}")
    if role_hits:
        reasons.append(f"role: {', '.join(sorted(role_hits))}")
    if knowledge_hits:
        reasons.append(f"knowledge: {', '.join(sorted(list(knowledge_hits)[:4]))}")
    reasoning = "; ".join(reasons) or "no strong match"
    return round(score, 3), reasoning


# ---------------------------------------------------------------------------
#  Named-twin extraction (if boss explicitly mentions a twin)
# ---------------------------------------------------------------------------

def find_named_twin(
    db: Session, question: str, meeting_id: UUID,
) -> Optional[MeetingParticipant]:
    """If the question explicitly names a twin who is in this meeting,
    return that participant — the boss is asking THEM specifically.
    """
    from services.twin_meeting_intent import extract_twin_names, find_twins_by_names
    names = extract_twin_names(question)
    if not names:
        return None
    matched, _ = find_twins_by_names(db, names)
    if not matched:
        return None
    matched_ids = {t.id for t in matched}
    return (
        db.query(MeetingParticipant)
        .filter(
            MeetingParticipant.meeting_id == meeting_id,
            MeetingParticipant.twin_id.in_(matched_ids),
            MeetingParticipant.session_status.in_(("active", "escalated")),
        )
        .first()
    )


# ---------------------------------------------------------------------------
#  Public API
# ---------------------------------------------------------------------------

def ask_in_meeting(
    db: Session,
    meeting_id: UUID,
    question: str,
    threshold: Optional[float] = None,
) -> dict:
    """Score every active twin in the meeting against the question. Twins
    above the threshold raise their hand (MeetingHandRaise row inserted).
    Returns the ranked list so the boss UI can render tile badges.

    If the question names a specific twin already present, that twin gets
    score=1.0 and is auto-granted-floor (no boss click needed).
    """
    thr = threshold if threshold is not None else _RAISE_THRESHOLD

    # Named twin shortcut
    named = find_named_twin(db, question, meeting_id)
    auto_granted_participant_id = None

    participants = (
        db.query(MeetingParticipant)
        .filter(
            MeetingParticipant.meeting_id == meeting_id,
            MeetingParticipant.session_status.in_(("active", "escalated")),
        )
        .all()
    )
    if not participants:
        return {"ok": False, "reason": "no_active_twins"}

    # Lower any pre-existing raised hands for previous questions
    db.query(MeetingHandRaise).filter(
        MeetingHandRaise.meeting_id == meeting_id,
        MeetingHandRaise.status == "raised",
    ).update({
        MeetingHandRaise.status: "lowered",
        MeetingHandRaise.lowered_at: datetime.utcnow(),
    }, synchronize_session=False)

    raises: list[dict] = []
    for p in participants:
        if named and p.id == named.id:
            score, reasoning = 1.0, "boss named this twin directly"
        else:
            score, reasoning = score_twin(db, p.twin_id, question)

        if score >= thr:
            row = MeetingHandRaise(
                meeting_id=meeting_id,
                participant_id=p.id,
                twin_id=p.twin_id,
                question_text=question,
                confidence_score=score,
                reasoning=reasoning,
                status="raised",
                raised_at=datetime.utcnow(),
            )
            db.add(row)
            db.flush()
            raises.append({
                "raise_id": str(row.id),
                "participant_id": str(p.id),
                "twin_id": str(p.twin_id),
                "confidence": score,
                "reasoning": reasoning,
                "auto_granted": named is not None and p.id == named.id,
            })
            if named and p.id == named.id:
                auto_granted_participant_id = p.id

    db.commit()
    raises.sort(key=lambda r: r["confidence"], reverse=True)

    return {
        "ok": True,
        "meeting_id": str(meeting_id),
        "question": question,
        "threshold": thr,
        "hands": raises,
        "auto_grant_participant_id": str(auto_granted_participant_id) if auto_granted_participant_id else None,
    }


async def grant_floor(
    db: Session,
    meeting_id: UUID,
    raise_id: UUID,
    model: Optional[str] = None,
) -> dict:
    """Boss grants speak permission to the chosen twin. The orchestrator
    generates a spoken reply, returns audio_url + text.
    """
    row = (
        db.query(MeetingHandRaise)
        .filter(
            MeetingHandRaise.id == raise_id,
            MeetingHandRaise.meeting_id == meeting_id,
        )
        .first()
    )
    if not row:
        raise ValueError("Hand-raise not found")
    if row.status != "raised":
        raise ValueError(f"Hand-raise is in state '{row.status}', not 'raised'")

    row.status = "granted"
    row.granted_at = datetime.utcnow()

    # Lower all other raised hands for the same question
    db.query(MeetingHandRaise).filter(
        MeetingHandRaise.meeting_id == meeting_id,
        MeetingHandRaise.question_text == row.question_text,
        MeetingHandRaise.id != row.id,
        MeetingHandRaise.status == "raised",
    ).update({
        MeetingHandRaise.status: "lowered",
        MeetingHandRaise.lowered_at: datetime.utcnow(),
    }, synchronize_session=False)
    db.commit()

    # Trigger the twin to speak via the orchestrator
    result = await twin_meeting_orchestrator.respond_in_meeting(
        db,
        twin_id=row.twin_id,
        meeting_id=meeting_id,
        prompt=row.question_text,
        model=model,
        speak_aloud=True,
    )
    return {
        "raise_id": str(row.id),
        "twin_id": str(row.twin_id),
        "confidence": row.confidence_score,
        "reply": result,
    }


def list_hands(db: Session, meeting_id: UUID) -> list[dict]:
    rows = (
        db.query(MeetingHandRaise, DigitalTwin)
        .join(DigitalTwin, DigitalTwin.id == MeetingHandRaise.twin_id)
        .filter(MeetingHandRaise.meeting_id == meeting_id)
        .order_by(MeetingHandRaise.raised_at.desc())
        .limit(40)
        .all()
    )
    return [
        {
            "raise_id": str(r.id),
            "twin_id": str(r.twin_id),
            "twin_name": t.name,
            "twin_role": t.role,
            "question_text": r.question_text,
            "confidence_score": r.confidence_score,
            "reasoning": r.reasoning,
            "status": r.status,
            "raised_at": r.raised_at.isoformat() if r.raised_at else None,
            "granted_at": r.granted_at.isoformat() if r.granted_at else None,
        }
        for r, t in rows
    ]
