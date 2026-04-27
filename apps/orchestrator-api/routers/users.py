"""
VIP AI Platform — Users & Notifications Router
User management, role-based access, notification bell.
"""

from uuid import UUID
from pydantic import BaseModel, Field
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from db.base import get_db
from services import user_service

router = APIRouter(tags=["users"])


# ---------------------------------------------------------------------------
# User endpoints
# ---------------------------------------------------------------------------

class CreateUserBody(BaseModel):
    email: str = Field(...)
    name: str = Field(default="")
    org_id: str = Field(default="default")


@router.post("/users", status_code=201)
def create_or_get_user(body: CreateUserBody, db: Session = Depends(get_db)):
    """Get or create a platform user."""
    user = user_service.get_or_create_user(db, email=body.email, name=body.name, org_id=body.org_id)
    return user_service._user_to_dict(user)


@router.get("/users")
def list_users(org_id: Optional[str] = Query(None), db: Session = Depends(get_db)):
    """List all platform users."""
    return user_service.list_users(db, org_id=org_id)


@router.get("/users/{user_id}")
def get_user(user_id: UUID, db: Session = Depends(get_db)):
    """Get a user by ID."""
    from db.models import PlatformUser
    user = db.query(PlatformUser).filter(PlatformUser.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    return user_service._user_to_dict(user)


class CreateWorkerBody(BaseModel):
    email: str = Field(...)
    name: str = Field(...)
    password: str = Field(...)
    twin_id: Optional[str] = Field(None, description="Digital twin ID to link")
    department: Optional[str] = None


@router.post("/users/worker", status_code=201)
def create_worker(body: CreateWorkerBody, db: Session = Depends(get_db)):
    """Create a worker account with password and optional twin link."""
    from db.models import PlatformUser, DigitalTwin
    from services.auth_service import _hash_password

    # Check if email already exists
    existing = db.query(PlatformUser).filter(PlatformUser.email == body.email).first()
    if existing:
        raise HTTPException(400, "Email already registered")

    user = PlatformUser(
        email=body.email,
        name=body.name,
        password_hash=_hash_password(body.password),
        role="worker",
        department=body.department,
    )

    # Link twin if provided
    if body.twin_id:
        twin = db.query(DigitalTwin).filter(DigitalTwin.id == body.twin_id).first()
        if twin:
            user.has_twin = True
            user.twin_id = twin.id

    db.add(user)
    db.commit()

    return {
        "created": True,
        "id": str(user.id),
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "has_twin": user.has_twin,
        "twin_id": str(user.twin_id) if user.twin_id else None,
    }


class UpdateRoleBody(BaseModel):
    role: str = Field(..., description="admin | operator | viewer | worker")


@router.patch("/users/{user_id}/role")
def update_role(user_id: UUID, body: UpdateRoleBody, db: Session = Depends(get_db)):
    """Update a user's role."""
    try:
        result = user_service.update_user_role(db, user_id, body.role)
        if not result:
            raise HTTPException(404, "User not found")
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/roles")
def list_roles():
    """List all available roles and their permissions."""
    return user_service.ROLE_PERMISSIONS


# ---------------------------------------------------------------------------
# Notification endpoints
# ---------------------------------------------------------------------------

@router.get("/notifications")
def get_notifications(
    unread_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Get notifications (notification bell data)."""
    return user_service.get_notifications(db, unread_only=unread_only, limit=limit)


@router.get("/notifications/unread-count")
def get_unread_count(db: Session = Depends(get_db)):
    """Get unread notification count (for bell badge)."""
    count = user_service.get_unread_count(db)
    return {"unread": count}


@router.patch("/notifications/{notification_id}/read")
def mark_read(notification_id: UUID, db: Session = Depends(get_db)):
    """Mark a notification as read."""
    success = user_service.mark_as_read(db, notification_id)
    if not success:
        raise HTTPException(404, "Notification not found")
    return {"marked": True}


@router.post("/notifications/mark-all-read")
def mark_all_read(db: Session = Depends(get_db)):
    """Mark all notifications as read."""
    count = user_service.mark_all_read(db)
    return {"marked": count}
