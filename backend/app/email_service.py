import logging

import resend

from app.config import settings

logger = logging.getLogger(__name__)


def _init_resend() -> None:
    resend.api_key = settings.RESEND_API_KEY


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
    resend.Emails.send(params)
    logger.info("Verification email sent to %s", email)
