"""
VIP AI Platform — Telegram Router
POST /telegram/webhook — receives updates from Telegram Bot API
GET /telegram/status — bot configuration status
POST /telegram/set-webhook — register webhook URL
POST /telegram/test-send — test send a message
POST /telegram/link-user — link a Telegram user
"""

from pydantic import BaseModel, Field
from typing import Optional
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from db.base import get_db
from db.models import TelegramUser
from services import telegram_service
from services.logger import log

router = APIRouter(prefix="/telegram", tags=["telegram"])


class SetWebhookBody(BaseModel):
    url: str = Field(..., description="Public webhook URL (e.g., from ngrok)")


class TestSendBody(BaseModel):
    chat_id: str = Field(...)
    text: str = Field(...)


class LinkUserBody(BaseModel):
    telegram_user_id: str = Field(...)
    linked_user_id: str = Field(default="system")
    role: str = Field(default="viewer", description="admin | operator | viewer")


@router.post("/webhook")
async def webhook(request: Request, db: Session = Depends(get_db)):
    """Receive Telegram webhook updates. Processes bot commands."""
    body = await request.json()

    message = body.get("message", {})
    text = message.get("text", "")
    chat = message.get("chat", {})
    user = message.get("from", {})

    chat_id = str(chat.get("id", ""))
    telegram_user_id = str(user.get("id", ""))

    if not text or not chat_id:
        return {"ok": True, "skipped": True}

    # Parse command
    parts = text.strip().split()
    command = parts[0].lower().split("@")[0]  # Handle /command@BotName
    args = parts[1:]

    log.info(
        f"telegram: {telegram_user_id} -> {command}",
        extra={"action": "telegram.webhook"},
    )

    # Process command
    response = telegram_service.handle_command(db, telegram_user_id, chat_id, command, args)

    # Send response back
    telegram_service.send_message(chat_id, response)

    return {"ok": True}


@router.get("/status")
def telegram_status():
    """Check Telegram bot configuration and connectivity."""
    info = telegram_service.get_bot_info()
    return info


@router.post("/set-webhook")
def set_webhook(body: SetWebhookBody):
    """Register a webhook URL with Telegram. Use ngrok URL for local dev."""
    ok = telegram_service.set_webhook(body.url)
    return {"success": ok, "webhook_url": body.url}


@router.post("/test-send")
def test_send(body: TestSendBody):
    """Test sending a message to a chat. Use to verify bot token works."""
    ok = telegram_service.send_message(body.chat_id, body.text)
    return {"sent": ok, "chat_id": body.chat_id}


@router.post("/link-user")
def link_user(body: LinkUserBody, db: Session = Depends(get_db)):
    """Link a Telegram user ID to the platform. Required for command access."""
    existing = db.query(TelegramUser).filter(TelegramUser.telegram_user_id == body.telegram_user_id).first()
    if existing:
        existing.linked_user_id = body.linked_user_id
        existing.role = body.role
        existing.status = "active"
        db.commit()
        return {"linked": True, "updated": True, "telegram_user_id": body.telegram_user_id, "role": body.role}

    user = TelegramUser(
        telegram_user_id=body.telegram_user_id,
        linked_user_id=body.linked_user_id,
        role=body.role,
        status="active",
    )
    db.add(user)
    db.commit()
    return {"linked": True, "created": True, "telegram_user_id": body.telegram_user_id, "role": body.role}


@router.get("/users")
def list_users(db: Session = Depends(get_db)):
    """List all linked Telegram users."""
    users = db.query(TelegramUser).order_by(TelegramUser.created_at.desc()).all()
    return [
        {
            "id": str(u.id),
            "telegram_user_id": u.telegram_user_id,
            "linked_user_id": u.linked_user_id,
            "role": u.role,
            "status": u.status,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in users
    ]


@router.post("/simulate")
def simulate_command(
    telegram_user_id: str = "admin_000",
    command: str = "/status",
    db: Session = Depends(get_db),
):
    """Simulate a Telegram command locally (no actual Telegram needed). For testing."""
    parts = command.strip().split()
    cmd = parts[0]
    args = parts[1:]
    response = telegram_service.handle_command(db, telegram_user_id, "test-chat", cmd, args)
    return {"command": cmd, "args": args, "response": response}
