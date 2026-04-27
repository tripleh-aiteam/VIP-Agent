"""
VIP AI Platform — Google Drive Auto-Learning Service
Connects to worker's Google Drive and pulls documents automatically.

Setup:
1. Create Google Cloud project
2. Enable Google Drive API
3. Create OAuth 2.0 credentials (Desktop app)
4. Set env vars: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
5. Worker authorizes via Twin Portal → token stored in DB

Documents are pulled every 2 hours and saved to TwinKnowledge.
"""

import os
import json
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from db.models import DigitalTwin, TwinKnowledge, PlatformUser
from services import twin_service
from services.logger import log

# Google OAuth config
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:3001/auth/google/callback")


def is_configured() -> bool:
    """Check if Google Drive integration is configured."""
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def get_auth_url(twin_id: str) -> str:
    """Generate Google OAuth authorization URL."""
    scopes = "https://www.googleapis.com/auth/drive.readonly"
    return (
        f"https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={GOOGLE_CLIENT_ID}"
        f"&redirect_uri={GOOGLE_REDIRECT_URI}"
        f"&response_type=code"
        f"&scope={scopes}"
        f"&access_type=offline"
        f"&state={twin_id}"
    )


def exchange_code(code: str) -> Optional[dict]:
    """Exchange authorization code for tokens."""
    import httpx
    try:
        resp = httpx.post("https://oauth2.googleapis.com/token", data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
        })
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception as e:
        log.error(f"gdrive: token exchange failed: {e}")
        return None


def refresh_token(refresh_token_str: str) -> Optional[str]:
    """Refresh an expired access token."""
    import httpx
    try:
        resp = httpx.post("https://oauth2.googleapis.com/token", data={
            "refresh_token": refresh_token_str,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "grant_type": "refresh_token",
        })
        if resp.status_code == 200:
            return resp.json().get("access_token")
        return None
    except Exception:
        return None


def pull_documents(db: Session, twin_id: UUID, access_token: str, max_docs: int = 10) -> list[dict]:
    """
    Pull recent documents from worker's Google Drive.
    Reads: Google Docs, Sheets (text), TXT, MD files.
    Saves new/updated docs to TwinKnowledge.
    """
    import httpx

    pulled = []
    try:
        # List recent docs (modified in last 7 days)
        week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat() + "Z"
        resp = httpx.get(
            "https://www.googleapis.com/drive/v3/files",
            headers={"Authorization": f"Bearer {access_token}"},
            params={
                "q": f"modifiedTime > '{week_ago}' and (mimeType contains 'document' or mimeType contains 'text' or mimeType contains 'spreadsheet')",
                "fields": "files(id,name,mimeType,modifiedTime)",
                "orderBy": "modifiedTime desc",
                "pageSize": max_docs,
            },
        )

        if resp.status_code != 200:
            log.warning(f"gdrive: list failed {resp.status_code}")
            return []

        files = resp.json().get("files", [])

        for file in files:
            file_id = file["id"]
            file_name = file["name"]
            mime_type = file["mimeType"]

            # Check if already saved (by title match)
            existing = (
                db.query(TwinKnowledge)
                .filter(TwinKnowledge.twin_id == twin_id, TwinKnowledge.title == f"[Drive] {file_name}")
                .first()
            )

            # Export/download content
            content = _download_file_content(access_token, file_id, mime_type)
            if not content or len(content) < 20:
                continue

            # Truncate long docs
            if len(content) > 5000:
                content = content[:5000] + "\n\n[Truncated — first 5000 chars]"

            if existing:
                # Update existing
                existing.content = content
                existing.created_at = datetime.utcnow()
                pulled.append({"name": file_name, "action": "updated"})
            else:
                # Save new
                twin_service.add_knowledge(
                    db, twin_id,
                    title=f"[Drive] {file_name}",
                    content=content,
                    source_type="document",
                )
                pulled.append({"name": file_name, "action": "added"})

            twin_service.log_activity(
                db, twin_id, "auto_learn",
                f"Learned from Google Drive: {file_name}",
                {"source": "google_drive", "file_id": file_id},
            )

        db.flush()

    except Exception as e:
        log.error(f"gdrive: pull failed: {e}")

    return pulled


def _download_file_content(access_token: str, file_id: str, mime_type: str) -> str:
    """Download file content from Google Drive."""
    import httpx

    try:
        if "google-apps" in mime_type:
            # Google Docs/Sheets → export as plain text
            export_mime = "text/plain"
            if "spreadsheet" in mime_type:
                export_mime = "text/csv"
            resp = httpx.get(
                f"https://www.googleapis.com/drive/v3/files/{file_id}/export",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"mimeType": export_mime},
            )
        else:
            # Regular files → download
            resp = httpx.get(
                f"https://www.googleapis.com/drive/v3/files/{file_id}",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"alt": "media"},
            )

        if resp.status_code == 200:
            return resp.text
        return ""
    except Exception:
        return ""
