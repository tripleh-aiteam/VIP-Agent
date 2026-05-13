"""
VIP AI Platform — Meeting Retention Service (Sprint 6)
Background cleanup for meeting media and audit data per the project's
retention policy. The DB rows stay (for audit / commitment provenance)
but the audio files and signed URLs are purged.

Configuration via env vars:
  MEETING_AUDIO_RETENTION_DAYS         (default 90)   — twin TTS replies
  VOICE_SAMPLE_RETENTION_DAYS          (default 365)  — worker voice samples
  REVOKED_PROFILE_GRACE_DAYS           (default 30)   — purge after consent revoke

Wire into the existing scheduler_service (or call manually from
/twins/admin/retention/purge for ops use).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from db.models import MeetingUtterance, WorkerVoiceProfile, MeetingParticipant
from services.logger import log


_AUDIO_RETENTION_DAYS = int(os.getenv("MEETING_AUDIO_RETENTION_DAYS", "90"))
_SAMPLE_RETENTION_DAYS = int(os.getenv("VOICE_SAMPLE_RETENTION_DAYS", "365"))
_REVOKED_GRACE_DAYS = int(os.getenv("REVOKED_PROFILE_GRACE_DAYS", "30"))


def purge_old_meeting_audio(
    db: Session, retention_days: Optional[int] = None, dry_run: bool = False,
) -> dict:
    """Delete twin-voice WAV files older than retention_days. The
    MeetingUtterance row stays so commitments + transcripts remain
    auditable; just the audio_url is nulled out.
    """
    days = retention_days or _AUDIO_RETENTION_DAYS
    cutoff = datetime.utcnow() - timedelta(days=days)

    rows = (
        db.query(MeetingUtterance)
        .filter(
            MeetingUtterance.created_at < cutoff,
            MeetingUtterance.audio_url.isnot(None),
        )
        .all()
    )
    files_removed = 0
    bytes_freed = 0
    rows_updated = 0
    errors = 0

    for row in rows:
        local_path = _audio_url_to_local_path(row.audio_url)
        if local_path and local_path.exists():
            try:
                bytes_freed += local_path.stat().st_size
                if not dry_run:
                    local_path.unlink()
                files_removed += 1
            except Exception as e:
                log.warning(f"meeting_retention: could not remove {local_path}: {e}")
                errors += 1
        if not dry_run:
            row.audio_url = None
            rows_updated += 1

    if not dry_run:
        db.commit()

    return {
        "retention_days": days,
        "cutoff": cutoff.isoformat(),
        "rows_scanned": len(rows),
        "rows_updated": rows_updated,
        "files_removed": files_removed,
        "bytes_freed": bytes_freed,
        "errors": errors,
        "dry_run": dry_run,
    }


def purge_old_voice_samples(
    db: Session, retention_days: Optional[int] = None, dry_run: bool = False,
) -> dict:
    """Delete voice samples (raw worker voice recordings) past retention.
    The WorkerVoiceProfile row stays but `sample_url` is nulled.
    """
    days = retention_days or _SAMPLE_RETENTION_DAYS
    cutoff = datetime.utcnow() - timedelta(days=days)

    rows = (
        db.query(WorkerVoiceProfile)
        .filter(
            WorkerVoiceProfile.created_at < cutoff,
            WorkerVoiceProfile.sample_url.isnot(None),
        )
        .all()
    )
    return _purge_files_from(db, rows, "sample_url", dry_run=dry_run, retention_days=days)


def purge_revoked_profiles(db: Session, dry_run: bool = False) -> dict:
    """Worker revoked consent — wait grace days, then purge sample + model."""
    cutoff = datetime.utcnow() - timedelta(days=_REVOKED_GRACE_DAYS)
    rows = (
        db.query(WorkerVoiceProfile)
        .filter(
            WorkerVoiceProfile.status == "revoked",
            WorkerVoiceProfile.consent_revoked_at.isnot(None),
            WorkerVoiceProfile.consent_revoked_at < cutoff,
        )
        .all()
    )
    return _purge_files_from(
        db, rows, "sample_url",
        also_clear=("melotts_model_path",),
        dry_run=dry_run,
        retention_days=_REVOKED_GRACE_DAYS,
    )


def run_all(db: Session, dry_run: bool = False) -> dict:
    """Run every retention job. Returns a combined report — safe to call
    from a daily cron or an admin endpoint.
    """
    return {
        "audio": purge_old_meeting_audio(db, dry_run=dry_run),
        "voice_samples": purge_old_voice_samples(db, dry_run=dry_run),
        "revoked_profiles": purge_revoked_profiles(db, dry_run=dry_run),
        "ran_at": datetime.utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
#  Internal helpers
# ---------------------------------------------------------------------------

def _audio_url_to_local_path(url: str) -> Optional[Path]:
    """Map /static/twin_voice/foo.wav → uploads/twin_voice/foo.wav."""
    if not url or not url.startswith("/static/"):
        return None
    rel = url[len("/static/"):]
    base = Path(__file__).resolve().parent.parent / "uploads"
    return base / rel


def _purge_files_from(
    db: Session,
    rows: list,
    url_attr: str,
    also_clear: tuple[str, ...] = (),
    dry_run: bool = False,
    retention_days: int = 0,
) -> dict:
    files_removed = 0
    bytes_freed = 0
    rows_updated = 0
    errors = 0
    for row in rows:
        url = getattr(row, url_attr, None)
        local_path = _audio_url_to_local_path(url) if url else None
        if local_path and local_path.exists():
            try:
                bytes_freed += local_path.stat().st_size
                if not dry_run:
                    local_path.unlink()
                files_removed += 1
            except Exception as e:
                log.warning(f"meeting_retention: purge {local_path} failed: {e}")
                errors += 1
        if not dry_run:
            setattr(row, url_attr, None)
            for attr in also_clear:
                setattr(row, attr, None)
            rows_updated += 1
    if not dry_run:
        db.commit()
    return {
        "retention_days": retention_days,
        "rows_scanned": len(rows),
        "rows_updated": rows_updated,
        "files_removed": files_removed,
        "bytes_freed": bytes_freed,
        "errors": errors,
        "dry_run": dry_run,
    }
