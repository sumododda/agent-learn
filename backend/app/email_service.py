import logging

import asyncio

import resend

from app.config import settings

logger = logging.getLogger(__name__)


def _init_resend() -> None:
    resend.api_key = settings.RESEND_API_KEY


def _send_sync(params: "resend.Emails.SendParams") -> None:
    resend.Emails.send(params)


def send_verification_email(email: str, otp: str) -> None:
    """Send a 6-digit OTP verification email via Resend."""
    _init_resend()
    params: resend.Emails.SendParams = {
        "from": settings.RESEND_FROM_EMAIL,
        "to": [email],
        "subject": "Your verification code",
        "html": (
            '<div style="font-family: sans-serif; max-width: 400px; margin: 0 auto; padding: 20px;">'
            '<h2 style="color: #7c3aed;">Verify your email</h2>'
            "<p>Your verification code is:</p>"
            '<div style="font-size: 32px; font-weight: bold; letter-spacing: 8px; '
            'padding: 16px; background: #f3f4f6; border-radius: 8px; text-align: center;">'
            f"{otp}</div>"
            '<p style="color: #6b7280; font-size: 14px; margin-top: 16px;">'
            "This code expires in 10 minutes.</p>"
            "</div>"
        ),
    }
    def _on_done(fut):
        exc = fut.exception()
        if exc:
            logger.error("Failed to send verification email: %s", type(exc).__name__)

    try:
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(None, _send_sync, params)
        fut.add_done_callback(_on_done)
    except RuntimeError:
        resend.Emails.send(params)
    local, _, domain = email.partition("@")
    masked = f"{local[:2]}***@{domain}" if len(local) > 2 else f"***@{domain}"
    logger.info("Verification email sent to %s", masked)
