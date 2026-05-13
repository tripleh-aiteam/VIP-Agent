"""
VIP AI Platform — Twin Meeting Scheduler (v3 redesign)
Boss says 'lets meet in 10 minutes' inside a group chat. We:
  1) parse the time + (optional) specific twin names from the text,
  2) create a Meeting with status='scheduled' + scheduled_at set,
  3) record the intent in the group chat as a system message,
  4) schedule a background job that flips the meeting to 'active' at the
     scheduled time and auto-joins the relevant twins (the twins are
     'waiting in the room' as soon as scheduled_at fires).

The background job runs via the project's existing APScheduler instance
(services.scheduler_service). If no scheduler is wired up at runtime we
fall back to firing immediately for short-horizon (<60s) meetings so the
local test still works.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from db.base import SessionLocal
from db.models import (
    Meeting, MeetingParticipant, TwinGroup, TwinGroupMember, DigitalTwin,
    PlatformUser,
)
from services import twin_meeting_session, twin_group_service
from services.logger import log
from services.time_parser import parse_meeting_time
from services.twin_meeting_intent import extract_twin_names, find_twins_by_names


def schedule_meeting_from_text(
    db: Session,
    group_id: Optional[UUID],
    text: str,
    boss_user_id: Optional[UUID] = None,
    authority: str = "answer_factual",
    twins_only: bool = False,
) -> dict:
    """Parse a boss's chat message. If it contains both a meeting intent
    AND a time, create a scheduled Meeting and queue auto-join. If only
    intent, schedule "now". If neither, return ok=False.
    """
    from services import twin_meeting_intent
    if not twin_meeting_intent.detect_meeting_intent(text):
        return {"ok": False, "reason": "no_meeting_intent", "text": text}

    time_info = parse_meeting_time(text)
    if not time_info:
        # No explicit time — treat as "now"
        time_info = {
            "scheduled_at_utc": datetime.utcnow(),
            "kind": "now",
            "matched": "(implicit now)",
            "delta_seconds": 0,
            "human": "right now",
        }

    scheduled_at: datetime = time_info["scheduled_at_utc"]

    # Decide which twins should attend:
    #   1. If specific names mentioned -> only those twins
    #   2. Else if group_id provided -> all group members' twins
    #   3. Else -> empty (boss can add manually)
    candidate_names = extract_twin_names(text)
    twin_ids: list[UUID] = []
    twin_label_set: set[str] = set()

    if candidate_names:
        matched, unmatched = find_twins_by_names(db, candidate_names)
        twin_ids = [t.id for t in matched]
        twin_label_set = {t.name for t in matched}
        unmatched_label = unmatched
    else:
        unmatched_label = []
        if group_id:
            twin_ids = twin_group_service.list_member_twin_ids(db, group_id)
            for tid in twin_ids:
                t = db.query(DigitalTwin).filter(DigitalTwin.id == tid).first()
                if t:
                    twin_label_set.add(t.name)

    # Twins-only (off-day): twins get full_proxy authority since no human is around
    effective_authority = "full_proxy" if twins_only else authority

    # Create the Meeting row in 'scheduled' state (will flip to 'active' at scheduled_at)
    fire_now = time_info["delta_seconds"] <= 30
    prefix = "Twins-only " if twins_only else "Group "
    title = (
        f"{prefix}meeting — {', '.join(sorted(twin_label_set))}"
        if twin_label_set else f"{prefix}meeting"
    )
    meeting = Meeting(
        title=title[:200],
        meeting_type="all_hands",
        status="active" if fire_now else "scheduled",
        scheduled_at=scheduled_at,
        started_at=datetime.utcnow() if fire_now else None,
        created_by="vip_chat",
        is_voice=True,
    )
    db.add(meeting)
    db.flush()

    # If firing now, join twins immediately. If scheduled in the future,
    # pre-attach 'invited' participant rows so the autojoin dispatcher
    # (twin_meeting_autojoin) can flip them to active at scheduled_at —
    # this survives uvicorn restarts (unlike in-memory APScheduler `date`
    # triggers).
    joined: list[dict] = []
    if fire_now:
        joined = _join_twins(db, meeting.id, twin_ids, effective_authority, boss_user_id)
    else:
        _attach_invited(db, meeting.id, twin_ids, effective_authority, boss_user_id)
        # Also queue the APScheduler fire-at-time as a faster path. The
        # autojoin poller is the safety net.
        _enqueue_auto_join(meeting.id, twin_ids, effective_authority, boss_user_id, scheduled_at)

    db.commit()

    return {
        "ok": True,
        "meeting_id": str(meeting.id),
        "meeting_title": meeting.title,
        "meeting_status": meeting.status,
        "scheduled_at": scheduled_at.isoformat(),
        "scheduled_at_human": time_info.get("human"),
        "twin_ids": [str(t) for t in twin_ids],
        "twin_names": sorted(twin_label_set),
        "unmatched_names": unmatched_label,
        "fired_immediately": fire_now,
        "joined": joined,
        "twins_only": twins_only,
        "authority": effective_authority,
        "meeting_room_url": f"/meetings/{meeting.id}/room",
        "time_parse": {
            "kind": time_info["kind"],
            "matched": time_info["matched"],
            "delta_seconds": time_info["delta_seconds"],
        },
    }


def _attach_invited(
    db: Session,
    meeting_id: UUID,
    twin_ids: list[UUID],
    authority: str,
    boss_user_id: Optional[UUID],
) -> None:
    """Pre-create MeetingParticipant rows in 'invited' state so the
    autojoin dispatcher can find + activate them later, even after a
    server restart. Twin status is NOT changed to in_meeting yet — that
    happens at scheduled_at when the dispatcher flips them to active.
    """
    for tid in twin_ids:
        owner = (
            db.query(PlatformUser)
            .filter(PlatformUser.twin_id == tid)
            .first()
        )
        for_user_id = owner.id if owner else None
        participant = MeetingParticipant(
            meeting_id=meeting_id,
            twin_id=tid,
            participant_type="twin_proxy" if for_user_id else "twin",
            for_user_id=for_user_id,
            meeting_authority=authority,
            authorized_by_user_id=boss_user_id,
            authorized_at=datetime.utcnow() if boss_user_id else None,
            session_status="invited",
            joined_at=datetime.utcnow(),  # invite time; flipped to actual join later
        )
        db.add(participant)
    db.flush()


def _join_twins(
    db: Session,
    meeting_id: UUID,
    twin_ids: list[UUID],
    authority: str,
    boss_user_id: Optional[UUID],
) -> list[dict]:
    out = []
    for tid in twin_ids:
        try:
            owner = (
                db.query(PlatformUser)
                .filter(PlatformUser.twin_id == tid)
                .first()
            )
            for_user_id = owner.id if owner else None
            p = twin_meeting_session.join_meeting(
                db, twin_id=tid, meeting_id=meeting_id,
                for_user_id=for_user_id, authority=authority,
                authorized_by_user_id=boss_user_id,
                reason="auto-join from chat schedule",
            )
            out.append({"twin_id": str(tid), "participant_id": str(p.id)})
        except Exception as e:
            log.warning(f"twin_meeting_scheduler: join {tid} failed: {e}")
            out.append({"twin_id": str(tid), "error": str(e)})
    return out


# ---------------------------------------------------------------------------
#  Background scheduling — fires at scheduled_at
# ---------------------------------------------------------------------------

def _enqueue_auto_join(
    meeting_id: UUID,
    twin_ids: list[UUID],
    authority: str,
    boss_user_id: Optional[UUID],
    scheduled_at: datetime,
) -> None:
    """Try APScheduler first; if that fails, fall back to an asyncio task
    so local-dev still works without the scheduler service.
    """
    try:
        from services.scheduler_service import _scheduler  # type: ignore
        if _scheduler is not None:
            _scheduler.add_job(
                _run_auto_join_sync,
                "date",
                run_date=scheduled_at,
                args=[str(meeting_id), [str(t) for t in twin_ids], authority,
                      str(boss_user_id) if boss_user_id else None],
                id=f"meeting-{meeting_id}",
                replace_existing=True,
            )
            log.info(f"twin_meeting_scheduler: queued meeting {meeting_id} via APScheduler at {scheduled_at}")
            return
    except Exception as e:
        log.warning(f"twin_meeting_scheduler: APScheduler unavailable, using asyncio fallback: {e}")

    # Fallback: asyncio.sleep until scheduled_at, then fire
    try:
        loop = asyncio.get_event_loop()
        delay = max(0.0, (scheduled_at - datetime.utcnow()).total_seconds())
        loop.create_task(_run_auto_join_async(meeting_id, twin_ids, authority, boss_user_id, delay))
    except Exception as e:
        log.warning(f"twin_meeting_scheduler: asyncio fallback failed: {e}")


async def _run_auto_join_async(
    meeting_id: UUID, twin_ids: list[UUID], authority: str,
    boss_user_id: Optional[UUID], delay: float,
) -> None:
    await asyncio.sleep(delay)
    _run_auto_join_sync(
        str(meeting_id),
        [str(t) for t in twin_ids],
        authority,
        str(boss_user_id) if boss_user_id else None,
    )


def _run_auto_join_sync(
    meeting_id_str: str, twin_id_strs: list[str], authority: str,
    boss_user_id_str: Optional[str],
) -> None:
    """Top-level callable usable by APScheduler. Opens its own DB session."""
    db = SessionLocal()
    try:
        meeting_id = UUID(meeting_id_str)
        twin_ids = [UUID(t) for t in twin_id_strs]
        boss_user_id = UUID(boss_user_id_str) if boss_user_id_str else None

        meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
        if not meeting:
            log.warning(f"twin_meeting_scheduler: meeting {meeting_id} disappeared before fire")
            return
        if meeting.status == "ended":
            log.info(f"twin_meeting_scheduler: meeting {meeting_id} already ended, skipping fire")
            return

        meeting.status = "active"
        meeting.started_at = datetime.utcnow()
        _join_twins(db, meeting_id, twin_ids, authority, boss_user_id)
        db.commit()
        log.info(f"twin_meeting_scheduler: fired meeting {meeting_id}; joined {len(twin_ids)} twins")
    except Exception as e:
        log.warning(f"twin_meeting_scheduler: auto-join sync failed: {e}")
        db.rollback()
    finally:
        db.close()
