"""
VIP AI Platform — Twin Meeting Intent (Sprint 8)
Parses boss commands like 'let's have a meeting with [twin names]' or
'회의하자 김현성 트윈과 다브론벡' from the assistant page (text OR voice STT
output) and auto-creates a Meeting with those twins joined.

Bilingual (KR + EN) intent detection + name extraction. Fuzzy-matches
parsed names against the DigitalTwin registry so the boss doesn't have
to type the exact registered name.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from db.models import DigitalTwin, Meeting, MeetingParticipant, PlatformUser
from services import twin_meeting_session
from services.logger import log


# ---------------------------------------------------------------------------
#  Intent detection
# ---------------------------------------------------------------------------

# English meeting verbs that should fire auto-create
_EN_MEETING_TRIGGERS = [
    r"\blet'?s\s+(?:have\s+|do\s+|start\s+|hold\s+)?(?:a\s+)?meeting\b",
    r"\b(?:start|create|open|begin)\s+(?:a\s+)?meeting\b",
    r"\bmeeting\s+with\b",
    r"\bcall\s+(?:a\s+|an\s+)?meeting\b",
    r"\bbring\s+(?:in\s+)?\w+\s+(?:to\s+a\s+|for\s+a\s+)?meeting\b",
    r"\bmeet\s+with\s+(?!me\b)\w+",   # "meet with X" but not "meet with me"
]

# Korean meeting triggers
_KR_MEETING_TRIGGERS = [
    r"회의하자",        # "let's meet"
    r"회의\s*시작",     # "start meeting"
    r"회의를?\s*열어",  # "open meeting"
    r"미팅하자",
    r"미팅\s*시작",
    r"\w+(?:와|과|랑|이랑|하고)\s+(?:같이\s+)?(?:회의|미팅)",  # "with X (have a) meeting"
    r"(?:회의|미팅)\s*\w+(?:와|과|랑)",
    r"트윈들?\s*(?:불러|소집)",   # "summon the twins"
]

_TRIGGERS = re.compile("|".join(_EN_MEETING_TRIGGERS + _KR_MEETING_TRIGGERS), re.IGNORECASE)


def detect_meeting_intent(text: str) -> bool:
    """True if the text expresses 'start a meeting' intent (KR or EN)."""
    if not text:
        return False
    return bool(_TRIGGERS.search(text))


# ---------------------------------------------------------------------------
#  Name extraction
# ---------------------------------------------------------------------------

# Pattern for extracting names AFTER trigger words. Captures sequences of
# capitalized English words OR Korean Hangul runs.
_NAME_AFTER_WITH_EN = re.compile(
    r"(?:with|and|,|&|plus|together with)\s+"
    r"([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,2}|[가-힣]{2,6})",
)

# Korean: NAME (와|과|랑|이랑|하고) — name comes BEFORE the connector
_NAME_BEFORE_KR = re.compile(r"([가-힣]{2,6})\s*(?:와|과|랑|이랑|하고|및)\s*")

# Korean: a bare list of Hangul names separated by spaces near the trigger
_HANGUL_NAME = re.compile(r"([가-힣]{2,6})(?:\s*트윈)?")


def extract_twin_names(text: str) -> list[str]:
    """Return candidate twin names mentioned in the text. May include
    duplicates and false positives — pass through find_twins_by_names for
    fuzzy matching against the registry.
    """
    if not text:
        return []
    names: list[str] = []

    # English: "...with NAME and NAME..."
    for m in _NAME_AFTER_WITH_EN.finditer(text):
        candidate = m.group(1).strip()
        if candidate.lower() not in {"me", "team", "everyone", "all"}:
            names.append(candidate)

    # Korean: NAME 와/과/랑 ...
    for m in _NAME_BEFORE_KR.finditer(text):
        names.append(m.group(1).strip())

    # Korean fallback: any Hangul name near 트윈 keyword
    if "트윈" in text or "회의" in text or "미팅" in text:
        for m in _HANGUL_NAME.finditer(text):
            cand = m.group(1).strip()
            # Filter out the verb/noun stems themselves
            if cand not in {"회의", "미팅", "트윈", "트윈들", "지금"}:
                names.append(cand)

    # Dedupe preserving order
    seen = set()
    unique = []
    for n in names:
        k = n.lower()
        if k not in seen:
            seen.add(k)
            unique.append(n)
    return unique


# ---------------------------------------------------------------------------
#  Twin registry lookup (fuzzy)
# ---------------------------------------------------------------------------

def find_twins_by_names(db: Session, names: list[str]) -> tuple[list[DigitalTwin], list[str]]:
    """Match each candidate name to a DigitalTwin. Returns (matched, unmatched).
    Match strategy:
      1. case-insensitive exact name match
      2. substring match (e.g. "Kim" → "김현성" via PlatformUser.name)
      3. Hangul substring match against twin.name
    """
    if not names:
        return [], []

    all_twins = db.query(DigitalTwin).all()
    if not all_twins:
        return [], names

    matched: list[DigitalTwin] = []
    matched_ids: set[str] = set()
    unmatched: list[str] = []

    for raw in names:
        candidate = raw.strip().lower()
        if not candidate:
            continue
        hit: Optional[DigitalTwin] = None

        # Pass 1: exact case-insensitive
        for t in all_twins:
            if t.name and t.name.lower() == candidate:
                hit = t
                break

        # Pass 2: substring against twin.name
        if not hit:
            for t in all_twins:
                if t.name and candidate in t.name.lower():
                    hit = t
                    break
                if t.name and t.name.lower() in candidate:
                    hit = t
                    break

        # Pass 3: linked PlatformUser.name (if name is the worker's real name not twin name)
        if not hit:
            user = (
                db.query(PlatformUser)
                .filter(func_lower(PlatformUser.name) == candidate)
                .first()
            )
            if user and user.twin_id:
                for t in all_twins:
                    if t.id == user.twin_id:
                        hit = t
                        break

        if hit and str(hit.id) not in matched_ids:
            matched.append(hit)
            matched_ids.add(str(hit.id))
        elif not hit:
            unmatched.append(raw)

    return matched, unmatched


def func_lower(col):
    """sqlalchemy.func.lower wrapper — late import to avoid circular."""
    from sqlalchemy import func
    return func.lower(col)


# ---------------------------------------------------------------------------
#  Orchestration: parse → match → create meeting → auto-join
# ---------------------------------------------------------------------------

def auto_create_meeting_from_text(
    db: Session,
    text: str,
    authorized_by_user_id: Optional[UUID] = None,
    authority: str = "answer_factual",
    meeting_type: str = "all_hands",
    title: Optional[str] = None,
) -> dict:
    """Top-level entry point used by /chat/voice and the new
    /twins/meetings/auto-create endpoint. Returns a result dict the UI
    can render and the voice handler can read back as TTS.
    """
    if not detect_meeting_intent(text):
        return {
            "ok": False,
            "reason": "no_meeting_intent",
            "message": "I didn't hear a meeting request — try 'Let's have a meeting with [Twin name]'.",
            "korean_message": "회의 요청이 감지되지 않았습니다. '김현성 트윈과 회의하자' 같은 식으로 말씀해주세요.",
        }

    candidate_names = extract_twin_names(text)
    matched, unmatched = find_twins_by_names(db, candidate_names)

    if not matched:
        return {
            "ok": False,
            "reason": "no_twins_matched",
            "candidates": candidate_names,
            "message": (
                f"I caught the meeting intent but couldn't find any twins matching "
                f"{candidate_names if candidate_names else '(no names detected)'}."
            ),
            "korean_message": (
                f"회의 의도는 감지했지만 일치하는 트윈을 찾지 못했습니다: "
                f"{candidate_names if candidate_names else '(이름 미감지)'}"
            ),
        }

    # Create the meeting row
    final_title = title or f"Auto: {', '.join(t.name for t in matched)} ({datetime.utcnow().strftime('%H:%M')})"
    meeting = Meeting(
        title=final_title,
        meeting_type=meeting_type,
        status="active",
        started_at=datetime.utcnow(),
        created_by="vip_assistant",
        is_voice=True,
    )
    db.add(meeting)
    db.flush()

    # Auto-join each matched twin
    joined: list[dict] = []
    skipped: list[dict] = []
    for twin in matched:
        try:
            owner = (
                db.query(PlatformUser)
                .filter(PlatformUser.twin_id == twin.id)
                .first()
            )
            for_user_id = owner.id if owner else None
            participant = twin_meeting_session.join_meeting(
                db,
                twin_id=twin.id,
                meeting_id=meeting.id,
                for_user_id=for_user_id,
                authority=authority,
                authorized_by_user_id=authorized_by_user_id,
                reason="auto-join from assistant",
            )
            joined.append({
                "twin_id": str(twin.id),
                "twin_name": twin.name,
                "participant_id": str(participant.id),
            })
        except Exception as e:
            log.warning(f"twin_meeting_intent: failed to join {twin.name}: {e}")
            skipped.append({"twin_id": str(twin.id), "twin_name": twin.name, "reason": str(e)})

    db.commit()

    summary_en = (
        f"Meeting started. Joined {len(joined)} twin(s): "
        f"{', '.join(j['twin_name'] for j in joined)}"
    )
    if skipped:
        summary_en += f" — skipped {len(skipped)}"
    summary_kr = (
        f"회의를 시작했습니다. {len(joined)}명의 트윈이 입장했습니다: "
        f"{', '.join(j['twin_name'] for j in joined)}"
    )

    return {
        "ok": True,
        "meeting_id": str(meeting.id),
        "meeting_title": meeting.title,
        "meeting_room_url": f"/meetings/{meeting.id}/room",
        "joined": joined,
        "skipped": skipped,
        "unmatched_names": unmatched,
        "message": summary_en,
        "korean_message": summary_kr,
    }
