"""
VIP AI Platform — Twin Group Service (v3 redesign)
Boss creates groups of workers; each worker auto-includes their linked
twin so meeting commands in the group thread can find all twins.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from db.models import (
    TwinGroup, TwinGroupMember, TwinGroupMessage,
    PlatformUser, DigitalTwin,
)


def create_group(
    db: Session,
    name: str,
    created_by_user_id: Optional[UUID] = None,
    description: Optional[str] = None,
    avatar_color: Optional[str] = None,
) -> TwinGroup:
    group = TwinGroup(
        name=name,
        description=description,
        created_by_user_id=created_by_user_id,
        avatar_color=avatar_color,
    )
    db.add(group)
    db.flush()
    return group


def list_groups(db: Session) -> list[dict]:
    rows = db.query(TwinGroup).order_by(TwinGroup.created_at.desc()).all()
    return [_serialize_group(db, g) for g in rows]


def get_group(db: Session, group_id: UUID) -> Optional[dict]:
    g = db.query(TwinGroup).filter(TwinGroup.id == group_id).first()
    if not g:
        return None
    return _serialize_group(db, g, full_members=True)


def delete_group(db: Session, group_id: UUID) -> bool:
    g = db.query(TwinGroup).filter(TwinGroup.id == group_id).first()
    if not g:
        return False
    db.delete(g)
    db.flush()
    return True


def add_member(
    db: Session, group_id: UUID, user_id: UUID, role: str = "member",
) -> dict:
    """Add a worker by user_id. Auto-include their linked twin so meeting
    commands find both halves.
    """
    user = db.query(PlatformUser).filter(PlatformUser.id == user_id).first()
    if not user:
        raise ValueError("User not found")
    g = db.query(TwinGroup).filter(TwinGroup.id == group_id).first()
    if not g:
        raise ValueError("Group not found")

    existing = (
        db.query(TwinGroupMember)
        .filter(
            TwinGroupMember.group_id == group_id,
            TwinGroupMember.user_id == user_id,
        )
        .first()
    )
    if existing:
        return {
            "added": False,
            "reason": "already a member",
            "member_id": str(existing.id),
        }

    twin_id = user.twin_id
    twin_name = None
    if twin_id:
        twin = db.query(DigitalTwin).filter(DigitalTwin.id == twin_id).first()
        if twin:
            twin_name = twin.name

    member = TwinGroupMember(
        group_id=group_id,
        user_id=user_id,
        twin_id=twin_id,
        role=role,
    )
    db.add(member)
    db.flush()

    return {
        "added": True,
        "member_id": str(member.id),
        "user_id": str(user_id),
        "user_name": user.name,
        "user_email": user.email,
        "twin_id": str(twin_id) if twin_id else None,
        "twin_name": twin_name,
    }


def remove_member(db: Session, group_id: UUID, user_id: UUID) -> bool:
    m = (
        db.query(TwinGroupMember)
        .filter(
            TwinGroupMember.group_id == group_id,
            TwinGroupMember.user_id == user_id,
        )
        .first()
    )
    if not m:
        return False
    db.delete(m)
    db.flush()
    return True


def list_member_twin_ids(db: Session, group_id: UUID) -> list[UUID]:
    """All twin_ids of a group's members. Used by meeting scheduler to
    invite the right twins.
    """
    rows = (
        db.query(TwinGroupMember.twin_id)
        .filter(
            TwinGroupMember.group_id == group_id,
            TwinGroupMember.twin_id.isnot(None),
        )
        .all()
    )
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
#  Group chat messages
# ---------------------------------------------------------------------------

def post_message(
    db: Session,
    group_id: UUID,
    content: str,
    sender_type: str = "boss",
    sender_user_id: Optional[UUID] = None,
    sender_twin_id: Optional[UUID] = None,
    meta: Optional[dict] = None,
) -> TwinGroupMessage:
    msg = TwinGroupMessage(
        group_id=group_id,
        content=content,
        sender_type=sender_type,
        sender_user_id=sender_user_id,
        sender_twin_id=sender_twin_id,
        meta_json=meta or {},
    )
    db.add(msg)
    db.flush()
    return msg


def list_messages(db: Session, group_id: UUID, limit: int = 100) -> list[dict]:
    rows = (
        db.query(TwinGroupMessage)
        .filter(TwinGroupMessage.group_id == group_id)
        .order_by(TwinGroupMessage.created_at.asc())
        .limit(limit)
        .all()
    )
    out = []
    for m in rows:
        sender_label = "Boss"
        if m.sender_type == "twin" and m.sender_twin_id:
            twin = db.query(DigitalTwin).filter(DigitalTwin.id == m.sender_twin_id).first()
            if twin:
                sender_label = f"{twin.name} (Twin)"
        elif m.sender_type == "worker" and m.sender_user_id:
            user = db.query(PlatformUser).filter(PlatformUser.id == m.sender_user_id).first()
            if user:
                sender_label = user.name
        elif m.sender_type == "system":
            sender_label = "System"
        out.append({
            "id": str(m.id),
            "sender_type": m.sender_type,
            "sender_label": sender_label,
            "content": m.content,
            "meta": m.meta_json or {},
            "created_at": m.created_at.isoformat() if m.created_at else None,
        })
    return out


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def _serialize_group(db: Session, g: TwinGroup, full_members: bool = False) -> dict:
    member_count = len(g.members) if g.members else 0
    out = {
        "id": str(g.id),
        "name": g.name,
        "description": g.description,
        "avatar_color": g.avatar_color,
        "member_count": member_count,
        "created_at": g.created_at.isoformat() if g.created_at else None,
    }
    if full_members:
        members_data = []
        for m in g.members:
            user = db.query(PlatformUser).filter(PlatformUser.id == m.user_id).first() if m.user_id else None
            twin = db.query(DigitalTwin).filter(DigitalTwin.id == m.twin_id).first() if m.twin_id else None
            members_data.append({
                "member_id": str(m.id),
                "user_id": str(m.user_id) if m.user_id else None,
                "user_name": user.name if user else None,
                "user_email": user.email if user else None,
                "twin_id": str(m.twin_id) if m.twin_id else None,
                "twin_name": twin.name if twin else None,
                "twin_status": twin.status if twin else None,
                "role": m.role,
            })
        out["members"] = members_data
    return out
