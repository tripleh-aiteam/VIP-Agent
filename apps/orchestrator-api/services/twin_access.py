"""
VIP AI Platform — Twin Access Control
Ensures workers can only access their own twin.
Boss (admin/operator) can access any twin.
"""

from uuid import UUID
from typing import Optional

from fastapi import Header, HTTPException, Depends
from sqlalchemy.orm import Session

from db.base import get_db
from db.models import PlatformUser
from services.auth_service import _hash_password


def get_current_user(
    x_user_email: Optional[str] = Header(None),
    x_user_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
) -> Optional[dict]:
    """
    Extract current user from headers.
    Returns user dict with id, email, role, twin_id.
    Returns None if no auth headers (backward compatible — boss doesn't need headers).
    """
    if not x_user_email:
        return None  # No auth header = boss (backward compatible)

    user = db.query(PlatformUser).filter(PlatformUser.email == x_user_email).first()
    if not user:
        return None

    return {
        "id": str(user.id),
        "email": user.email,
        "role": user.role,
        "twin_id": str(user.twin_id) if user.twin_id else None,
    }


def verify_twin_access(
    twin_id: UUID,
    x_user_email: Optional[str] = Header(None),
    x_user_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """
    Verify the current user can access this twin.
    - No headers (boss/admin) → allowed (backward compatible)
    - admin/operator role → allowed (boss can access any twin)
    - worker role → only their own twin_id
    """
    if not x_user_email:
        return  # No auth header = boss mode, allow all

    user = db.query(PlatformUser).filter(PlatformUser.email == x_user_email).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    # Boss roles can access any twin
    if user.role in ("admin", "operator", "viewer"):
        return

    # Worker can only access their own twin
    if user.role == "worker":
        if not user.twin_id or str(user.twin_id) != str(twin_id):
            raise HTTPException(status_code=403, detail="You can only access your own twin")
        return

    raise HTTPException(status_code=403, detail="Access denied")
