"""CRUD API for user LLM provider configurations."""
import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
router = APIRouter(prefix="/providers", tags=["providers"])

# Provider display names (full registry in provider_service.py, Phase 3)
_PROVIDER_NAMES = {
    "anthropic": "Anthropic",
    "azure": "Azure OpenAI",
    "mistral": "Mistral",
    "nvidia": "NVIDIA NIM",
    "vertex_ai": "Vertex AI",
    "openrouter": "OpenRouter",
}

_VALID_PROVIDERS = set(_PROVIDER_NAMES.keys())


def _uid(user_id: str) -> uuid.UUID:
    """Convert string user_id to UUID for FK-typed columns."""
    return uuid.UUID(user_id)


async def _get_or_create_salt(user_id: str, session: AsyncSession) -> tuple[bytes, bool]:
    """Get existing salt or create a new one. Returns (salt, is_new)."""
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
    """Return provider definitions for frontend form rendering.
    Full registry will come from provider_service.py in Phase 3.
    For now, return provider names and valid list."""
    return {"providers": _PROVIDER_NAMES}


@router.get("", response_model=list[ProviderConfigResponse])
async def list_providers(
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """List user's configured providers (hints only, never credentials)."""
    uid = _uid(user_id)
    result = await session.execute(
        select(ProviderConfig).where(ProviderConfig.user_id == uid)
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
async def save_provider(
    body: ProviderSaveRequest,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Save a new provider configuration. Encrypts credentials with password-derived key."""
    if body.provider not in _VALID_PROVIDERS:
        raise HTTPException(400, f"Invalid provider: {body.provider}")

    uid = _uid(user_id)

    # Check for existing config
    result = await session.execute(
        select(ProviderConfig).where(
            ProviderConfig.user_id == uid,
            ProviderConfig.provider == body.provider,
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(409, f"Provider {body.provider} already configured. Use PUT to update.")

    # Derive encryption key
    salt, _ = await _get_or_create_salt(user_id, session)
    key = derive_key(body.password, salt, settings.ENCRYPTION_PEPPER.encode("utf-8"))
    try:
        encrypted = encrypt_credentials(key, json.dumps(body.credentials))
    finally:
        zero_buffer(key)

    hint = generate_credential_hint(body.provider, body.credentials)

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
async def set_default_provider(
    body: ProviderDefaultRequest,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Set a provider as the user's default."""
    uid = _uid(user_id)
    # Verify the provider is configured
    result = await session.execute(
        select(ProviderConfig).where(
            ProviderConfig.user_id == uid,
            ProviderConfig.provider == body.provider,
        )
    )
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(404, f"Provider {body.provider} not configured")

    # Unset all defaults for this user
    all_result = await session.execute(
        select(ProviderConfig).where(ProviderConfig.user_id == uid)
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
async def update_provider(
    provider: str,
    body: ProviderUpdateRequest,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Update a provider config. Password required only when credentials change."""
    uid = _uid(user_id)
    result = await session.execute(
        select(ProviderConfig).where(
            ProviderConfig.user_id == uid,
            ProviderConfig.provider == provider,
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(404, f"Provider {provider} not configured")

    if body.credentials is not None:
        if not body.password:
            raise HTTPException(400, "Password required when updating credentials")
        salt, _ = await _get_or_create_salt(user_id, session)
        key = derive_key(body.password, salt, settings.ENCRYPTION_PEPPER.encode("utf-8"))
        try:
            config.encrypted_credentials = encrypt_credentials(key, json.dumps(body.credentials))
        finally:
            zero_buffer(key)
        config.credential_hint = generate_credential_hint(provider, body.credentials)

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
async def delete_provider(
    provider: str,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Remove a provider configuration."""
    uid = _uid(user_id)
    result = await session.execute(
        select(ProviderConfig).where(
            ProviderConfig.user_id == uid,
            ProviderConfig.provider == provider,
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(404, f"Provider {provider} not configured")
    await session.delete(config)
    await session.commit()


@router.post("/{provider}/test")
async def test_provider(
    provider: str,
    body: ProviderTestRequest,
    user_id: str = Depends(get_current_user),
):
    """Test credentials without saving. Placeholder until Phase 3 adds LiteLLM validation."""
    if provider not in _VALID_PROVIDERS:
        raise HTTPException(400, f"Invalid provider: {provider}")
    # Phase 3 will add: provider_service.validate_credentials(provider, body.credentials, body.extra_fields)
    return {"status": "not_implemented", "message": "Credential testing available after LiteLLM integration"}
