"""
autonomous_alerts — agents proactively watch their OWN live data and alert each
other (and the boss) when something crosses a threshold, on a schedule — NOT
only when a report runs. This is the "agents actually talk to each other"
behaviour: e.g. Stock Agent detects a market drop and warns Asset Agent.

Each alert:
  - sends a REAL A2A message (sender → target) → shows in the A2A Monitor,
  - posts a dashboard/bell notification,
  - (warning/critical) pings the boss on Telegram.

De-duplicated in-process (TTL) so the same condition doesn't spam every run.
Uses live data WITHOUT creating new tasks (snapshot / latest report / OnBid),
so it doesn't flood the task/judgement tables.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from db.base import SessionLocal
from services.logger import log

_SENT: dict[str, float] = {}          # alert_key -> last-sent epoch
_DEDUP_TTL = 6 * 3600                  # don't repeat the same alert within 6h


def _should_send(key: str) -> bool:
    now = time.time()
    if now - _SENT.get(key, 0) < _DEDUP_TTL:
        return False
    _SENT[key] = now
    return True


def _to_num(v: Any) -> float:
    try:
        return float(str(v).replace(",", "").replace("원", "").replace("%", "").strip())
    except Exception:
        return 0.0


def _emit(db, trace_id, sender, target, purpose, payload, title, body, severity="info"):
    """Send the A2A message + dashboard notification + (high sev) Telegram.
    Uses message_type='risk_alert' and a valid purpose; the human description
    rides in the payload. Sender/target must be REAL agents."""
    try:
        from services import a2a_service
        a2a_service.send_message(
            db, trace_id=trace_id, sender_agent_id=sender, target_agent_id=target,
            message_type="risk_alert",
            purpose=("escalate" if severity in ("warning", "critical") else "inform"),
            payload={**payload, "summary": purpose, "title": title, "body": body,
                     "severity": severity},
        )
    except Exception as e:
        log.warning(f"autonomous alert send_message failed: {e}")
    try:
        from services.a2a_notifications import _store_dashboard_notification
        _store_dashboard_notification(
            "agent_alert", title, body, trace_id, severity,
            metadata={"sender": sender, "target": target},
        )
    except Exception as e:
        log.warning(f"autonomous alert notification failed: {e}")
    if severity in ("warning", "critical"):
        try:
            from services.telegram_service import send_alert
            send_alert(f"⚠️ <b>{title}</b>\n{body}\n<i>{sender} → {target}</i>")
        except Exception:
            pass
    log.info(f"autonomous alert: {title} ({sender} -> {target})",
             extra={"trace_id": trace_id, "action": "auto_alert.sent"})


# ---------------------------------------------------------------------------
#  Per-agent watchers (live data, no new tasks)
# ---------------------------------------------------------------------------

def _check_stock(db, trace_id: str) -> None:
    """Stock Agent watches the market; warns Asset Agent / VIP on big moves."""
    try:
        from services.agent_report_builder import _latest_snapshot
        snap = _latest_snapshot()
    except Exception as e:
        log.warning(f"auto_alert stock snapshot failed: {e}")
        return
    if not snap:
        return
    ch = snap.get("changes", {}) or {}
    pr = snap.get("prices", {}) or {}
    day = datetime.utcnow().strftime("%Y%m%d")

    for key, label in (("kospi", "KOSPI"), ("kosdaq", "KOSDAQ")):
        c = _to_num(ch.get(key))
        if c <= -2 and _should_send(f"stock-{key}-down-{day}"):
            _emit(db, trace_id, "Stock Agent", "Asset Agent",
                  purpose=f"{label} dropped {c:.2f}% — review market/asset exposure",
                  payload={"alert": "market_drop", "index": label, "change_pct": c, "value": pr.get(key)},
                  title=f"📉 {label} down {c:.2f}%",
                  body=f"{label} at {pr.get(key)} ({c:+.2f}%). Stock Agent flagged market risk to Asset Agent.",
                  severity="warning")

    for key, label in (("samsung", "삼성전자"), ("skhynix", "SK하이닉스")):
        c = _to_num(ch.get(key))
        if c <= -5 and _should_send(f"stock-{key}-drop-{day}"):
            _emit(db, trace_id, "Stock Agent", "Asset Agent",
                  purpose=f"{label} dropped {c:.2f}% today",
                  payload={"alert": "stock_drop", "stock": label, "change_pct": c, "price": pr.get(key)},
                  title=f"⚠️ {label} {c:.2f}%",
                  body=f"{label} fell {c:.2f}% to {pr.get(key)}원. Stock Agent alerted the boss.",
                  severity="warning")


def _check_asset(db, trace_id: str) -> None:
    """Asset Agent surfaces overdue/expiring alerts (from its latest report) to VIP."""
    try:
        from db.models import OrchReport
        row = (db.query(OrchReport)
               .filter(OrchReport.report_type == "agent_daily_asset")
               .order_by(OrchReport.created_at.desc()).first())
    except Exception as e:
        log.warning(f"auto_alert asset query failed: {e}")
        return
    if not row or not isinstance(row.content_json, dict):
        return
    rep = row.content_json.get("report") or {}
    day = datetime.utcnow().strftime("%Y%m%d")
    for a in (rep.get("alerts") or [])[:3]:
        if _should_send(f"asset-{a[:32]}-{day}"):
            _emit(db, trace_id, "Asset Agent", "Stock Agent",
                  purpose=a, payload={"alert": "asset", "detail": a},
                  title="🏢 Asset alert", body=a, severity="warning")


def _check_realty(db, trace_id: str) -> None:
    """Real Estate Agent flags a notable OnBid (공매) opportunity to VIP."""
    try:
        from services.onbid_tools import tool_onbid_search
        ob = tool_onbid_search(category="real estate", sort="expensive", limit=1)
    except Exception as e:
        log.warning(f"auto_alert realty onbid failed: {e}")
        return
    items = ob.get("items") or []
    if not items:
        return
    it = items[0]
    key = f"realty-onbid-{it.get('id') or it.get('name', '')[:24]}"
    if _should_send(key):
        _emit(db, trace_id, "Real Estate Agent", "Asset Agent",
              purpose=f"OnBid opportunity: {it.get('name', '')[:40]}",
              payload={"alert": "onbid", "item": it},
              title="🏠 OnBid opportunity",
              body=f"{(it.get('address') or it.get('name') or '')[:44]} — 최저 {it.get('min_bid', '?')} (감정 {it.get('appraisal', '?')})",
              severity="info")


def run_autonomous_alerts():
    """Scheduled entry — run all agent watchers. Best-effort; never raises."""
    db = SessionLocal()
    trace_id = f"tr-auto-alert-{int(datetime.utcnow().timestamp())}"
    try:
        for fn in (_check_stock, _check_asset, _check_realty):
            try:
                fn(db, trace_id)
            except Exception as e:
                log.warning(f"autonomous_alerts {fn.__name__} failed: {e}")
        db.commit()
    except Exception as e:
        log.warning(f"autonomous_alerts run failed: {e}")
    finally:
        db.close()
