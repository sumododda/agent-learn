"""Clerk JWT authentication for public API endpoints.

Verifies Bearer tokens issued by Clerk using JWKS (RS256).
Internal endpoints continue using X-Internal-Token and are not affected.
"""
import time

import httpx
import jwt
from fastapi import Header, HTTPException

from app.config import settings

_jwks_cache: dict | None = None
_jwks_cache_time: float = 0


async def _get_jwks() -> dict:
    """Fetch and cache Clerk's JWKS keys (cached for 1 hour)."""
    global _jwks_cache, _jwks_cache_time
    now = time.time()
    if _jwks_cache and (now - _jwks_cache_time) < 3600:
        return _jwks_cache
    async with httpx.AsyncClient() as client:
        resp = await client.get(settings.CLERK_JWKS_URL)
        resp.raise_for_status()
        _jwks_cache = resp.json()
        _jwks_cache_time = now
        return _jwks_cache


async def get_current_user(authorization: str | None = Header(default=None)) -> str:
    """FastAPI dependency: extract and verify Clerk JWT, return user_id.

    Raises:
        HTTPException(401) if the token is missing, invalid, or expired.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization")
    token = authorization.removeprefix("Bearer ")
    try:
        jwks = await _get_jwks()
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        key = None
        for k in jwks.get("keys", []):
            if k.get("kid") == kid:
                key = jwt.algorithms.RSAAlgorithm.from_jwk(k)
                break
        if not key:
            raise HTTPException(status_code=401, detail="Key not found")
        decoded = jwt.decode(
            token, key, algorithms=["RS256"], issuer=settings.CLERK_ISSUER
        )
        return decoded["sub"]
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=str(e))
