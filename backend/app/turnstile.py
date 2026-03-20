import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def verify_turnstile_token(token: str) -> bool:
    """Verify a Cloudflare Turnstile token server-side.

    Returns True if the token is valid, False otherwise.
    """
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                json={"secret": settings.TURNSTILE_SECRET_KEY, "response": token},
                timeout=10.0,
            )
            result = resp.json()
            return result.get("success", False)
    except httpx.TimeoutException:
        logger.warning("Turnstile verification timed out")
        return False
    except Exception:
        logger.exception("Turnstile verification failed")
        return False
