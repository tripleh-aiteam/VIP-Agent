"""
chatbot_email_client — Email channel for the chatbot inbox.

Treats an email thread as a Conversation:
  - From-address → Customer (created on first contact, matched by email)
  - Subject thread → Conversation (matched by RFC In-Reply-To / References,
    falling back to normalized subject)
  - Each inbound email becomes a Message(kind="text", author="customer")
  - Bot replies are sent via SMTP, persisted as Message(author="bot")

Two transports are abstracted behind plain functions so we can swap
Gmail OAuth in later without touching the reply pipeline:
  - poll_inbox(agent_id) → list[InboundEmail]      (IMAP today, Gmail
    Watch + push notifications when OAuth lands)
  - send_email(agent_id, to, subject, body, in_reply_to=...) → message_id

Configuration (one .env block per agent — eventually move into a DB row
on chatbot_agent_settings):
  CHATBOT_EMAIL_<AGENT>_IMAP_HOST      (e.g. imap.gmail.com)
  CHATBOT_EMAIL_<AGENT>_IMAP_PORT      (default 993)
  CHATBOT_EMAIL_<AGENT>_SMTP_HOST      (e.g. smtp.gmail.com)
  CHATBOT_EMAIL_<AGENT>_SMTP_PORT      (default 587)
  CHATBOT_EMAIL_<AGENT>_USERNAME       (full email address)
  CHATBOT_EMAIL_<AGENT>_PASSWORD       (app password — NOT account password)
  CHATBOT_EMAIL_<AGENT>_FROM_NAME      (display name on outbound mail)

Polling runs via a scheduler tick (every 2 min by default) — see
scheduler_service.py. Each poll diffs UID > last_seen and ingests new
messages through chatbot_reply_service.handle_incoming_message.

Why email at all: same agent, more surface area. Customers who don't use
KakaoTalk still email about listings — and Korean contract paperwork
attached as PDFs lands here, not on KakaoTalk. The bot learns + replies
the same way regardless of channel."""

from __future__ import annotations

import asyncio
import email as _email
import imaplib
import os
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.header import decode_header
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr, parsedate_to_datetime
from typing import Iterable, Optional

from services.logger import log


# ============================================================================
#  Config — per-agent env var lookup
# ============================================================================

def _env(agent_id: str, suffix: str, default: Optional[str] = None) -> Optional[str]:
    """Per-agent env var: CHATBOT_EMAIL_<AGENT>_<SUFFIX>. Falls back to
    the global default CHATBOT_EMAIL_<SUFFIX> so single-tenant deploys
    can skip the agent prefix."""
    a = (agent_id or "").upper().replace("-", "_")
    return (
        os.getenv(f"CHATBOT_EMAIL_{a}_{suffix}")
        or os.getenv(f"CHATBOT_EMAIL_{suffix}")
        or default
    )


def is_configured(agent_id: str) -> bool:
    return bool(
        _env(agent_id, "USERNAME")
        and _env(agent_id, "PASSWORD")
        and _env(agent_id, "IMAP_HOST")
    )


# ============================================================================
#  Inbound — IMAP polling
# ============================================================================

@dataclass
class InboundEmail:
    """One newly-arrived email, normalized into something the reply
    pipeline can consume without knowing what IMAP is."""
    uid: str
    message_id: str
    in_reply_to: Optional[str]
    references: list[str]
    from_email: str
    from_name: Optional[str]
    subject: str
    body_text: str
    received_at_ms: int


def poll_inbox(agent_id: str, *, since_uid: Optional[int] = None) -> list[InboundEmail]:
    """Fetch new UNSEEN messages (or UID > since_uid) and return them
    normalized. Marks fetched messages as Seen so the next tick doesn't
    re-deliver them.

    Synchronous — IMAP libs are stdlib + sync. The scheduler runs this
    inside `run_in_executor` so it doesn't block the event loop."""
    if not is_configured(agent_id):
        return []

    host = _env(agent_id, "IMAP_HOST")
    port = int(_env(agent_id, "IMAP_PORT", "993") or "993")
    user = _env(agent_id, "USERNAME")
    password = _env(agent_id, "PASSWORD")

    try:
        with imaplib.IMAP4_SSL(host, port) as imap:
            imap.login(user, password)            # type: ignore[arg-type]
            imap.select("INBOX")
            if since_uid is not None:
                typ, data = imap.uid("search", None, f"UID {since_uid + 1}:*")
            else:
                typ, data = imap.uid("search", None, "UNSEEN")
            if typ != "OK":
                return []
            uids = (data[0] or b"").split()
            if not uids:
                return []

            inbound: list[InboundEmail] = []
            for uid in uids:
                try:
                    typ, msg_data = imap.uid("fetch", uid, "(RFC822)")
                    if typ != "OK" or not msg_data or not msg_data[0]:
                        continue
                    raw = msg_data[0][1]                # type: ignore[index]
                    if isinstance(raw, bytes):
                        parsed = _email.message_from_bytes(raw)
                        inbound.append(
                            _normalize_email(uid.decode("ascii"), parsed)
                        )
                except Exception as e:
                    log.warning(f"chatbot_email: fetch UID {uid} failed: {e}")
            return inbound
    except Exception as e:
        log.warning(f"chatbot_email: IMAP poll failed for {agent_id}: {e}")
        return []


