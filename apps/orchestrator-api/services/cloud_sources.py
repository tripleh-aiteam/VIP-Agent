"""
VIP AI Platform — Cloud learning sources (server-side auto-pull).

After a worker clicks "Connect" once (OAuth), the server pulls their cloud work
automatically on a schedule — no capture client, no terminal. Sources:

  • Google Drive     — recent docs/sheets/text          → [google-drive]
  • Google Calendar  — upcoming/recent events            → [google-calendar]
  • Gmail            — recent sent mail (the worker's own writing/style) → [gmail]
  • Notion           — pages shared with the integration → [notion]

Everything is gated by the twin's learning_consent at pull time, distilled by
watch_learn (so it's tagged + embedded), and kept private to the worker's twin.

Dormant until configured: needs GOOGLE_CLIENT_ID/SECRET (Google) and/or
NOTION_CLIENT_SECRET (Notion). Safe to deploy with no keys — it simply no-ops.
"""

import os
import time
import hmac
import base64
import hashlib
import secrets
from datetime import datetime, timedelta
from uuid import UUID
from typing import Optional

from sqlalchemy.orm import Session

from db.models import DigitalTwin, OAuthConnection
from services import watch_learn
from services.logger import log

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv(
    "GOOGLE_REDIRECT_URI",
    "https://vip-orchestrator.onrender.com/twins/oauth/google/callback")
NOTION_SECRET = os.getenv("NOTION_CLIENT_SECRET", "") or os.getenv("NOTION_SECRET", "")

GOOGLE_SCOPES = " ".join([
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/calendar.events",   # read + create events
    "https://www.googleapis.com/auth/gmail.readonly",
    "openid", "email",
])
MAX_PULL_CHARS = 18000


def google_configured() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def notion_configured() -> bool:
    return bool(NOTION_SECRET)


def provider_status() -> dict:
    return {"google": google_configured(), "notion": notion_configured()}


# --------------------------------------------------------------------------- #
#  Google OAuth
# --------------------------------------------------------------------------- #
def _state_secret() -> bytes:
    # Server-only secret. GOOGLE_CLIENT_SECRET is a fine HMAC key (never leaves
    # the server); fall back to SECRET_KEY so signing still works if needed.
    return (GOOGLE_CLIENT_SECRET or os.getenv("SECRET_KEY", "vip-twins-state-key")).encode()


def make_state(twin_id: str, ttl_seconds: int = 600) -> str:
    """Sign an expiring state token for `twin_id`. Only callers that know the
    server secret (i.e. the owner-gated auth-url endpoint) can mint a valid one,
    so the public callback never trusts a raw twin_id from the URL."""
    nonce = secrets.token_urlsafe(9)
    exp = str(int(time.time()) + ttl_seconds)
    msg = f"{twin_id}:{nonce}:{exp}"
    sig = hmac.new(_state_secret(), msg.encode(), hashlib.sha256).hexdigest()[:32]
    return base64.urlsafe_b64encode(f"{msg}:{sig}".encode()).decode()


def verify_state(state: str) -> Optional[str]:
    """Return the twin_id from a valid, unexpired state, else None."""
    try:
        raw = base64.urlsafe_b64decode(state.encode()).decode()
        twin_id, nonce, exp, sig = raw.split(":")
        msg = f"{twin_id}:{nonce}:{exp}"
        good = hmac.new(_state_secret(), msg.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(good, sig):
            return None
        if int(exp) < int(time.time()):
            return None
        return twin_id
    except Exception:
        return None


def google_auth_url(twin_id: str) -> str:
    from urllib.parse import urlencode
    q = urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": GOOGLE_SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "state": make_state(str(twin_id)),   # signed + expiring, not a raw twin_id
    })
    return f"https://accounts.google.com/o/oauth2/v2/auth?{q}"


def google_exchange_and_store(db: Session, code: str, twin_id: str) -> bool:
    """Exchange the auth code for tokens and upsert an OAuthConnection."""
    import httpx
    try:
        resp = httpx.post("https://oauth2.googleapis.com/token", data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
        }, timeout=30)
        if resp.status_code != 200:
            log.warning(f"google_exchange: {resp.status_code} {resp.text[:200]}")
            return False
        tok = resp.json()
        email = ""
        try:
            ui = httpx.get("https://www.googleapis.com/oauth2/v2/userinfo",
                           headers={"Authorization": f"Bearer {tok.get('access_token')}"}, timeout=15)
            if ui.status_code == 200:
                email = ui.json().get("email", "")
        except Exception:
            pass
        _upsert_connection(
            db, twin_id, "google",
            access_token=tok.get("access_token"),
            refresh_token=tok.get("refresh_token"),
            scopes=GOOGLE_SCOPES,
            expires_in=tok.get("expires_in"),
            connected_email=email,
        )
        return True
    except Exception as e:
        log.error(f"google_exchange failed: {e}")
        return False


