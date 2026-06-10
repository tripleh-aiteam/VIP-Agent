"""
report_email — send a report by email with a .docx attachment.

Uses the same SMTP env block as twin_meeting_email (pure stdlib smtplib):
  SMTP_HOST       default smtp.gmail.com
  SMTP_PORT       default 587
  SMTP_USER       sender email address
  SMTP_PASSWORD   app password (NOT the account password)
  SMTP_FROM_NAME  default "VIP AI Platform"
  SMTP_USE_TLS    default "1"

If SMTP is unset this is a no-op that returns ok=False with a reason — the
report itself is still saved + sent to Telegram regardless of email delivery.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage

from services.logger import log

_DOCX_MIME = "vnd.openxmlformats-officedocument.wordprocessingml.document"

# Default recipient for report emails when no env override is set. Change here
# (or override per-report via KIWOOM_REPORT_EMAIL / NEWSPAPER_REPORT_EMAIL /
# YOUTUBE_REPORT_EMAIL on Render) when the real recipient is decided.
DEFAULT_RECIPIENT = "davronbekmalikov96@gmail.com"


def sender_address() -> str:
    """The 'From' address. Accepts SMTP_USER or SMTP_EMAIL (some deploys named
    it SMTP_EMAIL)."""
    return os.getenv("SMTP_USER") or os.getenv("SMTP_EMAIL") or ""


def smtp_host() -> str:
    """SMTP host; defaults to Gmail when unset (the common case here)."""
    return os.getenv("SMTP_HOST") or "smtp.gmail.com"


def is_configured() -> bool:
    # Host has a safe default, so we only require a sender + password.
    return bool(sender_address() and os.getenv("SMTP_PASSWORD"))


def send_email_with_docs(
    to_email: str,
    subject: str,
    body_text: str,
    files: list[tuple[str, bytes]],
) -> dict:
    """Send `body_text` with one or more .docx attachments. `files` is a list of
    (filename, bytes). Returns {ok, to, reason?}."""
    if not is_configured():
        return {"ok": False, "reason": "SMTP not configured — set SMTP_EMAIL (or SMTP_USER) + SMTP_PASSWORD"}
    if not to_email:
        return {"ok": False, "reason": "no recipient (set KIWOOM_REPORT_EMAIL or REPORT_EMAIL_TO)"}
    files = [f for f in (files or []) if f and f[1]]
    if not files:
        return {"ok": False, "reason": "no attachments"}

    host = smtp_host()
    port = int(os.getenv("SMTP_PORT", "587") or "587")
    user = sender_address()
    password = os.getenv("SMTP_PASSWORD", "")
    from_name = os.getenv("SMTP_FROM_NAME", "VIP AI Platform")
    use_tls = os.getenv("SMTP_USE_TLS", "1") == "1"

    msg = EmailMessage()
    msg["From"] = f"{from_name} <{user}>"
    msg["To"] = to_email
    msg["Subject"] = subject or "(no subject)"
    msg.set_content(body_text or "")
    for filename, docx_bytes in files:
        msg.add_attachment(docx_bytes, maintype="application", subtype=_DOCX_MIME, filename=filename)

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            if use_tls:
                smtp.starttls(context=ctx)
            smtp.login(user, password)
            smtp.send_message(msg)
        names = ", ".join(f[0] for f in files)
        log.info(f"report_email: sent to {to_email} ({names})", extra={"action": "report_email.sent"})
        return {"ok": True, "to": to_email}
    except Exception as e:
        log.warning(f"report_email: send failed: {e}")
        return {"ok": False, "reason": str(e)[:200]}


def send_email_with_docx(
    to_email: str,
    subject: str,
    body_text: str,
    filename: str,
    docx_bytes: bytes,
) -> dict:
    """Single-attachment wrapper around send_email_with_docs."""
    return send_email_with_docs(to_email, subject, body_text, [(filename, docx_bytes)])
