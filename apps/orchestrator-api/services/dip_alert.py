"""dip_alert.py — proactive dip-bounce alerts: the bot GUIDES instead of waiting.

Every ~10 min during market hours the scheduler runs the dip-bounce scan; when a NEW
candidate passes (≥1.5%/1h dip + tape confirm), the boss gets an email with the entry/
target/stop plan. Guardrails: per-ticker dedup window (2h) and a daily email cap, both
DB-backed (Render restarts wipe module globals — see the breaking-news cap bug).
Pilot: owner-only recipient until sign-off (same policy as the recommendation report).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from services.logger import log

KST = timezone(timedelta(hours=9))
DEDUP_HOURS = 2          # one alert per ticker per window
DAILY_EMAIL_CAP = 5      # max alert emails per day

_DDL = """
CREATE TABLE IF NOT EXISTS trading_alerts (
    id         BIGSERIAL PRIMARY KEY,
    ts         TIMESTAMPTZ DEFAULT now(),
    kind       TEXT NOT NULL,
    ticker     TEXT NOT NULL,
    dip_pct    NUMERIC,
    entry      NUMERIC,
    emailed    BOOLEAN DEFAULT FALSE
)
"""


def _recipients() -> list[str]:
    env = os.getenv("DIP_ALERT_RECIPIENTS") or ""
    if env.strip():
        return [r.strip() for r in env.split(",") if r.strip()]
    return ["davronbekmalikov96@gmail.com"]          # pilot: owner-only


def _market_open_now() -> bool:
    n = datetime.now(KST)
    return n.weekday() < 5 and (9 * 60 + 5) <= (n.hour * 60 + n.minute) <= (15 * 60 + 15)


def run(db, force: bool = False) -> dict[str, Any]:
    """One alert pass. Returns {checked, new_alerts, emailed}."""
    if not force and not _market_open_now():
        return {"skipped": "market closed"}
    try:
        db.execute(text(_DDL))
        db.commit()
    except Exception:
        db.rollback()

    from services.dip_bounce import scan
    r = scan(db, log_calls=True)                      # alerts ARE recommendations → graded
    cands = r.get("candidates") or []
    if r.get("market_plunge") or not cands:
        return {"checked": len(cands), "new_alerts": 0,
                "note": "plunge day" if r.get("market_plunge") else "no candidates"}

    # DB-backed dedup: only tickers not alerted within the window
    fresh = []
    for c in cands:
        seen = db.execute(text(
            "SELECT 1 FROM trading_alerts WHERE kind='dip_bounce' AND ticker=:t "
            "AND ts > now() - (:h || ' hours')::interval LIMIT 1"),
            {"t": c["ticker"], "h": DEDUP_HOURS}).first()
        if not seen:
            fresh.append(c)
    if not fresh:
        return {"checked": len(cands), "new_alerts": 0, "note": "all recently alerted"}

    for c in fresh:
        db.execute(text(
            "INSERT INTO trading_alerts (kind, ticker, dip_pct, entry) "
            "VALUES ('dip_bounce', :t, :d, :e)"),
            {"t": c["ticker"], "d": c["dip_pct"], "e": c["cur"]})
    db.commit()

    # daily email cap (DB-counted, restart-proof)
    sent_today = db.execute(text(
        "SELECT count(*) FROM trading_alerts WHERE kind='dip_bounce' AND emailed "
        "AND ts::date = (now() AT TIME ZONE 'Asia/Seoul')::date")).scalar() or 0
    emailed = False
    if sent_today < DAILY_EMAIL_CAP:
        try:
            from services.report_email import send_plain_email
            from services.stock_resolver import display_name
            names = ", ".join(display_name(c["ticker"]) for c in fresh)
            res = send_plain_email(_recipients(),
                                   f"📉→📈 반등 후보 알림: {names}",
                                   r.get("reasoning_ko") or "")
            emailed = bool(res.get("ok"))
            if emailed:
                db.execute(text(
                    "UPDATE trading_alerts SET emailed=TRUE WHERE kind='dip_bounce' "
                    "AND ticker = ANY(:ts) AND ts > now() - interval '5 minutes'"),
                    {"ts": [c["ticker"] for c in fresh]})
                db.commit()
        except Exception as e:
            log.warning(f"dip_alert email: {str(e)[:120]}")
    else:
        log.info("dip_alert: daily email cap reached — logged without email",
                 extra={"action": "dip_alert.capped"})
    log.info(f"dip_alert: {len(fresh)} new candidate(s), emailed={emailed}",
             extra={"action": "dip_alert.fired"})
    return {"checked": len(cands), "new_alerts": len(fresh), "emailed": emailed}
