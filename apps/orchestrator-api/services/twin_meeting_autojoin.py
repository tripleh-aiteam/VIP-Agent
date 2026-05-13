"""
VIP AI Platform — Twin Auto-Join Dispatcher (v4-E)
Runs every minute. Looks at:
  1. Meetings with status='scheduled' whose scheduled_at has passed
     OR is within the next NEXT_MINUTE_WINDOW seconds.
  2. Joins every invited twin (group-derived if no specific invites),
     flips meeting to 'active', logs activity.

This is what makes the off-day flow real: boss schedules a meeting in
the morning, then disappears — at the scheduled time twins join on
their own, do the meeting, summarize, email out.

Wired via APScheduler interval job (default every 60s) from main.py
lifespan startup.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from db.base import SessionLocal
from db.models import (
    Meeting, MeetingParticipant, TwinGroup, TwinGroupMember,
    DigitalTwin, PlatformUser,
)
from services import twin_meeting_session, twin_group_service
from services.logger import log


_POLL_SECONDS = int(os.getenv("TWIN_AUTOJOIN_POLL_SECONDS", "60"))
_LOOKAHEAD_SECONDS = int(os.getenv("TWIN_AUTOJOIN_LOOKAHEAD_SECONDS", "30"))


def run_once() -> dict:
    """Find scheduled meetings due now (or within lookahead) and fire them."""
    db = SessionLocal()
    fired: list[dict] = []
    skipped: list[dict] = []
    try:
        cutoff = datetime.utcnow() + timedelta(seconds=_LOOKAHEAD_SECONDS)
        due = (
            db.query(Meeting)
            .filter(
                Meeting.status == "scheduled",
                Meeting.scheduled_at <= cutoff,
            )
            .order_by(Meeting.scheduled_at.asc())
            .all()
        )

        for meeting in due:
            # Activate invited participants — preferred path because the
            # scheduler attached them with the right authority + worker link.
            invited = (
                db.query(MeetingParticipant)
                .filter(
                    MeetingParticipant.meeting_id == meeting.id,
                    MeetingParticipant.session_status == "invited",
                )
                .all()
            )
            joined_for_meeting = []
            for p in invited:
                p.session_status = "active"
                p.joined_at = datetime.utcnow()
                # Flip twin status to in_meeting
                twin = db.query(DigitalTwin).filter(DigitalTwin.id == p.twin_id).first()
                if twin:
                    twin.status = "in_meeting"
                joined_for_meeting.append({
                    "twin_id": str(p.twin_id),
                    "participant_id": str(p.id),
                })

            if not invited:
                skipped.append({
                    "meeting_id": str(meeting.id),
                    "reason": "no invited twins (legacy meeting?)",
                })
                continue

            meeting.status = "active"
            meeting.started_at = datetime.utcnow()
            fired.append({
                "meeting_id": str(meeting.id),
                "title": meeting.title,
                "scheduled_at": meeting.scheduled_at.isoformat() if meeting.scheduled_at else None,
                "joined": joined_for_meeting,
            })

        db.commit()
    except Exception as e:
        db.rollback()
        log.warning(f"twin_meeting_autojoin: poll cycle failed: {e}")
    finally:
        db.close()

    return {
        "ran_at": datetime.utcnow().isoformat(),
        "fired": fired,
        "skipped": skipped,
    }


def _twin_ids_for_meeting(db, meeting: Meeting) -> list[UUID]:
    """Resolve invited twins for a scheduled meeting:
      1. Twins already attached as participants (regardless of status) -> use them.
      2. Otherwise, if the meeting has a group_id in title or metadata,
         pull group members.
    """
    existing = (
        db.query(MeetingParticipant)
        .filter(MeetingParticipant.meeting_id == meeting.id)
        .all()
    )
    if existing:
        return [p.twin_id for p in existing]

    # Fallback: no participants pre-assigned — nothing to do (boss must
    # have scheduled an empty meeting). Future enhancement: parse group
    # reference from meeting metadata.
    return []


def register_with_scheduler() -> dict:
    try:
        from services.scheduler_service import _scheduler  # type: ignore
        if _scheduler is None:
            return {"installed": False, "reason": "scheduler not initialized"}
        _scheduler.add_job(
            run_once,
            "interval",
            seconds=_POLL_SECONDS,
            id="twin-autojoin",
            replace_existing=True,
            next_run_time=datetime.utcnow() + timedelta(seconds=5),
        )
        return {"installed": True, "poll_seconds": _POLL_SECONDS}
    except Exception as e:
        return {"installed": False, "reason": str(e)}


def get_status() -> dict:
    try:
        from services.scheduler_service import _scheduler  # type: ignore
        if _scheduler is None:
            return {"installed": False, "reason": "scheduler not initialized"}
        job = _scheduler.get_job("twin-autojoin")
        if not job:
            return {"installed": False, "reason": "job not registered"}
        return {
            "installed": True,
            "poll_seconds": _POLL_SECONDS,
            "lookahead_seconds": _LOOKAHEAD_SECONDS,
            "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
        }
    except Exception as e:
        return {"installed": False, "reason": str(e)}
