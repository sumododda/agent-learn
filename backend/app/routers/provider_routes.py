"""CRUD API for user OpenRouter configuration."""
import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import key_cache, provider_service
from app.auth import get_current_user
from app.config import settings
from app.crypto import (
    derive_key,
    encrypt_credentials,
    decrypt_credentials,
    generate_credential_hint,
    generate_salt,
)
from app.database import get_session
from app.models import ProviderConfig, UserKeySalt
from app.schemas import (
    ProviderConfigResponse,
    ProviderSaveRequest,
    ProviderTestRequest,
    ProviderUpdateRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/providers", tags=["providers"])

PROVIDER = "openrouter"


def _uid(user_id: str) -> uuid.UUID:
    return uuid.UUID(user_id)


async def _get_or_create_salt(user_id: str, session: AsyncSession) -> tuple[bytes, bool]:
    uid = _uid(user_id)
    result = await session.execute(
        select(UserKeySalt).where(UserKeySalt.user_id == uid)
    )
    existing = result.scalar_one_or_none()
    if existing:
        return existing.salt, False
    salt = generate_salt()
    session.add(UserKeySalt(user_id=uid, salt=salt))
    await session.flush()
    return salt, True


def _get_key(salt: bytes) -> bytearray:
    return derive_key(salt, settings.ENCRYPTION_PEPPER.encode("utf-8"))


@router.get("/registry")
async def get_registry(user_id: str = Depends(get_current_user)):
    """Return provider info for frontend."""
    return {
        "providers": {
            "openrouter": {
                "name": "OpenRouter",
                "fields": [
                    {"key": "api_key", "label": "API Key", "type": "password", "required": True, "secret": True}
                ],
            }
        }
    }


@router.get("", response_model=list[ProviderConfigResponse])
async def list_providers(
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    uid = _uid(user_id)
    result = await session.execute(
        select(ProviderConfig).where(
            ProviderConfig.user_id == uid,
            ProviderConfig.provider == PROVIDER,
        )
    )
    configs = result.scalars().all()
    return [
        ProviderConfigResponse(
            provider=c.provider,
            name="OpenRouter",
            credential_hint=c.credential_hint,
            extra_fields=c.extra_fields or {},
            is_default=c.is_default,
        )
        for c in configs
    ]


@router.post("", response_model=ProviderConfigResponse, status_code=201)
async def save_provider(
    body: ProviderSaveRequest,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    uid = _uid(user_id)

    result = await session.execute(
        select(ProviderConfig).where(
            ProviderConfig.user_id == uid,
            ProviderConfig.provider == PROVIDER,
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(409, "Already configured. Use PUT to update.")

    salt, _ = await _get_or_create_salt(user_id, session)
    key = _get_key(salt)
    encrypted = encrypt_credentials(key, json.dumps(body.credentials))
    hint = generate_credential_hint(PROVIDER, body.credentials)

    config = ProviderConfig(
        user_id=uid,
        provider=PROVIDER,
        encrypted_credentials=encrypted,
        credential_hint=hint,
        extra_fields=body.extra_fields or None,
        is_default=True,
    )
    session.add(config)
    await session.commit()

    key_cache.set_credentials(user_id, PROVIDER, body.credentials)
    key_cache.set_default_llm(user_id, PROVIDER)

    return ProviderConfigResponse(
        provider=PROVIDER,
        name="OpenRouter",
        credential_hint=config.credential_hint,
        extra_fields=config.extra_fields or {},
        is_default=True,
    )


@router.put("/default")
async def set_default_provider(
    user_id: str = Depends(get_current_user),
):
    """No-op — OpenRouter is always the default."""
    return {"provider": PROVIDER, "is_default": True}


@router.put("/{provider}", response_model=ProviderConfigResponse)
async def update_provider(
    provider: str,
    body: ProviderUpdateRequest,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    uid = _uid(user_id)
    result = await session.execute(
        select(ProviderConfig).where(
            ProviderConfig.user_id == uid,
            ProviderConfig.provider == PROVIDER,
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(404, "Not configured")

    if body.credentials is not None:
        salt, _ = await _get_or_create_salt(user_id, session)
        key = _get_key(salt)
        config.encrypted_credentials = encrypt_credentials(key, json.dumps(body.credentials))
        config.credential_hint = generate_credential_hint(PROVIDER, body.credentials)
        key_cache.set_credentials(user_id, PROVIDER, body.credentials)

    if body.extra_fields is not None:
        config.extra_fields = body.extra_fields

    await session.commit()

    return ProviderConfigResponse(
        provider=PROVIDER,
        name="OpenRouter",
        credential_hint=config.credential_hint,
        extra_fields=config.extra_fields or {},
        is_default=config.is_default,
    )


@router.delete("/{provider}", status_code=204)
async def delete_provider(
    provider: str,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    uid = _uid(user_id)
    result = await session.execute(
        select(ProviderConfig).where(
            ProviderConfig.user_id == uid,
            ProviderConfig.provider == PROVIDER,
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(404, "Not configured")
    await session.delete(config)
    await session.commit()
    key_cache.remove_credentials(user_id, PROVIDER)


@router.post("/{provider}/test")
async def test_provider(
    provider: str,
    body: ProviderTestRequest,
    user_id: str = Depends(get_current_user),
):
    api_key = body.credentials.get("api_key", "")
    if not api_key:
        raise HTTPException(400, "API key required")
    ok = await provider_service.validate_credentials(api_key)
    if ok:
        return {"status": "ok", "message": "OpenRouter credentials validated"}
    raise HTTPException(400, "Credential validation failed")
