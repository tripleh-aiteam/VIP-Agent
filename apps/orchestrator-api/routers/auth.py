"""
VIP AI Platform — Auth Router
POST /auth/login, /auth/change-password, /auth/forgot-password, /auth/reset-password
"""

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from db.base import get_db
from services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


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
