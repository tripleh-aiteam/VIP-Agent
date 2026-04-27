"""
VIP AI Platform — Claude Code Session Import
Imports Claude Code conversation sessions as twin knowledge.

The user works with Claude Code daily for development.
Each session contains valuable context: code decisions, architecture choices, debugging approaches.
This service extracts that knowledge and feeds it to the worker's twin.
"""

import re
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from db.models import DigitalTwin
from services import twin_service
from services.llm_client import chat_completion_sync
from services.logger import log


def import_claude_session(
    db: Session,
    twin_id: UUID,
    session_text: str,
    session_title: Optional[str] = None,
    auto_extract: bool = True,
) -> dict:
    """
    Import a Claude Code session and extract useful knowledge for the twin.

    session_text: Raw conversation text (User messages + Claude responses)
    session_title: Optional title for the session
    auto_extract: If True, use LLM to extract structured insights
    """
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        return {"error": "Twin not found"}

    if not session_text or len(session_text.strip()) < 50:
        return {"error": "Session text too short"}

    # Auto-generate title if not provided
    if not session_title:
        date_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
        session_title = f"Claude Code Session — {date_str}"

    # Save raw session as a document
    raw_content = session_text[:5000] if len(session_text) > 5000 else session_text
    raw_knowledge = twin_service.add_knowledge(
        db, twin_id,
        title=f"[Claude Code] {session_title}",
        content=f"Claude Code Session Transcript:\n\n{raw_content}",
        source_type="document",
    )

    extracted_items = []

    if auto_extract:
        # Use LLM to extract structured insights
        extraction_prompt = f"""Analyze this Claude Code development session. Extract USEFUL knowledge for the worker's digital twin.

SESSION TRANSCRIPT:
{session_text[:3000]}

Extract these 4 types of items (if present):

1. DECISIONS — technical choices made (e.g., "Chose Redis over Kafka for pub/sub")
2. PATTERNS — code patterns or approaches used (e.g., "Used dependency injection for services")
3. RULES — rules discovered (e.g., "Always validate input before DB write")
4. LEARNINGS — key insights from debugging or problem-solving

Format as JSON array:
[
  {{"type": "decision", "title": "Short title", "content": "Detail"}},
  {{"type": "pattern", "title": "...", "content": "..."}},
  ...
]

Only include clear, specific items. Return empty array [] if nothing useful found."""

        response = chat_completion_sync(
            system_prompt="You extract structured technical knowledge from developer conversations. Return valid JSON only.",
            messages=[{"role": "user", "content": extraction_prompt}],
            max_tokens=600,
            temperature=0.2,
        )

        # Parse JSON from response
        import json
        try:
            start = response.find("[")
            end = response.rfind("]") + 1
            if start >= 0 and end > start:
                items = json.loads(response[start:end])
                for item in items[:10]:  # Max 10 items per session
                    item_type = item.get("type", "learning").lower()
                    title = item.get("title", "")
                    content = item.get("content", "")

                    if not title or not content:
                        continue

                    # Map type to source_type
                    if item_type == "decision" or item_type == "rule":
                        source_type = "decision"
                    elif item_type == "pattern":
                        source_type = "instruction"
                    else:
                        source_type = "document"

                    twin_service.add_knowledge(
                        db, twin_id,
                        title=f"[Claude] {title[:80]}",
                        content=f"{content[:400]}\n\n(From Claude Code session: {session_title})",
                        source_type=source_type,
                    )
                    extracted_items.append({"type": item_type, "title": title})
        except Exception as e:
            log.warning(f"claude_import: extraction failed: {e}")

    # Log activity
    twin_service.log_activity(
        db, twin_id, "auto_learn",
        f"Imported Claude Code session: {session_title}",
        {
            "source": "claude_code",
            "session_title": session_title,
            "extracted_count": len(extracted_items),
        },
    )

    db.flush()

    return {
        "imported": True,
        "twin_id": str(twin_id),
        "twin_name": twin.name,
        "session_title": session_title,
        "raw_knowledge_id": str(raw_knowledge.id),
        "extracted_items": extracted_items,
        "extracted_count": len(extracted_items),
        "session_length_chars": len(session_text),
    }


def import_generic_ai_session(
    db: Session,
    twin_id: UUID,
    session_text: str,
    source: str = "chatgpt",
    session_title: Optional[str] = None,
) -> dict:
    """
    Generic import for ChatGPT/Gemini/Claude sessions.
    Source can be: chatgpt, gemini, claude, bard, copilot.
    """
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        return {"error": "Twin not found"}

    if not session_text or len(session_text.strip()) < 30:
        return {"error": "Session text too short"}

    if not session_title:
        date_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
        session_title = f"{source.upper()} Session — {date_str}"

    # Save raw content
    raw_content = session_text[:5000] if len(session_text) > 5000 else session_text
    raw_knowledge = twin_service.add_knowledge(
        db, twin_id,
        title=f"[{source.upper()}] {session_title}",
        content=f"{source.upper()} Conversation:\n\n{raw_content}",
        source_type="document",
    )

    # Extract Q&A pairs (simpler than Claude — these are usually Q&A format)
    extracted_count = _extract_qa_pairs(db, twin_id, session_text, source, session_title)

    twin_service.log_activity(
        db, twin_id, "auto_learn",
        f"Imported {source} session: {session_title}",
        {"source": source, "session_title": session_title, "qa_count": extracted_count},
    )

    db.flush()

    return {
        "imported": True,
        "twin_id": str(twin_id),
        "twin_name": twin.name,
        "source": source,
        "session_title": session_title,
        "qa_pairs_extracted": extracted_count,
    }


def _extract_qa_pairs(db: Session, twin_id: UUID, text: str, source: str, title: str) -> int:
    """Extract Q&A pairs from a conversation text."""
    # Split by common conversation markers
    # Look for patterns like "You:", "User:", "Me:", "Q:" followed by assistant responses
    lines = text.split("\n")

    pairs = []
    current_q = None
    current_a = []

    for line in lines:
        line_lower = line.lower().strip()

        # Detect question markers
        if any(line_lower.startswith(m) for m in ["you:", "user:", "me:", "q:", "question:", "human:"]):
            # Save previous pair
            if current_q and current_a:
                pairs.append((current_q, "\n".join(current_a)))
            current_q = line.split(":", 1)[1].strip() if ":" in line else line
            current_a = []
        elif any(line_lower.startswith(m) for m in ["chatgpt:", "gemini:", "claude:", "assistant:", "ai:", "bard:", "copilot:", "a:", "answer:"]):
            content = line.split(":", 1)[1].strip() if ":" in line else line
            current_a.append(content)
        elif current_q is not None and line.strip():
            current_a.append(line.strip())

    # Save last pair
    if current_q and current_a:
        pairs.append((current_q, "\n".join(current_a)))

    # Save meaningful Q&As as knowledge
    saved = 0
    for q, a in pairs[:8]:  # Max 8 Q&As per import
        if len(q) < 15 or len(a) < 30:
            continue  # Skip trivial

        twin_service.add_knowledge(
            db, twin_id,
            title=f"[{source.upper()}] Q: {q[:60]}",
            content=f"Q: {q}\nA: {a[:400]}",
            source_type="document",
        )
        saved += 1

    return saved
