"""
VIP AI Platform — Auth Router
POST /auth/login, /auth/change-password, /auth/forgot-password, /auth/reset-password
"""

from typing import Optional

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session

from db.base import get_db
from services import auth_service, embed_auth

router = APIRouter(prefix="/auth", tags=["auth"])

# How long a boss-embed session token is valid (the iframe stays usable this long).
EMBED_TOKEN_TTL_SECONDS = 60 * 60 * 8  # 8h — matches the dashboard session length


class LoginBody(BaseModel):
    email: str = Field(default="admin")
    password: str = Field(...)


class ChangePasswordBody(BaseModel):
    email: str = Field(...)
    current_password: str = Field(...)
    new_password: str = Field(...)


class ForgotPasswordBody(BaseModel):
    email: str = Field(...)


class ResetPasswordBody(BaseModel):
    token: str = Field(...)
    new_password: str = Field(...)


@router.post("/login")
def login(body: LoginBody, db: Session = Depends(get_db)):
    result = auth_service.login(db, body.email, body.password)
    if not result["success"]:
        raise HTTPException(401, result["error"])
    return result


class EmbedTokenBody(BaseModel):
    twin_id: str = Field(...)


@router.post("/embed-token")
def embed_token(
    body: EmbedTokenBody,
    x_user_email: Optional[str] = Header(default=None),
    x_user_token: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    """Mint a short-lived signed token that lets the boss open a twin's portal.

    Auth: the caller must present a valid ADMIN/OPERATOR login session (the same
    X-User-Email + X-User-Token the dashboard already holds). Identity for the
    minted token comes from the verified session — never from the request body —
    so a client cannot choose who they act as.
    """
    user = auth_service.verify_session_token(db, x_user_email or "", x_user_token or "")
    if not user or user.role not in ("admin", "operator"):
        raise HTTPException(status_code=403, detail="Admin session required")

    token = embed_auth.mint_embed_token(
        twin_id=body.twin_id,
        principal=user.email,
        ttl_seconds=EMBED_TOKEN_TTL_SECONDS,
    )
    if not token:
        # No signing secret configured on the orchestrator → fail closed.
        raise HTTPException(status_code=503, detail="Embed tokens are disabled (EMBED_SIGNING_SECRET unset)")
    return {"token": token, "expires_in": EMBED_TOKEN_TTL_SECONDS}


@router.post("/change-password")
def change_password(body: ChangePasswordBody, db: Session = Depends(get_db)):
    result = auth_service.change_password(db, body.email, body.current_password, body.new_password)
    if not result["success"]:
        raise HTTPException(400, result["error"])
    return result


@router.post("/forgot-password")
def forgot_password(body: ForgotPasswordBody, db: Session = Depends(get_db)):
    result = auth_service.forgot_password(db, body.email)
    return result


@router.post("/forgot-password-telegram")
def forgot_password_telegram(body: ForgotPasswordBody, db: Session = Depends(get_db)):
    """Send a temporary password directly to Telegram — no email needed."""
    result = auth_service.reset_via_telegram(db, body.email)
    return result


@router.post("/reset-password")
def reset_password(body: ResetPasswordBody, db: Session = Depends(get_db)):
    result = auth_service.reset_password(db, body.token, body.new_password)
    if not result["success"]:
        raise HTTPException(400, result["error"])
    return result
