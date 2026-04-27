"""
VIP AI Platform — Claude Code Auto-Importer
Reads Claude Code session files automatically and feeds them to the worker's twin.

Claude Code stores sessions as JSONL files at:
  Windows: C:/Users/{user}/.claude/projects/{project-name}/{session-id}.jsonl
  Mac:     /Users/{user}/.claude/projects/{project-name}/{session-id}.jsonl
  Linux:   /home/{user}/.claude/projects/{project-name}/{session-id}.jsonl

Each session file contains user messages + assistant responses + tool calls in JSONL format.
"""

import os
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from db.models import DigitalTwin, TwinKnowledge
from services import twin_service
from services.logger import log


# Default Claude Code paths
def get_claude_projects_dir() -> Optional[Path]:
    """Find Claude Code projects directory."""
    candidates = [
        Path.home() / ".claude" / "projects",  # Standard path
        Path(os.getenv("CLAUDE_PROJECTS_DIR", "")),
    ]
    for p in candidates:
        if p and p.exists() and p.is_dir():
            return p
    return None


def list_claude_projects() -> list[dict]:
    """List all Claude Code projects on the PC."""
    projects_dir = get_claude_projects_dir()
    if not projects_dir:
        return []

    projects = []
    for p in projects_dir.iterdir():
        if p.is_dir():
            # Count session files
            session_files = list(p.glob("*.jsonl"))
            if session_files:
                # Get latest modification time
                latest = max((f.stat().st_mtime for f in session_files), default=0)
                projects.append({
                    "name": p.name,
                    "path": str(p),
                    "session_count": len(session_files),
                    "last_modified": datetime.fromtimestamp(latest).isoformat() if latest else None,
                })

    # Sort by latest activity
    projects.sort(key=lambda x: x["last_modified"] or "", reverse=True)
    return projects


def read_session_file(filepath: Path) -> dict:
    """Read a JSONL session file and extract user messages + assistant responses."""
    messages = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Skip queue operations and metadata
                if entry.get("type") == "queue-operation":
                    continue

                # Extract user messages
                if entry.get("type") == "user":
                    msg = entry.get("message", {})
                    content = msg.get("content", [])
                    if isinstance(content, list):
                        text_parts = []
                        for c in content:
                            if isinstance(c, dict) and c.get("type") == "text":
                                text = c.get("text", "").strip()
                                # Skip system reminders and metadata
                                if "<ide_opened_file>" in text or "<system-reminder>" in text:
                                    continue
                                if text:
                                    text_parts.append(text)
                        if text_parts:
                            messages.append({
                                "role": "user",
                                "content": "\n".join(text_parts),
                                "timestamp": entry.get("timestamp"),
                            })
                    elif isinstance(content, str) and content.strip():
                        messages.append({
                            "role": "user",
                            "content": content.strip(),
                            "timestamp": entry.get("timestamp"),
                        })

                # Extract assistant messages
                elif entry.get("type") == "assistant":
                    msg = entry.get("message", {})
                    content = msg.get("content", [])
                    if isinstance(content, list):
                        text_parts = []
                        for c in content:
                            if isinstance(c, dict) and c.get("type") == "text":
                                text = c.get("text", "").strip()
                                if text:
                                    text_parts.append(text)
                        if text_parts:
                            messages.append({
                                "role": "assistant",
                                "content": "\n".join(text_parts),
                                "timestamp": entry.get("timestamp"),
                            })
    except Exception as e:
        log.warning(f"claude_auto: failed to read {filepath}: {e}")

    return {
        "session_id": filepath.stem,
        "project": filepath.parent.name,
        "messages": messages,
        "message_count": len(messages),
        "file_modified": datetime.fromtimestamp(filepath.stat().st_mtime).isoformat() if filepath.exists() else None,
    }


def _was_session_imported(db: Session, twin_id: UUID, session_id: str) -> bool:
    """Check if this session was already imported to avoid duplicates."""
    existing = (
        db.query(TwinKnowledge)
        .filter(TwinKnowledge.twin_id == twin_id)
        .filter(TwinKnowledge.title.like(f"%[Claude Auto] Session {session_id[:8]}%"))
        .first()
    )
    return existing is not None


def import_recent_sessions(
    db: Session,
    twin_id: UUID,
    project_filter: Optional[str] = None,
    hours: int = 24,
    max_sessions: int = 10,
) -> dict:
    """
    Auto-import recent Claude Code sessions for a twin.

    project_filter: Only import sessions from this project name (e.g. "c--Users-TRIPLEH-Desktop-VIP-Agent")
    hours: Only sessions modified in last N hours
    max_sessions: Maximum sessions to import per run
    """
    twin = twin_service.get_twin(db, twin_id)
    if not twin:
        return {"error": "Twin not found"}

    projects_dir = get_claude_projects_dir()
    if not projects_dir:
        return {"error": "Claude Code projects directory not found"}

    cutoff_ts = (datetime.utcnow() - timedelta(hours=hours)).timestamp()

    # Collect session files
    session_files = []
    for project_path in projects_dir.iterdir():
        if not project_path.is_dir():
            continue
        if project_filter and project_path.name != project_filter:
            continue

        for sf in project_path.glob("*.jsonl"):
            if sf.stat().st_mtime >= cutoff_ts:
                session_files.append(sf)

    # Sort by newest first, limit
    session_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    session_files = session_files[:max_sessions]

    imported = []
    skipped = 0

    for sf in session_files:
        session_id = sf.stem
        if _was_session_imported(db, twin_id, session_id):
            skipped += 1
            continue

        session_data = read_session_file(sf)
        if session_data["message_count"] < 2:
            continue  # Skip empty/trivial sessions

        # Build transcript
        transcript_parts = []
        for m in session_data["messages"][:30]:  # Max 30 messages per session
            role = "User" if m["role"] == "user" else "Claude"
            content = m["content"][:500]  # Truncate long messages
            transcript_parts.append(f"{role}: {content}")
        transcript = "\n\n".join(transcript_parts)

        if len(transcript) < 100:
            continue  # Skip trivial sessions

        # Save as knowledge
        date_str = (session_data.get("file_modified") or "")[:10]
        session_title = f"{session_data['project'].replace('c--Users-TRIPLEH-Desktop-', '')} ({date_str})"

        twin_service.add_knowledge(
            db, twin_id,
            title=f"[Claude Auto] Session {session_id[:8]} — {session_title}",
            content=f"Claude Code Session: {session_title}\n\n{transcript[:4000]}",
            source_type="document",
        )

        imported.append({
            "session_id": session_id[:8],
            "project": session_data["project"],
            "messages": session_data["message_count"],
            "transcript_length": len(transcript),
        })

    # Log activity
    if imported:
        twin_service.log_activity(
            db, twin_id, "auto_learn",
            f"Auto-imported {len(imported)} Claude Code sessions",
            {"source": "claude_auto", "count": len(imported), "skipped": skipped},
        )

    db.flush()

    return {
        "twin_name": twin.name,
        "imported_count": len(imported),
        "skipped_count": skipped,
        "imported": imported,
        "projects_dir": str(projects_dir),
    }


def auto_import_all_twins(db: Session) -> list[dict]:
    """Called by scheduler — auto-import for all twins linked to workers."""
    twins = db.query(DigitalTwin).all()
    results = []
    for twin in twins:
        try:
            result = import_recent_sessions(db, twin.id, hours=6, max_sessions=3)
            if result.get("imported_count", 0) > 0:
                results.append(result)
        except Exception as e:
            log.warning(f"claude_auto: failed for {twin.name}: {e}")
    return results
