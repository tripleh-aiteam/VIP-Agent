"""
VIP AI Platform — Auth Service
Login, password change, forgot password with Gmail recovery.
"""

import os
import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from db.models import PlatformUser
from services.logger import log

# Default admin credentials (created on first login)
DEFAULT_ADMIN_EMAIL = os.getenv("VIP_ADMIN_EMAIL", "admin@vip-agent.com")
DEFAULT_ADMIN_PASSWORD = os.getenv("VIP_ADMIN_PASSWORD", "VipBoss2026!")
RESET_TOKEN_HOURS = 24


def _hash_password(password: str) -> str:
    """Simple hash — uses SHA256 with salt. Good enough for single-user."""
    salt = "vip-agent-salt-2026"
    return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()


def _verify_password(password: str, password_hash: str) -> bool:
    return _hash_password(password) == password_hash


def _get_or_create_admin(db: Session) -> PlatformUser:
    """Get admin user or create default one."""
    user = db.query(PlatformUser).filter(PlatformUser.email == DEFAULT_ADMIN_EMAIL).first()
    if not user:
        user = PlatformUser(
            email=DEFAULT_ADMIN_EMAIL,
            name="VIP Admin",
            password_hash=_hash_password(DEFAULT_ADMIN_PASSWORD),
            role="admin",
        )
        db.add(user)
        db.commit()
        log.info(f"auth: created default admin user {DEFAULT_ADMIN_EMAIL}", extra={"action": "auth.admin_created"})
    # If user exists but has no password hash, set default
    if not user.password_hash:
        user.password_hash = _hash_password(DEFAULT_ADMIN_PASSWORD)
        db.commit()
    return user


def login(db: Session, email: str, password: str) -> dict:
    """Login with email + password."""
    user = db.query(PlatformUser).filter(PlatformUser.email == email).first()

    # Also try login with just password (for single-user simplicity)
    if not user:
        user = _get_or_create_admin(db)
        if user.email != email and email != "admin":
            return {"success": False, "error": "User not found"}

    if not _verify_password(password, user.password_hash or ""):
        return {"success": False, "error": "Incorrect password"}

    user.last_login_at = datetime.utcnow()
    db.commit()

    log.info(f"auth: login success for {user.email}", extra={"action": "auth.login"})

    # Include twin info if worker has a linked twin
    twin_id = None
    twin_name = None
    if user.has_twin and user.twin_id:
        twin_id = str(user.twin_id)
        from db.models import DigitalTwin
        twin = db.query(DigitalTwin).filter(DigitalTwin.id == user.twin_id).first()
        if twin:
            twin_name = twin.name

    return {
        "success": True,
        "user": {
            "id": str(user.id),
            "email": user.email,
            "name": user.name,
            "role": user.role,
        },
        "token": _hash_password(f"{user.id}:{user.email}"),  # simple session token
        "name": user.name,
        "twin_id": twin_id,
        "twin_name": twin_name,
    }


def change_password(db: Session, email: str, current_password: str, new_password: str) -> dict:
    """Change password — requires current password."""
    user = db.query(PlatformUser).filter(PlatformUser.email == email).first()
    if not user:
        return {"success": False, "error": "User not found"}

    if not _verify_password(current_password, user.password_hash or ""):
        return {"success": False, "error": "Current password is incorrect"}

    if len(new_password) < 6:
        return {"success": False, "error": "New password must be at least 6 characters"}

    user.password_hash = _hash_password(new_password)
    db.commit()

    log.info(f"auth: password changed for {user.email}", extra={"action": "auth.password_changed"})
    return {"success": True, "message": "Password changed successfully"}


def forgot_password(db: Session, email: str) -> dict:
    """Generate reset token and send recovery email. Returns real send status."""
    email = (email or "").strip().lower()
    user = db.query(PlatformUser).filter(PlatformUser.email == email).first()

    # Auto-seed the configured admin so first-time recovery works
    if not user and email == DEFAULT_ADMIN_EMAIL.lower():
        user = _get_or_create_admin(db)

    if not user:
        return {
            "success": False,
            "email_sent": False,
            "error": "No account with that email",
        }

    token = secrets.token_urlsafe(32)
    user.reset_token = _hash_password(token)
    user.reset_token_expires = datetime.utcnow() + timedelta(hours=RESET_TOKEN_HOURS)
    db.commit()

    email_sent, err = _send_recovery_email(user.email, token)

    if email_sent:
        log.info(f"auth: recovery email sent to {user.email}", extra={"action": "auth.reset_requested"})
        return {
            "success": True,
            "email_sent": True,
            "message": f"Recovery link sent to {user.email}. Check your inbox (and spam).",
        }

    log.warning(f"auth: recovery email FAILED for {user.email}: {err}", extra={"action": "auth.reset_failed"})
    return {
        "success": False,
        "email_sent": False,
        "error": err or "Email delivery failed. Check SMTP_EMAIL / SMTP_PASSWORD in .env.",
    }