def _upsert_connection(db: Session, twin_id, provider: str, *, access_token=None,
                       refresh_token=None, scopes=None, expires_in=None, connected_email=None):
    conn = (db.query(OAuthConnection)
            .filter(OAuthConnection.twin_id == twin_id, OAuthConnection.provider == provider)
            .first())
    if not conn:
        conn = OAuthConnection(twin_id=twin_id, provider=provider)
        db.add(conn)
    if access_token:
        conn.access_token = access_token
    if refresh_token:                    # Google only returns this on first consent
        conn.refresh_token = refresh_token
    if scopes:
        conn.scopes = scopes
    if connected_email:
        conn.connected_email = connected_email
    if expires_in:
        conn.expires_at = datetime.utcnow() + timedelta(seconds=int(expires_in) - 60)
    conn.status = "active"
    db.commit()
    return conn


def _google_valid_token(db: Session, conn: OAuthConnection) -> Optional[str]:
    """Return a usable access token, refreshing if expired."""
    if conn.access_token and conn.expires_at and conn.expires_at > datetime.utcnow():
        return conn.access_token
    if not conn.refresh_token:
        return conn.access_token
    import httpx
    try:
        resp = httpx.post("https://oauth2.googleapis.com/token", data={
            "refresh_token": conn.refresh_token,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "grant_type": "refresh_token",
        }, timeout=30)
        if resp.status_code == 200:
            tok = resp.json()
            conn.access_token = tok.get("access_token")
            conn.expires_at = datetime.utcnow() + timedelta(seconds=int(tok.get("expires_in", 3600)) - 60)
            conn.status = "active"
            db.commit()
            return conn.access_token
        conn.status = "error"; db.commit()
    except Exception as e:
        log.warning(f"google refresh failed: {e}")
    return None


# --------------------------------------------------------------------------- #
#  Pull functions  (each returns raw text; ingestion is done by pull_twin)
# --------------------------------------------------------------------------- #
def _google_drive_text(token: str) -> str:
    import httpx
    week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat() + "Z"
    try:
        r = httpx.get("https://www.googleapis.com/drive/v3/files",
                      headers={"Authorization": f"Bearer {token}"},
                      params={"q": f"modifiedTime > '{week_ago}' and (mimeType contains 'document' or mimeType contains 'text' or mimeType contains 'spreadsheet')",
                              "fields": "files(id,name,mimeType)", "orderBy": "modifiedTime desc", "pageSize": 8}, timeout=30)
        if r.status_code != 200:
            return ""
        out = []
        for f in r.json().get("files", []):
            mime = f.get("mimeType", "")
            try:
                if "google-apps" in mime:
                    em = "text/csv" if "spreadsheet" in mime else "text/plain"
                    c = httpx.get(f"https://www.googleapis.com/drive/v3/files/{f['id']}/export",
                                  headers={"Authorization": f"Bearer {token}"}, params={"mimeType": em}, timeout=30)
                else:
                    c = httpx.get(f"https://www.googleapis.com/drive/v3/files/{f['id']}",
                                  headers={"Authorization": f"Bearer {token}"}, params={"alt": "media"}, timeout=30)
                if c.status_code == 200 and c.text.strip():
                    out.append(f"# {f.get('name','doc')}\n{c.text[:3000]}")
            except Exception:
                continue
        return "\n\n".join(out)[:MAX_PULL_CHARS]
    except Exception as e:
        log.warning(f"drive pull: {e}")
        return ""


def _google_calendar_text(token: str) -> str:
    import httpx
    now = datetime.utcnow().isoformat() + "Z"
    nxt = (datetime.utcnow() + timedelta(days=14)).isoformat() + "Z"
    try:
        r = httpx.get("https://www.googleapis.com/calendar/v3/calendars/primary/events",
                      headers={"Authorization": f"Bearer {token}"},
                      params={"timeMin": now, "timeMax": nxt, "singleEvents": "true",
                              "orderBy": "startTime", "maxResults": 25}, timeout=30)
        if r.status_code != 200:
            return ""
        lines = []
        for ev in r.json().get("items", []):
            start = (ev.get("start", {}) or {}).get("dateTime") or (ev.get("start", {}) or {}).get("date", "")
            summ = ev.get("summary", "(no title)")
            who = ", ".join(a.get("email", "") for a in ev.get("attendees", []) if a.get("email"))
            lines.append(f"- {start}: {summ}" + (f" (with {who})" if who else ""))
        return ("Upcoming meetings/events:\n" + "\n".join(lines))[:MAX_PULL_CHARS] if lines else ""
    except Exception as e:
        log.warning(f"calendar pull: {e}")
        return ""


