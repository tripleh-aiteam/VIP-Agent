"""
VIP AI Platform — Twin Notification Service
Creates notifications for workers when their twin does something.
"""

from uuid import UUID
from sqlalchemy.orm import Session
from db.models import TwinNotification


def notify(db: Session, twin_id: UUID, type: str, title: str, body: str = ""):
    """Create a notification for a worker's twin."""
    n = TwinNotification(
        twin_id=twin_id,
        type=type,
        title=title,
        body=body,
    )
    db.add(n)
    db.flush()
    return n


def get_notifications(db: Session, twin_id: UUID, unread_only: bool = False, limit: int = 20):
    """Get notifications for a twin."""
    query = db.query(TwinNotification).filter(TwinNotification.twin_id == twin_id)
    if unread_only:
        query = query.filter(TwinNotification.is_read == False)
    return query.order_by(TwinNotification.created_at.desc()).limit(limit).all()


def get_unread_count(db: Session, twin_id: UUID) -> int:
    """Count unread notifications."""
    return (
        db.query(TwinNotification)
        .filter(TwinNotification.twin_id == twin_id, TwinNotification.is_read == False)
        .count()
    )


def mark_read(db: Session, notification_id: UUID):
    """Mark a single notification as read."""
    n = db.query(TwinNotification).filter(TwinNotification.id == notification_id).first()
    if n:
        n.is_read = True
        db.flush()


def mark_all_read(db: Session, twin_id: UUID) -> int:
    """Mark all notifications as read for a twin."""
    notifications = (
        db.query(TwinNotification)
        .filter(TwinNotification.twin_id == twin_id, TwinNotification.is_read == False)
        .all()
    )
    for n in notifications:
        n.is_read = True
    db.flush()
    return len(notifications)
