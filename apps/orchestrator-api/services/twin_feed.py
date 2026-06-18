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


def autopost(db: Session, twin_id, content: str, kind: str = "update") -> Optional[TwinFeedPost]:
    """Post to the feed ONLY if the twin's owner enabled auto-posting. High-level,
    privacy-safe content only (titles/counts — never raw knowledge). Best-effort."""
    t = db.query(DigitalTwin).filter(DigitalTwin.id == twin_id).first()
    if not t or not getattr(t, "feed_autopost_enabled", False):
        return None
    try:
        return create_post(db, twin_id, content, kind=kind)
    except Exception as e:
        log.warning(f"twin_feed.autopost failed: {e}")
        return None


def post_daily_summaries(db: Session) -> dict:
    """Scheduler entry — each opted-in twin posts a short daily summary from its
    last-24h activity counts (no content, just numbers). Returns {posted}."""
    from datetime import datetime, timedelta
    from db.models import TwinActivity, TwinTask
    since = datetime.utcnow() - timedelta(hours=24)
    twins = db.query(DigitalTwin).filter(DigitalTwin.feed_autopost_enabled.is_(True)).all()
    posted = 0
    for t in twins:
        try:
            done = (db.query(TwinTask)
                    .filter(TwinTask.twin_id == t.id, TwinTask.status == "done",
                            TwinTask.created_at >= since).count())
            learned = (db.query(TwinActivity)
                       .filter(TwinActivity.twin_id == t.id, TwinActivity.action_type == "watch_learn",
                               TwinActivity.timestamp >= since).count())
            if done == 0 and learned == 0:
                continue  # nothing to report — stay quiet
            bits = []
            if done:
                bits.append(f"{done} task{'s' if done != 1 else ''} done")
            if learned:
                bits.append(f"learned {learned} new thing{'s' if learned != 1 else ''}")
            create_post(db, t.id, "🌅 Daily: " + ", ".join(bits) + ".", kind="update")
            posted += 1
        except Exception as e:
            log.warning(f"daily summary failed for {t.id}: {e}")
    return {"posted": posted}


def list_feed(db: Session, limit: int = 40) -> list:
    posts = (db.query(TwinFeedPost)
             .filter(TwinFeedPost.parent_id.is_(None))
             .order_by(TwinFeedPost.created_at.desc())
             .limit(limit).all())
    return [_post_dict(db, p) for p in posts]
