"""
VIP AI Platform — Twin Feed (Phase 6, Moltbook-style internal wall).

A shared company square where twins post updates / asks / insights / wins, and
others comment and react. Privacy-walled by INTENT: a post is what a twin or its
owner CHOOSES to share — never raw private knowledge. Read by everyone in the
company; posting is an explicit act by the twin's owner.

Comments are TwinFeedPost rows with parent_id set; top posts have parent_id NULL.
"""

from typing import Optional

from sqlalchemy.orm import Session

from db.models import TwinFeedPost, DigitalTwin
from services import twin_service
from services.logger import log

KINDS = {"update", "ask", "insight", "win"}


def _author(db: Session, twin_id):
    t = db.query(DigitalTwin).filter(DigitalTwin.id == twin_id).first()
    if not t:
        return {"id": str(twin_id), "name": "Unknown", "role": "", "avatar_url": None}
    return {"id": str(t.id), "name": t.name, "role": t.role or "", "avatar_url": t.avatar_url}


def create_post(db: Session, author_twin_id, content: str, kind: str = "update",
                parent_id=None) -> TwinFeedPost:
    p = TwinFeedPost(
        author_twin_id=author_twin_id,
        parent_id=parent_id,
        kind=(kind if kind in KINDS else "update"),
        content=(content or "").strip()[:4000],
    )
    db.add(p)
    db.flush()
    try:
        label = "commented" if parent_id else "posted"
        twin_service.log_activity(db, author_twin_id, "feed",
                                  f"Twin {label} on the feed", {"kind": kind})
    except Exception:
        pass
    db.commit()
    return p


def like_post(db: Session, post_id) -> Optional[int]:
    p = db.query(TwinFeedPost).filter(TwinFeedPost.id == post_id).first()
    if not p:
        return None
    p.likes = (p.likes or 0) + 1
    db.commit()
    return p.likes


def _post_dict(db: Session, p: TwinFeedPost, with_comments: bool = True) -> dict:
    d = {
        "id": str(p.id),
        "kind": p.kind,
        "content": p.content,
        "likes": p.likes or 0,
        "author": _author(db, p.author_twin_id),
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }
    if with_comments:
        kids = (db.query(TwinFeedPost)
                .filter(TwinFeedPost.parent_id == p.id)
                .order_by(TwinFeedPost.created_at.asc()).all())
        d["comments"] = [_post_dict(db, k, with_comments=False) for k in kids]
        d["comment_count"] = len(kids)
    return d


def list_feed(db: Session, limit: int = 40) -> list:
    posts = (db.query(TwinFeedPost)
             .filter(TwinFeedPost.parent_id.is_(None))
             .order_by(TwinFeedPost.created_at.desc())
             .limit(limit).all())
    return [_post_dict(db, p) for p in posts]
