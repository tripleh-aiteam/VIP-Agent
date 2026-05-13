"""
VIP AI Platform — Meeting Email Sender (Sprint 10)
Sends bilingual (Korean + English) meeting summary emails to participants
of a hybrid meeting room. Pure stdlib smtplib so no new pip dependency.

Config via env:
  SMTP_HOST             default smtp.gmail.com
  SMTP_PORT             default 587
  SMTP_USER             sender email
  SMTP_PASSWORD         app password
  SMTP_FROM_NAME        default "VIP AI Platform"
  SMTP_USE_TLS          default "1"

If SMTP_HOST is unset the sender is a no-op and returns ok=False with a
'reason' — the meeting summary itself is still saved to the twin's
knowledge base regardless of email delivery.
"""

from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from services.logger import log


def is_configured() -> bool:
    return bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_USER") and os.getenv("SMTP_PASSWORD"))


def send_meeting_summary_email(
    to_email: str,
    to_name: str,
    meeting_title: str,
    english_summary: str,
    korean_summary: str,
    action_items: Optional[list] = None,
    meeting_link: Optional[str] = None,
) -> dict:
    """Send a single bilingual summary email. Returns delivery result dict."""
    if not is_configured():
        return {
            "ok": False,
            "to": to_email,
            "reason": "SMTP not configured — set SMTP_HOST/USER/PASSWORD env vars",
        }

    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASSWORD", "")
    from_name = os.getenv("SMTP_FROM_NAME", "VIP AI Platform")
    use_tls = os.getenv("SMTP_USE_TLS", "1") == "1"

    subject = f"[VIP] Meeting Summary — {meeting_title}"

    actions_html = ""
    actions_text = ""
    if action_items:
        items = [str(a) for a in action_items if a]
        actions_html = (
            "<h3>Action Items / 실행 항목</h3><ul>"
            + "".join(f"<li>{i}</li>" for i in items)
            + "</ul>"
        )
        actions_text = "\n\nAction Items / 실행 항목:\n" + "\n".join(f"- {i}" for i in items)

    link_block = (
        f'<p><a href="{meeting_link}">Open meeting room / 회의실 열기</a></p>'
        if meeting_link else ""
    )

    html_body = f"""
    <html>
      <body style="font-family: -apple-system, sans-serif; max-width: 640px;">
        <h2>{meeting_title}</h2>
        <p>Hello {to_name},</p>
        <p>Your meeting has ended. Below is the bilingual summary.</p>

        <h3>English Summary</h3>
        <pre style="white-space: pre-wrap; font-family: inherit; background:#f5f5f5; padding:12px; border-radius:6px;">{english_summary}</pre>

        <h3>한국어 요약</h3>
        <pre style="white-space: pre-wrap; font-family: inherit; background:#f5f5f5; padding:12px; border-radius:6px;">{korean_summary}</pre>

        {actions_html}
        {link_block}
        <hr/>
        <p style="color:#999; font-size:12px;">Sent automatically by VIP AI Platform.</p>
      </body>
    </html>
    """

    text_body = (
        f"Hello {to_name},\n\nYour meeting '{meeting_title}' has ended.\n\n"
        f"English Summary:\n{english_summary}\n\n"
        f"한국어 요약:\n{korean_summary}"
        f"{actions_text}"
        f"\n\nVIP AI Platform"
    )

    msg = MIMEMultipart("alternative")
    msg["From"] = f"{from_name} <{user}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(host, port, timeout=30) as server:
            if use_tls:
                server.starttls()
            server.login(user, password)
            server.send_message(msg)
        return {"ok": True, "to": to_email}
    except Exception as e:
        log.warning(f"twin_meeting_email: send to {to_email} failed: {e}")
        return {"ok": False, "to": to_email, "reason": str(e)}
