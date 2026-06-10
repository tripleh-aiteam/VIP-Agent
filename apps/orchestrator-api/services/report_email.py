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


def is_configured() -> bool:
    return bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_USER") and os.getenv("SMTP_PASSWORD"))


def send_email_with_docx(
    to_email: str,
    subject: str,
    body_text: str,
    filename: str,
    docx_bytes: bytes,
) -> dict:
    """Send `body_text` with a .docx attachment. Returns {ok, to, reason?}."""
    if not is_configured():
        return {"ok": False, "reason": "SMTP not configured — set SMTP_HOST/SMTP_USER/SMTP_PASSWORD"}
    if not to_email:
        return {"ok": False, "reason": "no recipient (set KIWOOM_REPORT_EMAIL or REPORT_EMAIL_TO)"}

    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "587") or "587")
    user = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASSWORD", "")
    from_name = os.getenv("SMTP_FROM_NAME", "VIP AI Platform")
    use_tls = os.getenv("SMTP_USE_TLS", "1") == "1"

    msg = EmailMessage()
    msg["From"] = f"{from_name} <{user}>"
    msg["To"] = to_email
    msg["Subject"] = subject or "(no subject)"
    msg.set_content(body_text or "")
    msg.add_attachment(
        docx_bytes, maintype="application", subtype=_DOCX_MIME, filename=filename,
    )

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=25) as smtp:
            if use_tls:
                smtp.starttls(context=ctx)
            smtp.login(user, password)
            smtp.send_message(msg)
        log.info(f"report_email: sent to {to_email} ({filename})", extra={"action": "report_email.sent"})
        return {"ok": True, "to": to_email}
    except Exception as e:
        log.warning(f"report_email: send failed: {e}")
        return {"ok": False, "reason": str(e)[:200]}
