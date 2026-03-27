import logging

import asyncio

import resend

from app.config import settings

logger = logging.getLogger(__name__)


def _init_resend() -> None:
    resend.api_key = settings.RESEND_API_KEY


def _send_sync(params: "resend.Emails.SendParams") -> None:
    resend.Emails.send(params)


def _send_email(params: "resend.Emails.SendParams", email: str, log_label: str) -> None:
    _init_resend()

    def _on_done(fut):
        exc = fut.exception()
        if exc:
            logger.error("Failed to send %s email: %s", log_label, type(exc).__name__)

    try:
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(None, _send_sync, params)
        fut.add_done_callback(_on_done)
    except RuntimeError:
        resend.Emails.send(params)

    local, _, domain = email.partition("@")
    masked = f"{local[:2]}***@{domain}" if len(local) > 2 else f"***@{domain}"
    logger.info("%s email sent to %s", log_label.capitalize(), masked)


def _build_code_email(email: str, otp: str, subject: str, heading: str, description: str) -> None:
    params: resend.Emails.SendParams = {
        "from": settings.RESEND_FROM_EMAIL,
        "to": [email],
        "subject": subject,
        "html": (
            '<div style="font-family: sans-serif; max-width: 400px; margin: 0 auto; padding: 20px;">'
            f'<h2 style="color: #7c3aed;">{heading}</h2>'
            f"<p>{description}</p>"
            '<div style="font-size: 32px; font-weight: bold; letter-spacing: 8px; '
            'padding: 16px; background: #f3f4f6; border-radius: 8px; text-align: center;">'
            f"{otp}</div>"
            '<p style="color: #6b7280; font-size: 14px; margin-top: 16px;">'
            "This code expires in 10 minutes.</p>"
            "</div>"
        ),
    }
    _send_email(params, email, subject)


def send_verification_email(email: str, otp: str) -> None:
    """Send a 6-digit OTP verification email via Resend."""
    _build_code_email(
        email,
        otp,
        "Your verification code",
        "Verify your email",
        "Your verification code is:",
    )


def send_password_reset_email(email: str, otp: str) -> None:
    """Send a 6-digit password reset code via Resend."""
    _build_code_email(
        email,
        otp,
        "Your password reset code",
        "Reset your password",
        "Use this code to reset your password:",
    )
