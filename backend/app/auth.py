"""Local JWT authentication for public API endpoints.

Verifies Bearer tokens signed with HS256 using JWT_SECRET_KEY.
"""
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Header, HTTPException
from passlib.context import CryptContext

from app.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

_JWT_ISSUER = "agent-learn"
_JWT_AUDIENCE_API = "agent-learn-api"
_JWT_AUDIENCE_SSE = "agent-learn-sse"
_SSE_TOKEN_EXPIRY_SECONDS = 60


def create_access_token(user_id: str) -> str:
    """Create a signed JWT with the given user_id as the subject."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iss": _JWT_ISSUER,
        "aud": _JWT_AUDIENCE_API,
        "iat": now,
        "exp": now + timedelta(minutes=settings.JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm="HS256")


def create_sse_token(user_id: str) -> str:
    """Create a short-lived JWT for SSE stream authentication."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iss": _JWT_ISSUER,
        "aud": _JWT_AUDIENCE_SSE,
        "iat": now,
        "exp": now + timedelta(seconds=_SSE_TOKEN_EXPIRY_SECONDS),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm="HS256")


async def get_user_from_query_token(token: str) -> str:
    """Validate short-lived SSE JWT from query parameter."""
    try:
        decoded = jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=["HS256"],
            issuer=_JWT_ISSUER, audience=_JWT_AUDIENCE_SSE,
        )
        return decoded["sub"]
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def get_current_user(authorization: str | None = Header(default=None)) -> str:
    """FastAPI dependency: extract and verify JWT, return user_id.

    Raises:
        HTTPException(401) if the token is missing, invalid, or expired.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization")
    token = authorization.removeprefix("Bearer ")
    try:
        decoded = jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=["HS256"],
            issuer=_JWT_ISSUER, audience=_JWT_AUDIENCE_API,
        )
        return decoded["sub"]
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid authentication token")
