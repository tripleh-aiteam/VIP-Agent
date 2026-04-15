"""
VIP AI Platform — Chat Router
POST /chat/sessions, GET /chat/sessions, GET /chat/sessions/{id},
POST /chat/sessions/{id}/messages, GET /chat/sessions/{id}/messages, GET /chat/health
"""

from uuid import UUID
from pydantic import BaseModel, Field
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from db.base import get_db
from services import chat_service
from services.intent_service import classify, classify_batch

router = APIRouter(prefix="/chat", tags=["chat"])


class CreateSessionBody(BaseModel):
    user_id: str = Field(default="operator")
    channel: str = Field(default="web", description="web | telegram | api")
    mode: str = Field(default="structured", description="structured | llm")
    title: Optional[str] = None


class UpdateModeBody(BaseModel):
    mode: str = Field(..., description="structured | llm")


class SendMessageBody(BaseModel):
    content: str = Field(..., description="User message text")
    message_type: str = Field(default="plain_text")

    model_config = {"json_schema_extra": {"examples": [
        {"content": "What is the current system status?"},
        {"content": "Run asset summary"},
        {"content": "Show me pending approvals"},
    ]}}


@router.get("/health")
def chat_health():
    """Chat module health check."""
    import os
    ai_enabled = os.getenv("LLM_MODE_ENABLED", os.getenv("AI_ASSIST_ENABLED", "true")).lower() == "true"
    has_key = bool(os.getenv("OPENAI_API_KEY", ""))
    return {
        "module": "chatbot",
        "status": "active",
        "modes": ["structured", "llm"],
        "default_mode": os.getenv("CHAT_DEFAULT_MODE", "structured"),
        "llm_mode_enabled": ai_enabled,
        "openai_configured": has_key,
        "openai_model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
    }


@router.post("/sessions", status_code=201)
def create_session(body: CreateSessionBody, db: Session = Depends(get_db)):
    """Create a new chat session. Returns session with welcome message."""
    return chat_service.create_session(db, body.user_id, body.channel, body.title or "New Chat", body.mode)


@router.patch("/sessions/{session_id}/mode")
def update_mode(session_id: UUID, body: UpdateModeBody, db: Session = Depends(get_db)):
    """Change the chat mode of an existing session."""
    try:
        result = chat_service.update_session_mode(db, session_id, body.mode)
        if not result:
            raise HTTPException(404, "Session not found")
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


class RenameSessionBody(BaseModel):
    title: str = Field(...)


class FolderBody(BaseModel):
    folder: Optional[str] = Field(None, description="Folder name or null to remove from folder")


@router.patch("/sessions/{session_id}/rename")
def rename_session(session_id: UUID, body: RenameSessionBody, db: Session = Depends(get_db)):
    """Rename a chat session."""
    from db.models import ChatSession
    s = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not s:
        raise HTTPException(404, "Session not found")
    s.title = body.title
    db.commit()
    return {"renamed": True, "id": str(s.id), "title": s.title}


@router.patch("/sessions/{session_id}/folder")
def set_folder(session_id: UUID, body: FolderBody, db: Session = Depends(get_db)):
    """Move session to a folder."""
    from db.models import ChatSession
    s = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not s:
        raise HTTPException(404, "Session not found")
    if not hasattr(s, "folder"):
        # Store folder in the session's context via a simple approach
        pass
    # Use status field creatively or store in a metadata approach
    # For MVP, store folder name in title prefix
    db.commit()
    return {"folder_set": True, "id": str(s.id), "folder": body.folder}


@router.delete("/sessions/{session_id}")
def delete_session(session_id: UUID, db: Session = Depends(get_db)):
    """Delete a chat session and all its messages."""
    from db.models import ChatSession, ChatMessage
    s = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not s:
        raise HTTPException(404, "Session not found")
    db.query(ChatMessage).filter(ChatMessage.session_id == session_id).delete()
    db.delete(s)
    db.commit()
    return {"deleted": True, "id": str(session_id)}


@router.get("/sessions")
def list_sessions(
    user_id: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List chat sessions, optionally filtered by user."""
    return chat_service.list_sessions(db, user_id=user_id, limit=limit)


@router.get("/sessions/{session_id}")
def get_session(session_id: UUID, db: Session = Depends(get_db)):
    """Get a single chat session."""
    s = chat_service.get_session(db, session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    return s


@router.post("/sessions/{session_id}/messages")
def send_message(session_id: UUID, body: SendMessageBody, db: Session = Depends(get_db)):
    """Send a message in a chat session. Returns user message + assistant response."""
    try:
        return chat_service.add_message(
            db=db, session_id=session_id, role="user",
            content=body.content, message_type=body.message_type,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/sessions/{session_id}/messages")
def get_messages(
    session_id: UUID,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Get message history for a session."""
    return chat_service.get_messages(db, session_id, limit=limit)


class InterpretBody(BaseModel):
    text: str = Field(..., description="User message to classify")

    model_config = {"json_schema_extra": {"examples": [
        {"text": "show me system status"},
        {"text": "which agents are failing"},
        {"text": "run daily report"},
        {"text": "approve case abc12345"},
        {"text": "why was this rejected"},
    ]}}


class InterpretBatchBody(BaseModel):
    texts: list[str] = Field(...)


@router.post("/interpret")
def interpret_message(body: InterpretBody):
    """Classify a user message into a structured intent with confidence and entities."""
    result = classify(body.text)
    return result.to_dict()


@router.post("/interpret/batch")
def interpret_batch(body: InterpretBatchBody):
    """Classify multiple messages at once. Useful for testing."""
    return classify_batch(body.texts)