def _gmail_text(token: str) -> str:
    """Recent SENT mail — the worker's own writing/voice/decisions."""
    import httpx
    try:
        lst = httpx.get("https://gmail.googleapis.com/gmail/v1/users/me/messages",
                        headers={"Authorization": f"Bearer {token}"},
                        params={"q": "in:sent newer_than:7d", "maxResults": 8}, timeout=30)
        if lst.status_code != 200:
            return ""
        out = []
        for m in lst.json().get("messages", []):
            try:
                d = httpx.get(f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{m['id']}",
                              headers={"Authorization": f"Bearer {token}"},
                              params={"format": "metadata", "metadataHeaders": ["Subject"]}, timeout=20)
                if d.status_code == 200:
                    snip = d.json().get("snippet", "")
                    subj = ""
                    for h in (d.json().get("payload", {}) or {}).get("headers", []):
                        if h.get("name") == "Subject":
                            subj = h.get("value", "")
                    if snip:
                        out.append(f"Sent — {subj}: {snip}")
            except Exception:
                continue
        return "\n".join(out)[:MAX_PULL_CHARS]
    except Exception as e:
        log.warning(f"gmail pull: {e}")
        return ""


def _notion_text() -> str:
    """Pages shared with the integration (single workspace secret)."""
    import httpx
    if not notion_configured():
        return ""
    try:
        r = httpx.post("https://api.notion.com/v1/search",
                       headers={"Authorization": f"Bearer {NOTION_SECRET}",
                                "Notion-Version": "2022-06-28", "Content-Type": "application/json"},
                       json={"page_size": 10, "sort": {"direction": "descending", "timestamp": "last_edited_time"}}, timeout=30)
        if r.status_code != 200:
            return ""
        out = []
        for res in r.json().get("results", []):
            props = res.get("properties", {})
            title = ""
            for v in props.values():
                if v.get("type") == "title":
                    title = "".join(t.get("plain_text", "") for t in v.get("title", []))
                    break
            if title:
                out.append(f"- {title}")
        return ("Notion pages:\n" + "\n".join(out))[:MAX_PULL_CHARS] if out else ""
    except Exception as e:
        log.warning(f"notion pull: {e}")
        return ""


# --------------------------------------------------------------------------- #
#  Orchestration
# --------------------------------------------------------------------------- #
def pull_twin(db: Session, twin_id: UUID) -> dict:
    """Pull all connected sources for one twin. Consent-gated. Returns counts."""
    twin = db.query(DigitalTwin).filter(DigitalTwin.id == twin_id).first()
    if not twin:
        return {"ok": False, "error": "twin not found"}
    if not getattr(twin, "learning_consent", False):
        return {"ok": False, "error": "no consent"}

    learned = {}
    conns = db.query(OAuthConnection).filter(
        OAuthConnection.twin_id == twin_id, OAuthConnection.status == "active").all()
    for conn in conns:
        if conn.provider == "google" and google_configured():
            token = _google_valid_token(db, conn)
            if not token:
                continue
            for src, fn in (("google-drive", _google_drive_text),
                            ("google-calendar", _google_calendar_text),
                            ("gmail", _gmail_text)):
                text = fn(token)
                if text and len(text) > 40:
                    res = watch_learn.observe_and_learn(db, twin_id, text, source=src, kind="cloud_pull")
                    learned[src] = res.get("learned", 0)
        elif conn.provider == "notion" and notion_configured():
            text = _notion_text()
            if text and len(text) > 40:
                res = watch_learn.observe_and_learn(db, twin_id, text, source="notion", kind="cloud_pull")
                learned["notion"] = res.get("learned", 0)
        conn.last_pull_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "learned": learned}


def create_calendar_event(db: Session, twin_id, summary: str, start_iso: str,
                          end_iso: str, description: str = "", attendees=None) -> dict:
    """Create a Google Calendar event on the twin owner's primary calendar.
    Requires the twin to have a Google connection. Returns {ok, link?, error?}."""
    import httpx
    conn = (db.query(OAuthConnection)
            .filter(OAuthConnection.twin_id == twin_id,
                    OAuthConnection.provider == "google",
                    OAuthConnection.status == "active").first())
    if not conn:
        return {"ok": False, "error": "Google not connected for this twin"}
    token = _google_valid_token(db, conn)
    if not token:
        return {"ok": False, "error": "Google token unavailable"}
    body = {
        "summary": summary or "(no title)",
        "description": description or "",
        "start": {"dateTime": start_iso},
        "end": {"dateTime": end_iso or start_iso},
    }
    if attendees:
        body["attendees"] = [{"email": e} for e in attendees if e]
    try:
        r = httpx.post("https://www.googleapis.com/calendar/v3/calendars/primary/events",
                       headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                       json=body, timeout=30)
        if r.status_code in (200, 201):
            return {"ok": True, "link": r.json().get("htmlLink", "")}
        return {"ok": False, "error": f"calendar API {r.status_code}: {r.text[:160]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:160]}


def pull_all_due(db: Session) -> dict:
    """Scheduler entry — pull for every twin that has consent + a connection."""
    if not (google_configured() or notion_configured()):
        return {"ok": True, "skipped": "no providers configured"}
    twin_ids = [c.twin_id for c in db.query(OAuthConnection.twin_id)
                .filter(OAuthConnection.status == "active").distinct()]
    total = 0
    for tid in twin_ids:
        try:
            r = pull_twin(db, tid)
            total += sum((r.get("learned") or {}).values())
        except Exception as e:
            log.warning(f"cloud pull failed for {tid}: {e}")
    return {"ok": True, "twins": len(twin_ids), "learned": total}
