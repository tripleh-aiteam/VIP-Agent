"""
hourly_capture — every hour, snapshot the hour's RAW market data into the DB as
'<kind>_snapshot' parts (Korean news headlines, YouTube uploads, stock prices).
These are NOT emailed. The 6 AM daily build then reads ALL ~24 parts of the day
and synthesises the big, smart report per type (Kiwoom / Newspaper / YouTube),
so the morning report covers the WHOLE day & night — not just the last fetch.

Kept deliberately CHEAP (free RSS + price fetch, no LLM, no paid search) so
running it 24×/day is affordable; the heavy LLM synthesis happens once at 6 AM.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from db.base import SessionLocal
from db.models import OrchReport
from services.logger import log
from services.kst import kst_now


def _save_snapshot(db, kind: str, items: list[dict]) -> None:
    db.add(OrchReport(
        report_type=f"{kind}_snapshot",
        source_run_ids_json=[],
        content_json={
            "kind": kind,
            "hour": kst_now().strftime("%Y-%m-%d %H:00 KST"),
            "captured_at": datetime.utcnow().isoformat(),
            "items": items,
        },
        delivery_channel="snapshot",
    ))
    db.commit()


def _snapshot_newspaper(db) -> int:
    """Free Korean-outlet RSS only (no paid Serper) — market-relevant articles."""
    from services import newspaper_report as N, news_fetch
    items: list[dict] = []
    for paper in N.NEWSPAPERS:
        if paper["region"] != "KR":
            continue
        name, site = paper["name"], paper["site"]
        try:
            for it in news_fetch.rss_items(name, hours=2, cap=14):
                u = it.get("url", "")
                if u and N._domain_ok(u, site) and N._is_market_relevant(it):
                    items.append({"outlet": name, "title": it.get("title", ""), "url": u,
                                  "snippet": (it.get("summary") or "")[:400], "pub": it.get("pub")})
        except Exception as e:
            log.warning(f"hourly newspaper {name}: {str(e)[:60]}")
    _save_snapshot(db, "newspaper", items)
    return len(items)


def _snapshot_youtube(db) -> int:
    from services import youtube_report as Y
    items: list[dict] = []
    for ch in Y.YT_CHANNELS:
        try:
            vid = ch.get("video_id")
            chan_id = Y._channel_id(vid) if vid else None
            for u in (Y._latest_uploads(chan_id, n=8) if chan_id else []):
                if Y._within_24h(u.get("published")):
                    vidid = u.get("video_id", "")
                    items.append({"channel": ch["name"], "title": u.get("title", ""),
                                  "video_id": vidid,
                                  "url": f"https://www.youtube.com/watch?v={vidid}",
                                  "description": (u.get("description", "") or "")[:500],
                                  "published": u.get("published")})
        except Exception as e:
            log.warning(f"hourly youtube {ch.get('name')}: {str(e)[:60]}")
    _save_snapshot(db, "youtube", items)
    return len(items)


def _snapshot_kiwoom(db) -> int:
    from services import kiwoom_report as K
    rows = K._gather()
    items = [{"t": r["t"], "ko": r["ko"], "close": r.get("close"),
              "change_pct": r.get("change_pct"), "volume": r.get("volume"),
              "price_kind": r.get("price_kind"), "time": r.get("data_time")}
             for r in rows if r.get("ok")]
    _save_snapshot(db, "kiwoom", items)
    return len(items)


def capture_hourly() -> None:
    """One hourly part for each report type. Runs at :05 every hour."""
    db = SessionLocal()
    try:
        n = _snapshot_newspaper(db)
        v = _snapshot_youtube(db)
        k = _snapshot_kiwoom(db)
        log.info(f"hourly_capture: newspaper={n} youtube={v} kiwoom={k}",
                 extra={"action": "hourly.capture"})
    except Exception as e:
        log.warning(f"hourly_capture failed: {str(e)[:150]}", extra={"action": "hourly.capture.failed"})
    finally:
        db.close()


def accumulated(db, kind: str, hours: int = 26) -> list[dict]:
    """All snapshot items of `kind` captured in the last `hours`, newest first,
    deduped by url/title. This is the day's '24 parts' the 6 AM build reads."""
    try:
        since = datetime.utcnow() - timedelta(hours=hours)
        snaps = (db.query(OrchReport)
                 .filter(OrchReport.report_type == f"{kind}_snapshot")
                 .filter(OrchReport.created_at >= since)
                 .order_by(OrchReport.created_at.desc()).limit(40).all())
    except Exception as e:
        log.warning(f"accumulated({kind}): {str(e)[:80]}")
        return []
    seen: set[str] = set()
    out: list[dict] = []
    for s in snaps:
        for it in ((s.content_json or {}).get("items") or []):
            key = (it.get("url") or it.get("title") or "")[:140]
            if key and key not in seen:
                seen.add(key)
                out.append(it)
    return out


def cleanup_old_snapshots(db, keep_hours: int = 30) -> int:
    """Delete snapshots older than keep_hours so the table doesn't grow forever."""
    try:
        cutoff = datetime.utcnow() - timedelta(hours=keep_hours)
        q = (db.query(OrchReport)
             .filter(OrchReport.report_type.in_(["newspaper_snapshot", "youtube_snapshot", "kiwoom_snapshot"]))
             .filter(OrchReport.created_at < cutoff))
        n = q.count()
        q.delete(synchronize_session=False)
        db.commit()
        return n
    except Exception as e:
        log.warning(f"cleanup_old_snapshots: {str(e)[:80]}")
        return 0
