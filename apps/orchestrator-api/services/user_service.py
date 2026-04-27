"""
VIP AI Platform — User Service
User management, role-based access, org isolation, notification management.
"""

from datetime import datetime
from uuid import UUID
from typing import Any

from sqlalchemy.orm import Session

from db.models import PlatformUser, PlatformNotification
from services.logger import log


# ---------------------------------------------------------------------------
# Roles & Permissions
# ---------------------------------------------------------------------------

ROLE_PERMISSIONS = {
    "admin": {
        "can_approve_judgement": True,
        "can_compose_reports": True,
        "can_manage_agents": True,
        "can_manage_users": True,
        "can_manage_schedules": True,
        "can_send_a2a": True,
        "can_view_audit": True,
    },
    "operator": {
        "can_approve_judgement": True,
        "can_compose_reports": True,
        "can_manage_agents": False,
        "can_manage_users": False,
        "can_manage_schedules": True,
        "can_send_a2a": True,
        "can_view_audit": True,
    },
    "viewer": {
        "can_approve_judgement": False,
        "can_compose_reports": False,
        "can_manage_agents": False,
        "can_manage_users": False,
        "can_manage_schedules": False,
        "can_send_a2a": False,
        "can_view_audit": False,
    },
}


def check_permission(role: str, permission: str) -> bool:
    """Check if a role has a specific permission."""
    return ROLE_PERMISSIONS.get(role, {}).get(permission, False)


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------

def get_or_create_user(db: Session, email: str, name: str = "", org_id: str = "default") -> PlatformUser:
    """Get existing user or create new one."""
    user = db.query(PlatformUser).filter(PlatformUser.email == email).first()
    if user:
        user.last_login_at = datetime.utcnow()
        db.commit()
        return user

    user = PlatformUser(
        email=email,
        name=name or email.split("@")[0],
        role="admin" if not db.query(PlatformUser).first() else "viewer",  # first user = admin
        org_id=org_id,
    )
    db.add(user)
    db.commit()
    log.info(f"user created: {email} (role={user.role})", extra={"action": "user.created"})
    return user


def list_users(db: Session, org_id: str | None = None) -> list[dict]:
    """List all users, optionally filtered by org."""
    q = db.query(PlatformUser)
    if org_id:
        q = q.filter(PlatformUser.org_id == org_id)
    users = q.order_by(PlatformUser.created_at.desc()).all()
    return [_user_to_dict(u) for u in users]


def update_user_role(db: Session, user_id: UUID, new_role: str) -> dict | None:
    """Update a user's role. Only admin can do this."""
    if new_role not in ROLE_PERMISSIONS:
        raise ValueError(f"Invalid role: {new_role}. Must be one of: {list(ROLE_PERMISSIONS.keys())}")
    user = db.query(PlatformUser).filter(PlatformUser.id == user_id).first()
    if not user:
        return None
    user.role = new_role
    db.commit()
    log.info(f"user role updated: {user.email} -> {new_role}", extra={"action": "user.role_updated"})
    return _user_to_dict(user)


def _user_to_dict(user: PlatformUser) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "org_id": user.org_id,
        "status": user.status,
        "permissions": ROLE_PERMISSIONS.get(user.role, {}),
        "telegram_linked": bool(user.telegram_user_id),
        "last_login": user.last_login_at.isoformat() if user.last_login_at else None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "has_twin": getattr(user, "has_twin", False) or False,
        "twin_id": str(user.twin_id) if getattr(user, "twin_id", None) else None,
        "department": getattr(user, "department", None),
    }


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def create_notification(
    db: Session,
    title: str,
    body: str,
    severity: str = "info",
    notification_type: str = "general",
    trace_id: str = "",
    user_id: UUID | None = None,
) -> PlatformNotification:
    """Create a notification. If user_id is None, creates for all users."""
    notif = PlatformNotification(
        user_id=user_id,
        title=title,
        body=body,
        severity=severity,
        notification_type=notification_type,
        source_trace_id=trace_id,
    )
    db.add(notif)
    db.commit()
    return notif


def get_notifications(
    db: Session,
    user_id: UUID | None = None,
    unread_only: bool = False,
    limit: int = 50,
) -> list[dict]:
    """Get notifications for a user (or all if no user_id)."""
    q = db.query(PlatformNotification)
    if user_id:
        q = q.filter(
            (PlatformNotification.user_id == user_id) | (PlatformNotification.user_id.is_(None))
        )
    if unread_only:
        q = q.filter(PlatformNotification.is_read == False)
    notifs = q.order_by(PlatformNotification.created_at.desc()).limit(limit).all()
    return [
        {
            "id": str(n.id),
            "title": n.title,
            "body": n.body,
            "severity": n.severity,
            "type": n.notification_type,
            "trace_id": n.source_trace_id,
            "is_read": n.is_read,
            "created_at": n.created_at.isoformat() if n.created_at else None,
        }
        for n in notifs
    ]


def get_unread_count(db: Session, user_id: UUID | None = None) -> int:
    """Get count of unread notifications."""
    q = db.query(PlatformNotification).filter(PlatformNotification.is_read == False)
    if user_id:
        q = q.filter(
            (PlatformNotification.user_id == user_id) | (PlatformNotification.user_id.is_(None))
        )
    return q.count()


def mark_as_read(db: Session, notification_id: UUID) -> bool:
    """Mark a single notification as read."""
    notif = db.query(PlatformNotification).filter(PlatformNotification.id == notification_id).first()
    if not notif:
        return False
    notif.is_read = True
    db.commit()
    return True


def mark_all_read(db: Session, user_id: UUID | None = None) -> int:
    """Mark all notifications as read. Returns count."""
    q = db.query(PlatformNotification).filter(PlatformNotification.is_read == False)
    if user_id:
        q = q.filter(
            (PlatformNotification.user_id == user_id) | (PlatformNotification.user_id.is_(None))
        )
    count = q.update({PlatformNotification.is_read: True})
    db.commit()
    return count
