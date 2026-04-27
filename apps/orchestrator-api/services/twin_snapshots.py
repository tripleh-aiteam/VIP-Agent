"""
VIP AI Platform — Twin Version Snapshots
Save and restore twin personality versions.

Inspired by colleague.skill's version control approach.
Use cases:
- Save twin before making big changes (safety net)
- Restore if new training made twin worse
- Milestone snapshots (weekly/monthly)
- Compare versions
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from db.models import DigitalTwin, TwinKnowledge, TwinSnapshot
from services.logger import log


def create_snapshot(
    db: Session,
    twin_id: UUID,
    version_name: str,
    notes: str = "",
    snapshot_type: str = "manual",
) -> TwinSnapshot:
    """Create a snapshot of the twin's current state."""
    twin = db.query(DigitalTwin).filter(DigitalTwin.id == twin_id).first()
    if not twin:
        return None

    # Get current knowledge IDs
    knowledge = db.query(TwinKnowledge).filter(TwinKnowledge.twin_id == twin_id).all()
    knowledge_ids = [str(k.id) for k in knowledge]

    # Calculate intelligence
    from services.twin_intelligence import get_twin_intelligence
    intel = get_twin_intelligence(db, twin_id)
    intel_pct = intel.get("intelligence_pct", 0) if intel else 0

    snapshot = TwinSnapshot(
        twin_id=twin_id,
        version_name=version_name,
        snapshot_type=snapshot_type,
        personality_prompt=twin.personality_prompt,
        skills_json=twin.skills or [],
        mode=twin.mode,
        permission_level=twin.permission_level,
        knowledge_count=len(knowledge_ids),
        intelligence_pct=intel_pct,
        knowledge_ids=knowledge_ids,
        notes=notes,
    )
    db.add(snapshot)
    db.flush()

    log.info(f"snapshot: created '{version_name}' for {twin.name} ({len(knowledge_ids)} items, {intel_pct}%)",
             extra={"action": "twin.snapshot_created"})
    return snapshot


def list_snapshots(db: Session, twin_id: UUID) -> list[dict]:
    """Get all snapshots for a twin."""
    snapshots = (
        db.query(TwinSnapshot)
        .filter(TwinSnapshot.twin_id == twin_id)
        .order_by(TwinSnapshot.created_at.desc())
        .all()
    )
    return [
        {
            "id": str(s.id),
            "version_name": s.version_name,
            "snapshot_type": s.snapshot_type,
            "knowledge_count": s.knowledge_count,
            "intelligence_pct": s.intelligence_pct,
            "mode": s.mode,
            "permission_level": s.permission_level,
            "notes": s.notes,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in snapshots
    ]


def restore_snapshot(db: Session, snapshot_id: UUID) -> dict:
    """
    Restore a twin to a snapshot.
    WARNING: This overrides current personality and removes knowledge added after snapshot.
    """
    snapshot = db.query(TwinSnapshot).filter(TwinSnapshot.id == snapshot_id).first()
    if not snapshot:
        return {"error": "Snapshot not found"}

    twin = db.query(DigitalTwin).filter(DigitalTwin.id == snapshot.twin_id).first()
    if not twin:
        return {"error": "Twin not found"}

    # Save current state as auto-backup BEFORE restoring
    current_backup = create_snapshot(
        db, twin.id,
        version_name=f"Auto-backup before restoring to '{snapshot.version_name}'",
        snapshot_type="auto",
        notes=f"Automatically created before restoring snapshot {snapshot.id}",
    )

    # Restore twin profile
    twin.personality_prompt = snapshot.personality_prompt
    twin.skills = snapshot.skills_json or []
    twin.mode = snapshot.mode
    twin.permission_level = snapshot.permission_level
    twin.updated_at = datetime.utcnow()

    # Delete knowledge items added AFTER snapshot (keep only those in snapshot's list)
    snapshot_knowledge_ids = set(snapshot.knowledge_ids or [])
    current_knowledge = db.query(TwinKnowledge).filter(TwinKnowledge.twin_id == twin.id).all()
    removed = 0
    for k in current_knowledge:
        if str(k.id) not in snapshot_knowledge_ids:
            db.delete(k)
            removed += 1

    db.flush()

    log.info(f"snapshot: restored '{snapshot.version_name}' — removed {removed} items added after snapshot",
             extra={"action": "twin.snapshot_restored"})

    return {
        "restored": True,
        "version_name": snapshot.version_name,
        "knowledge_count_restored": snapshot.knowledge_count,
        "items_removed": removed,
        "backup_created": str(current_backup.id) if current_backup else None,
    }


def delete_snapshot(db: Session, snapshot_id: UUID) -> bool:
    """Delete a snapshot (doesn't affect twin)."""
    snapshot = db.query(TwinSnapshot).filter(TwinSnapshot.id == snapshot_id).first()
    if not snapshot:
        return False
    db.delete(snapshot)
    db.flush()
    return True
