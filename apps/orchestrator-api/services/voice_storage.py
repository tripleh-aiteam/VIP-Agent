"""
voice_storage — Supabase Storage bucket for call recordings.

Bucket: voice-recordings (private, auth required)
Path scheme: /{agent_id}/{call_id}.mp3

Per-agent isolation: each agent only sees its own folder; Supabase RLS
policies on the storage.objects table enforce this. Signed URLs let the
admin dashboard play recordings without exposing the bucket publicly.

How recordings actually get in here:
  - Vapi's end-of-call webhook contains a `recordingUrl` (Vapi-hosted).
  - Background job downloads from Vapi, re-uploads to our bucket at
    /{agent_id}/{call_id}.mp3, then drops the Vapi URL.
  - The webhook handler calls voice_storage.upload_recording_from_url(...)

Configure (one-time) per environment:
  - SUPABASE_URL          (already used elsewhere)
  - SUPABASE_SERVICE_KEY  (service_role key — admin uploads + signed URLs)
  - VOICE_RECORDINGS_BUCKET (default: "voice-recordings")
  - VOICE_RECORDINGS_RETENTION_DAYS (default: 30)
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

import httpx

from services.logger import log


BUCKET = os.getenv("VOICE_RECORDINGS_BUCKET", "voice-recordings")
DEFAULT_RETENTION_DAYS = int(os.getenv("VOICE_RECORDINGS_RETENTION_DAYS", "30"))


def _supabase_base() -> Optional[str]:
    url = os.getenv("SUPABASE_URL", "")
    return url.rstrip("/") if url else None


def _service_key() -> Optional[str]:
    return os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")


def _storage_path(agent_id: str, call_id: UUID | str) -> str:
    """Per-agent path: voice-recordings/{agent_id}/{call_id}.mp3"""
    return f"{agent_id}/{call_id}.mp3"


# ============================================================================
#  Bucket setup — one-time, idempotent
# ============================================================================

def ensure_bucket() -> bool:
    """Idempotent: create the bucket if it doesn't exist. Returns True on
    success or if the bucket already exists.

    Run once at deploy time (or call from a startup hook). Marked private:
    files are NOT publicly readable — every access goes through a signed
    URL with a TTL.
    """
    base = _supabase_base()
    key = _service_key()
    if not base or not key:
        log.warning("voice.storage: SUPABASE_URL or SERVICE_KEY missing — skipping bucket creation")
        return False
    try:
        with httpx.Client(timeout=10) as client:
            # Check existence first (avoids 400 on duplicate create)
            resp = client.get(
                f"{base}/storage/v1/bucket/{BUCKET}",
                headers={"Authorization": f"Bearer {key}", "apikey": key},
            )
            if resp.status_code == 200:
                return True
            # Create
            resp = client.post(
                f"{base}/storage/v1/bucket",
                headers={"Authorization": f"Bearer {key}", "apikey": key},
                json={"name": BUCKET, "public": False, "file_size_limit": 50 * 1024 * 1024},
            )
            return resp.status_code in (200, 201, 409)
    except Exception as e:
        log.warning(f"voice.storage: ensure_bucket error: {e}")
        return False


# ============================================================================
#  Upload — pull from provider's recording URL, push into our bucket
# ============================================================================

def upload_recording_from_url(
    agent_id: str, call_id: UUID | str, source_url: str
) -> Optional[dict]:
    """Stream the provider's recording into our Supabase bucket. Returns
    `{path, size_bytes}` on success, None on failure. Caller is responsible
    for writing the metadata row via voice_service.upsert_recording."""
    base = _supabase_base()
    key = _service_key()
    if not base or not key:
        log.warning("voice.storage: env missing — skipping upload")
        return None

    path = _storage_path(agent_id, call_id)
    try:
        with httpx.Client(timeout=60) as client:
            src = client.get(source_url)
            if src.status_code != 200:
                log.warning(f"voice.storage: source fetch failed {src.status_code}")
                return None
            audio_bytes = src.content
            up = client.post(
                f"{base}/storage/v1/object/{BUCKET}/{path}",
                headers={
                    "Authorization": f"Bearer {key}",
                    "apikey": key,
                    "Content-Type": "audio/mpeg",
                    "x-upsert": "true",
                },
                content=audio_bytes,
            )
            if up.status_code not in (200, 201):
                log.warning(f"voice.storage: upload failed {up.status_code}: {up.text[:200]}")
                return None
            return {"path": path, "size_bytes": len(audio_bytes)}
    except Exception as e:
        log.warning(f"voice.storage: upload error: {e}")
        return None


# ============================================================================
#  Signed URL — dashboard fetches recordings via these (never raw bucket links)
# ============================================================================

def create_signed_url(
    agent_id: str, call_id: UUID | str, expires_in: int = 60 * 60
) -> Optional[tuple[str, datetime]]:
    """Get a TTL-limited signed URL for a stored recording. `expires_in`
    is seconds (default 1 hour). Returns `(url, expires_at)` or None on
    failure (object missing, env unset, etc.)."""
    base = _supabase_base()
    key = _service_key()
    if not base or not key:
        return None
    path = _storage_path(agent_id, call_id)
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(
                f"{base}/storage/v1/object/sign/{BUCKET}/{path}",
                headers={"Authorization": f"Bearer {key}", "apikey": key},
                json={"expiresIn": expires_in},
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            signed_path = data.get("signedURL") or data.get("signedUrl")
            if not signed_path:
                return None
            full_url = f"{base}/storage/v1{signed_path}" if signed_path.startswith("/") else signed_path
            expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
            return full_url, expires_at
    except Exception as e:
        log.warning(f"voice.storage: signed-url error: {e}")
        return None


def retention_deadline(days: int = DEFAULT_RETENTION_DAYS) -> datetime:
    """Compute the deletion deadline for a fresh upload. Use for the
    voice_recordings.retention_expires_at column."""
    return datetime.utcnow() + timedelta(days=days)


def delete_storage_object(agent_id: str, call_id: UUID | str) -> bool:
    """Delete a single recording object from Storage. Used by the
    retention cleanup cron. Returns True on success or 404 (already
    gone), False on transport / auth errors so the caller can retry."""
    base = _supabase_base()
    key = _service_key()
    if not base or not key:
        return False
    path = _storage_path(agent_id, call_id)
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.delete(
                f"{base}/storage/v1/object/{BUCKET}/{path}",
                headers={"Authorization": f"Bearer {key}", "apikey": key},
            )
            return resp.status_code in (200, 204, 404)
    except Exception as e:
        log.warning(f"voice.storage: delete error for {path}: {e}")
        return False


def cleanup_expired_recordings() -> dict:
    """Cron entry point — delete every recording whose retention_expires_at
    has passed. Removes both the Storage object and the voice_recordings row.
    Returns `{deleted: N, errors: M}` for telemetry. Safe to run repeatedly.

    Scheduled from services/scheduler_service.py — once daily.
    """
    from db.base import SessionLocal
    from db.models import VoiceRecording

    deleted = 0
    errors = 0
    db = SessionLocal()
    try:
        expired = (
            db.query(VoiceRecording)
            .filter(VoiceRecording.retention_expires_at < datetime.utcnow())
            .limit(200)     # bound the batch — repeat tomorrow if more left
            .all()
        )
        for rec in expired:
            ok = delete_storage_object(rec.agent_id, rec.call_id)
            if not ok:
                errors += 1
                continue
            db.delete(rec)
            deleted += 1
        db.commit()
    finally:
        db.close()
    if deleted or errors:
        log.info(
            f"voice.storage: retention cleanup — deleted {deleted}, errors {errors}",
            extra={"action": "voice.storage_cleanup"},
        )
    return {"deleted": deleted, "errors": errors}
