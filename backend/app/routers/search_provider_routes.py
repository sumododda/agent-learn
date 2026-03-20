"""CRUD API for user search provider configurations."""
import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import search_service
from app.auth import get_current_user
from app.config import settings
from app.crypto import (
    decrypt_credentials,
    derive_key,
    encrypt_credentials,
    generate_credential_hint,
    generate_salt,
    zero_buffer,
)
from app.database import get_session
from app.models import ProviderConfig, UserKeySalt
from app.schemas import (
    ProviderConfigResponse,
    ProviderDefaultRequest,
    ProviderSaveRequest,
    ProviderTestRequest,
    ProviderUpdateRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/search-providers", tags=["search-providers"])

_PROVIDER_NAMES = {k: v["name"] for k, v in search_service.SEARCH_PROVIDERS.items()}
_VALID_PROVIDERS = set(_PROVIDER_NAMES.keys())


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


@router.get("/registry")
async def get_registry(user_id: str = Depends(get_current_user)):
    return {"providers": search_service.get_search_provider_registry()}


@router.get("", response_model=list[ProviderConfigResponse])
async def list_search_providers(
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    uid = _uid(user_id)
    result = await session.execute(
        select(ProviderConfig).where(
            ProviderConfig.user_id == uid,
            ProviderConfig.provider.in_(_VALID_PROVIDERS),
        )
    )
    configs = result.scalars().all()
    return [
        ProviderConfigResponse(
            provider=c.provider,
            name=_PROVIDER_NAMES.get(c.provider, c.provider),
            credential_hint=c.credential_hint,
            extra_fields=c.extra_fields or {},
            is_default=c.is_default,
        )
        for c in configs
    ]


@router.post("", response_model=ProviderConfigResponse, status_code=201)
async def save_search_provider(
    body: ProviderSaveRequest,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if body.provider not in _VALID_PROVIDERS:
        raise HTTPException(400, f"Invalid search provider: {body.provider}")

    uid = _uid(user_id)
    result = await session.execute(
        select(ProviderConfig).where(
            ProviderConfig.user_id == uid,
            ProviderConfig.provider == body.provider,
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(409, f"Search provider {body.provider} already configured. Use PUT to update.")

    salt, _ = await _get_or_create_salt(user_id, session)
    key = derive_key(body.password, salt, settings.ENCRYPTION_PEPPER.encode("utf-8"))
    try:
        encrypted = encrypt_credentials(key, json.dumps(body.credentials))
    finally:
        zero_buffer(key)

    hint = generate_credential_hint(body.provider, body.credentials) if body.credentials else "No key required"

    config = ProviderConfig(
        user_id=uid,
        provider=body.provider,
        encrypted_credentials=encrypted,
        credential_hint=hint,
        extra_fields=body.extra_fields or None,
        is_default=False,
    )
    session.add(config)
    await session.commit()

    return ProviderConfigResponse(
        provider=config.provider,
        name=_PROVIDER_NAMES.get(config.provider, config.provider),
        credential_hint=config.credential_hint,
        extra_fields=config.extra_fields or {},
        is_default=config.is_default,
    )


@router.put("/default", response_model=ProviderConfigResponse)
async def set_default_search_provider(
    body: ProviderDefaultRequest,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    uid = _uid(user_id)
    result = await session.execute(
        select(ProviderConfig).where(
            ProviderConfig.user_id == uid,
            ProviderConfig.provider == body.provider,
        )
    )
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(404, f"Search provider {body.provider} not configured")

    # Only toggle is_default among search provider rows
    all_result = await session.execute(
        select(ProviderConfig).where(
            ProviderConfig.user_id == uid,
            ProviderConfig.provider.in_(_VALID_PROVIDERS),
        )
    )
    for config in all_result.scalars().all():
        config.is_default = (config.provider == body.provider)

    await session.commit()

    return ProviderConfigResponse(
        provider=target.provider,
        name=_PROVIDER_NAMES.get(target.provider, target.provider),
        credential_hint=target.credential_hint,
        extra_fields=target.extra_fields or {},
        is_default=True,
    )


@router.put("/{provider}", response_model=ProviderConfigResponse)
async def update_search_provider(
    provider: str,
    body: ProviderUpdateRequest,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    uid = _uid(user_id)
    result = await session.execute(
        select(ProviderConfig).where(
            ProviderConfig.user_id == uid,
            ProviderConfig.provider == provider,
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(404, f"Search provider {provider} not configured")

    if body.credentials is not None:
        if not body.password:
            raise HTTPException(400, "Password required when updating credentials")
        salt, _ = await _get_or_create_salt(user_id, session)
        key = derive_key(body.password, salt, settings.ENCRYPTION_PEPPER.encode("utf-8"))
        try:
            config.encrypted_credentials = encrypt_credentials(key, json.dumps(body.credentials))
        finally:
            zero_buffer(key)
        config.credential_hint = generate_credential_hint(provider, body.credentials) if body.credentials else "No key required"

    if body.extra_fields is not None:
        config.extra_fields = body.extra_fields

    await session.commit()

    return ProviderConfigResponse(
        provider=config.provider,
        name=_PROVIDER_NAMES.get(config.provider, config.provider),
        credential_hint=config.credential_hint,
        extra_fields=config.extra_fields or {},
        is_default=config.is_default,
    )


@router.delete("/{provider}", status_code=204)
async def delete_search_provider(
    provider: str,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    uid = _uid(user_id)
    result = await session.execute(
        select(ProviderConfig).where(
            ProviderConfig.user_id == uid,
            ProviderConfig.provider == provider,
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(404, f"Search provider {provider} not configured")
    await session.delete(config)
    await session.commit()


@router.post("/{provider}/test")
async def test_search_provider(
    provider: str,
    body: ProviderTestRequest,
    user_id: str = Depends(get_current_user),
):
    if provider not in _VALID_PROVIDERS:
        raise HTTPException(400, f"Invalid search provider: {provider}")
    ok = await search_service.validate_search_credentials(provider, body.credentials)
    if ok:
        return {"status": "ok", "message": "Search credentials validated successfully"}
    raise HTTPException(400, "Search credential validation failed")