def reset_password(db: Session, token: str, new_password: str) -> dict:
    """Reset password using token from email."""
    token_hash = _hash_password(token)
    user = db.query(PlatformUser).filter(PlatformUser.reset_token == token_hash).first()

    if not user:
        return {"success": False, "error": "Invalid or expired reset link"}

    if user.reset_token_expires and user.reset_token_expires < datetime.utcnow():
        return {"success": False, "error": "Reset link has expired. Please request a new one."}

    if len(new_password) < 6:
        return {"success": False, "error": "New password must be at least 6 characters"}

    user.password_hash = _hash_password(new_password)
    user.reset_token = None
    user.reset_token_expires = None
    db.commit()

    log.info(f"auth: password reset for {user.email}", extra={"action": "auth.password_reset"})
    return {"success": True, "message": "Password has been reset. You can now sign in."}


def reset_via_telegram(db: Session, email: str) -> dict:
    """Generate a temporary password and send it directly to Telegram."""
    user = db.query(PlatformUser).filter(PlatformUser.email == email).first()

    # Also try default admin
    if not user:
        user = db.query(PlatformUser).first()

    # If no user exists at all, create the admin user first
    if not user:
        user = _get_or_create_admin(db)

    # Generate temporary password
    temp_password = secrets.token_urlsafe(8)  # e.g. "aB3x_kL9"
    user.password_hash = _hash_password(temp_password)
    db.commit()

    # Send to Telegram
    try:
        from services.telegram_service import send_alert
        send_alert(
            f"<b>VIP Agent — Password Reset</b>\n\n"
            f"Your temporary password:\n"
            f"<code>{temp_password}</code>\n\n"
            f"Use this to sign in, then change your password in Settings."
        )
        log.info(f"auth: temp password sent to Telegram for {user.email}", extra={"action": "auth.telegram_reset"})
    except Exception as e:
        log.warning(f"auth: Telegram send failed: {e}", extra={"action": "auth.telegram_failed"})
        return {"success": False, "message": "Failed to send to Telegram. Please try again."}

    return {"success": True, "message": "Temporary password sent to Telegram! Check @vip_agentbot_bot."}


def _send_recovery_email(to_email: str, token: str) -> tuple[bool, str | None]:
    """Send password recovery email via Gmail SMTP. Returns (success, error_message)."""
    import smtplib
    from email.mime.text import MIMEText

    smtp_user = (os.getenv("SMTP_EMAIL", "") or "").strip()
    smtp_pass = (os.getenv("SMTP_PASSWORD", "") or "").replace(" ", "")
    app_url = os.getenv("APP_URL", "http://localhost:3000").rstrip("/")

    if not smtp_user or not smtp_pass:
        return False, "Email service not configured (SMTP_EMAIL / SMTP_PASSWORD missing)"

    # AuthGuard reads ?token= from the root URL and switches into reset view
    reset_link = f"{app_url}/?token={token}"

    try:
        msg = MIMEText(
            f"VIP Agent Platform\n\n"
            f"You requested a password reset.\n\n"
            f"Click this link to set a new password:\n{reset_link}\n\n"
            f"This link expires in {RESET_TOKEN_HOURS} hours.\n\n"
            f"If you didn't request this, ignore this email.",
            "plain",
        )
        msg["Subject"] = "VIP Agent — Password Reset"
        msg["From"] = smtp_user
        msg["To"] = to_email

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)

        return True, None
    except smtplib.SMTPAuthenticationError:
        return False, "Gmail rejected the SMTP credentials. Verify SMTP_EMAIL and the 16-char App Password."
    except Exception as e:
        return False, f"SMTP send failed: {e.__class__.__name__}: {e}"
