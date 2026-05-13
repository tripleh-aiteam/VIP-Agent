"""
VIP AI Platform — Twin Groups & v3 Meeting Router
Groups, schedule-meeting-from-chat, ask + hand-raise + grant-floor.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from db.base import get_db
from db.models import PlatformUser
from contracts.twin import (
    GroupCreate, GroupAddMember, GroupMessageSend,
    ScheduleMeetingFromChat, AskInMeetingRequest, GrantFloorRequest,
)

router = APIRouter(prefix="/groups", tags=["twin-groups"])


def _resolve_boss(db: Session, x_user_email: Optional[str]) -> Optional[UUID]:
    if not x_user_email:
        return None
    u = db.query(PlatformUser).filter(PlatformUser.email == x_user_email).first()
    return u.id if u else None


# ---------------------------------------------------------------------------
#  Groups
# ---------------------------------------------------------------------------

@router.post("", status_code=201)
def create_group(
    body: GroupCreate,
    x_user_email: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    from services import twin_group_service
    boss_id = _resolve_boss(db, x_user_email)
    g = twin_group_service.create_group(
        db, name=body.name, created_by_user_id=boss_id,
        description=body.description, avatar_color=body.avatar_color,
    )
    db.commit()
    return {"id": str(g.id), "name": g.name, "member_count": 0}


@router.get("")
def list_groups(db: Session = Depends(get_db)):
    from services import twin_group_service
    return twin_group_service.list_groups(db)


@router.get("/{group_id}")
def get_group(group_id: UUID, db: Session = Depends(get_db)):
    from services import twin_group_service
    g = twin_group_service.get_group(db, group_id)
    if not g:
        raise HTTPException(status_code=404, detail="Group not found")
    return g


@router.delete("/{group_id}")
def delete_group(group_id: UUID, db: Session = Depends(get_db)):
    from services import twin_group_service
    ok = twin_group_service.delete_group(db, group_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Group not found")
    db.commit()
    return {"deleted": True}


@router.post("/{group_id}/members", status_code=201)
def add_member(group_id: UUID, body: GroupAddMember, db: Session = Depends(get_db)):
    from services import twin_group_service
    try:
        result = twin_group_service.add_member(db, group_id, body.user_id, body.role)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return result


@router.delete("/{group_id}/members/{user_id}")
def remove_member(group_id: UUID, user_id: UUID, db: Session = Depends(get_db)):
    from services import twin_group_service
    ok = twin_group_service.remove_member(db, group_id, user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Member not found")
    db.commit()
    return {"removed": True}


# ---------------------------------------------------------------------------
#  Group chat
# ---------------------------------------------------------------------------

@router.get("/{group_id}/messages")
def list_group_messages(group_id: UUID, limit: int = 100, db: Session = Depends(get_db)):
    from services import twin_group_service
    return twin_group_service.list_messages(db, group_id, limit=limit)


@router.post("/{group_id}/messages", status_code=201)
def post_group_message(
    group_id: UUID,
    body: GroupMessageSend,
    x_user_email: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Post a message in a group thread. If the message is a meeting
    request, ALSO auto-schedule the meeting and append a system message
    with the result.
    """
    from services import twin_group_service, twin_meeting_intent, twin_meeting_scheduler
    boss_id = _resolve_boss(db, x_user_email)

    # Save the boss's literal message
    msg = twin_group_service.post_message(
        db, group_id, body.content,
        sender_type=body.sender_type,
        sender_user_id=boss_id if body.sender_type == "boss" else None,
        meta=None,
    )

    schedule_result = None
    if body.sender_type == "boss" and twin_meeting_intent.detect_meeting_intent(body.content):
        try:
            schedule_result = twin_meeting_scheduler.schedule_meeting_from_text(
                db, group_id=group_id, text=body.content, boss_user_id=boss_id,
                twins_only=body.twins_only,
            )
        except Exception as e:
            schedule_result = {"ok": False, "reason": f"scheduler_error: {e}"}
        if schedule_result and schedule_result.get("ok"):
            system_text = (
                f"🗓 Meeting scheduled {schedule_result['scheduled_at_human']}. "
                f"Twins: {', '.join(schedule_result['twin_names']) or '(none matched)'}. "
                f"Room: {schedule_result['meeting_room_url']}"
            )
            twin_group_service.post_message(
                db, group_id, system_text,
                sender_type="system",
                meta={
                    "meeting_id": schedule_result["meeting_id"],
                    "meeting_room_url": schedule_result["meeting_room_url"],
                    "scheduled_at": schedule_result["scheduled_at"],
                },
            )

    db.commit()
    return {
        "message_id": str(msg.id),
        "schedule_result": schedule_result,
    }


# ---------------------------------------------------------------------------
#  Schedule meeting from text (also exposed directly for non-group use)
# ---------------------------------------------------------------------------

@router.post("/_meetings/schedule")
def schedule_meeting(
    body: ScheduleMeetingFromChat,
    x_user_email: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    from services import twin_meeting_scheduler
    boss_id = _resolve_boss(db, x_user_email)
    return twin_meeting_scheduler.schedule_meeting_from_text(
        db, group_id=body.group_id, text=body.text, boss_user_id=boss_id,
        authority=body.authority.value, twins_only=body.twins_only,
    )


# ---------------------------------------------------------------------------
#  Hand-raise / Ask / Grant Floor (Zoom-style)
# ---------------------------------------------------------------------------

@router.post("/_meetings/{meeting_id}/ask")
def ask_in_meeting(
    meeting_id: UUID,
    body: AskInMeetingRequest,
    db: Session = Depends(get_db),
):
    from services import twin_meeting_handraise
    return twin_meeting_handraise.ask_in_meeting(
        db, meeting_id, body.question, body.threshold,
    )


@router.post("/_meetings/{meeting_id}/grant-floor")
async def grant_floor(
    meeting_id: UUID,
    body: GrantFloorRequest,
    db: Session = Depends(get_db),
):
    from services import twin_meeting_handraise
    try:
        return await twin_meeting_handraise.grant_floor(
            db, meeting_id, body.raise_id, body.model,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/_meetings/{meeting_id}/hands")
def list_hands(meeting_id: UUID, db: Session = Depends(get_db)):
    from services import twin_meeting_handraise
    return {"hands": twin_meeting_handraise.list_hands(db, meeting_id)}