def _normalize_email(uid: str, msg: _email.message.Message) -> InboundEmail:
    from_name, from_email = parseaddr(msg.get("From", ""))
    subject = _decode_header(msg.get("Subject", ""))
    message_id = msg.get("Message-ID", "").strip() or f"<no-msgid-{uid}@local>"
    in_reply_to = (msg.get("In-Reply-To") or "").strip() or None
    references = [
        ref.strip() for ref in (msg.get("References") or "").split() if ref.strip()
    ]

    body_text = _extract_text_body(msg)

    received_at_ms = 0
    date_hdr = msg.get("Date")
    if date_hdr:
        try:
            dt = parsedate_to_datetime(date_hdr)
            received_at_ms = int(dt.timestamp() * 1000)
        except Exception:
            pass

    return InboundEmail(
        uid=uid,
        message_id=message_id,
        in_reply_to=in_reply_to,
        references=references,
        from_email=from_email.lower(),
        from_name=from_name or None,
        subject=subject,
        body_text=body_text,
        received_at_ms=received_at_ms,
    )


def _decode_header(raw: str) -> str:
    parts = decode_header(raw or "")
    out: list[str] = []
    for text, charset in parts:
        if isinstance(text, bytes):
            try:
                out.append(text.decode(charset or "utf-8", errors="replace"))
            except Exception:
                out.append(text.decode("utf-8", errors="replace"))
        else:
            out.append(text)
    return "".join(out).strip()


def _extract_text_body(msg: _email.message.Message) -> str:
    """Prefer text/plain. Fall back to stripping HTML if only HTML exists."""
    if msg.is_multipart():
        text_part = None
        html_part = None
        for part in msg.walk():
            ctype = part.get_content_type()
            disposition = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disposition:
                continue
            if ctype == "text/plain" and text_part is None:
                text_part = part
            elif ctype == "text/html" and html_part is None:
                html_part = part
        if text_part:
            return _payload_to_str(text_part)
        if html_part:
            return _strip_html(_payload_to_str(html_part))
        return ""
    if msg.get_content_type() == "text/html":
        return _strip_html(_payload_to_str(msg))
    return _payload_to_str(msg)


def _payload_to_str(part: _email.message.Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except Exception:
        return payload.decode("utf-8", errors="replace")


_HTML_TAG = re.compile(r"<[^>]+>")
_HTML_WS = re.compile(r"\s+")


def _strip_html(html: str) -> str:
    stripped = _HTML_TAG.sub(" ", html)
    return _HTML_WS.sub(" ", stripped).strip()


# ============================================================================
#  Outbound — SMTP send
# ============================================================================

def send_email(
    *,
    agent_id: str,
    to_email: str,
    subject: str,
    body_text: str,
    in_reply_to: Optional[str] = None,
    references: Optional[list[str]] = None,
) -> Optional[str]:
    """Send an email via the agent's configured SMTP server. Returns the
    Message-ID of the sent message (for thread tracking) or None on
    failure."""
    if not is_configured(agent_id):
        log.warning(f"chatbot_email: SMTP send skipped — {agent_id} not configured")
        return None

    host = _env(agent_id, "SMTP_HOST", _env(agent_id, "IMAP_HOST", "").replace("imap.", "smtp."))
    port = int(_env(agent_id, "SMTP_PORT", "587") or "587")
    user = _env(agent_id, "USERNAME")
    password = _env(agent_id, "PASSWORD")
    from_name = _env(agent_id, "FROM_NAME") or user

    msg = EmailMessage()
    msg["From"] = formataddr((from_name or "", user or ""))
    msg["To"] = to_email
    msg["Subject"] = subject if subject else "(no subject)"
    msg_id = make_msgid(domain=(user.split("@")[-1] if user and "@" in user else "localhost"))
    msg["Message-ID"] = msg_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        all_refs = list(references or []) + [in_reply_to]
        # Dedupe preserving order
        seen: set[str] = set()
        deduped = [r for r in all_refs if not (r in seen or seen.add(r))]
        msg["References"] = " ".join(deduped)
    msg.set_content(body_text)

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.starttls(context=ctx)
            smtp.login(user, password)            # type: ignore[arg-type]
            smtp.send_message(msg)
        return msg_id
    except Exception as e:
        log.warning(f"chatbot_email: SMTP send failed for {agent_id}: {e}")
        return None


# ============================================================================
#  Async wrappers — scheduler + reply pipeline use these
# ============================================================================

async def poll_inbox_async(
    agent_id: str, *, since_uid: Optional[int] = None
) -> list[InboundEmail]:
    return await asyncio.to_thread(poll_inbox, agent_id, since_uid=since_uid)


async def send_email_async(**kwargs) -> Optional[str]:
    return await asyncio.to_thread(send_email, **kwargs)


# ============================================================================
#  Threading — match inbound to existing conversation
# ============================================================================

_SUBJECT_PREFIX_RE = re.compile(r"^(re|fw|fwd|답장|회신)\s*:\s*", re.IGNORECASE)


def normalize_subject(subject: str) -> str:
    """Strip "Re: ", "Fwd: ", "답장: " etc. so two messages with the same
    underlying subject thread together."""
    s = subject or ""
    while True:
        new = _SUBJECT_PREFIX_RE.sub("", s).strip()
        if new == s:
            break
        s = new
    return s.strip().lower()


def iter_thread_keys(email: InboundEmail) -> Iterable[str]:
    """Yield the keys we'll use to match this email back to an existing
    conversation. Caller looks them up in conversation.thread_keys_json.

    Order matters — message-id chain is most authoritative, normalized
    subject is a fallback for clients that don't preserve headers."""
    yield email.message_id
    if email.in_reply_to:
        yield email.in_reply_to
    for ref in email.references:
        yield ref
    norm = normalize_subject(email.subject)
    if norm:
        yield f"subj:{norm}"
