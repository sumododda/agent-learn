"""CRUD API for user LLM provider configurations."""

import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import key_cache, provider_service
from app.auth import get_current_user
from app.config import settings
from app.crypto import (
    derive_key,
    encrypt_credentials,
    generate_credential_hint,
    generate_salt,
)
from app.database import get_session
from app.limiter import limiter
from app.models import ProviderConfig, UserKeySalt
from app.schemas import (
    ChatModelInfo,
    ProviderConfigResponse,
    ProviderDefaultRequest,
    ProviderSaveRequest,
    ProviderTestRequest,
    ProviderUpdateRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/providers", tags=["providers"])

_VALID_PROVIDERS = set(provider_service.PROVIDER_REGISTRY.keys())


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


def _ensure_valid_provider(provider: str) -> None:
    if provider not in _VALID_PROVIDERS:
        raise HTTPException(400, f"Invalid provider: {provider}")


def _require_new_credentials(provider: str, credentials: dict) -> None:
    definition = provider_service.PROVIDER_REGISTRY[provider]
    missing = [field.label for field in definition.fields if field.required and not str(credentials.get(field.key, "")).strip()]
    if missing:
        raise HTTPException(400, f"Missing required fields: {', '.join(missing)}")


async def _list_llm_configs(uid: uuid.UUID, session: AsyncSession) -> list[ProviderConfig]:
    result = await session.execute(
        select(ProviderConfig)
        .where(
            ProviderConfig.user_id == uid,
            ProviderConfig.provider.in_(_VALID_PROVIDERS),
        )
        .order_by(ProviderConfig.created_at.asc())
    )
    return list(result.scalars().all())


def _to_response(config: ProviderConfig) -> ProviderConfigResponse:
    return ProviderConfigResponse(
        provider=config.provider,
        name=provider_service.get_provider_name(config.provider),
        credential_hint=config.credential_hint,
        extra_fields=config.extra_fields or {},
        is_default=config.is_default,
    )


async def _ensure_cache_loaded(user_id: str, session: AsyncSession) -> None:
    if key_cache.get_default(user_id) is not None:
        return
    from app.routers.auth_routes import _load_provider_keys

    await _load_provider_keys(user_id, _uid(user_id), session)


@router.get("/registry")
async def get_registry(user_id: str = Depends(get_current_user)):
    return provider_service.get_provider_registry()


@router.get("", response_model=list[ProviderConfigResponse])
async def list_providers(
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    configs = await _list_llm_configs(_uid(user_id), session)
    configs.sort(key=lambda config: (not config.is_default, config.created_at))
    return [_to_response(config) for config in configs]


@router.get("/{provider}/models", response_model=list[ChatModelInfo])
@limiter.limit("30/minute")
async def list_provider_models(
    request: Request,
    provider: str,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    _ensure_valid_provider(provider)
    await _ensure_cache_loaded(user_id, session)
    credentials = key_cache.get(user_id, provider)
    result = await session.execute(
        select(ProviderConfig).where(
            ProviderConfig.user_id == _uid(user_id),
            ProviderConfig.provider == provider,
        )
    )
    config = result.scalar_one_or_none()
    extra_fields = config.extra_fields if config else {}

    if credentials is None or config is None:
        if provider == "openrouter":
            try:
                return await provider_service.list_public_models(provider, extra_fields or {})
            except provider_service.ProviderError as exc:
                raise HTTPException(502, str(exc)) from exc
        raise HTTPException(404, "not_configured")

    try:
        return await provider_service.list_models(provider, credentials, extra_fields or {})
    except provider_service.ProviderAuthError as exc:
        raise HTTPException(400, str(exc)) from exc
    except provider_service.ProviderError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("", response_model=ProviderConfigResponse, status_code=201)
async def save_provider(
    body: ProviderSaveRequest,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    _ensure_valid_provider(body.provider)
    _require_new_credentials(body.provider, body.credentials)

    uid = _uid(user_id)
    result = await session.execute(
        select(ProviderConfig).where(
            ProviderConfig.user_id == uid,
            ProviderConfig.provider == body.provider,
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(409, f"Provider {body.provider} already configured. Use PUT to update.")

    existing = await _list_llm_configs(uid, session)
    is_first = len(existing) == 0

    salt, _ = await _get_or_create_salt(user_id, session)
    key = _get_key(salt)
    encrypted = encrypt_credentials(key, json.dumps(body.credentials))
    hint = generate_credential_hint(body.provider, body.credentials)

    config = ProviderConfig(
        user_id=uid,
        provider=body.provider,
        encrypted_credentials=encrypted,
        credential_hint=hint,
        extra_fields=body.extra_fields or None,
        is_default=is_first,
    )
    session.add(config)
    await session.commit()

    key_cache.set_credentials(user_id, body.provider, body.credentials)
    if is_first:
        key_cache.set_default_llm(user_id, body.provider)

    return _to_response(config)


@router.put("/default", response_model=ProviderConfigResponse)
async def set_default_provider(
    body: ProviderDefaultRequest,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    _ensure_valid_provider(body.provider)
    uid = _uid(user_id)
    configs = await _list_llm_configs(uid, session)
    target = next((config for config in configs if config.provider == body.provider), None)
    if target is None:
        raise HTTPException(404, f"Provider {body.provider} not configured")

    for config in configs:
        config.is_default = config.provider == body.provider

    await session.commit()
    key_cache.set_default_llm(user_id, body.provider)
    return _to_response(target)


@router.put("/{provider}", response_model=ProviderConfigResponse)
async def update_provider(
    provider: str,
    body: ProviderUpdateRequest,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    _ensure_valid_provider(provider)
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
        _require_new_credentials(provider, body.credentials)
        salt, _ = await _get_or_create_salt(user_id, session)
        key = _get_key(salt)
        config.encrypted_credentials = encrypt_credentials(key, json.dumps(body.credentials))
        config.credential_hint = generate_credential_hint(provider, body.credentials)
        key_cache.set_credentials(user_id, provider, body.credentials)

    if body.extra_fields is not None:
        config.extra_fields = body.extra_fields

    await session.commit()
    return _to_response(config)


@router.delete("/{provider}", status_code=204)
async def delete_provider(
    provider: str,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    _ensure_valid_provider(provider)
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

    was_default = config.is_default
    await session.delete(config)
    await session.flush()

    next_default: ProviderConfig | None = None
    if was_default:
        remaining = await _list_llm_configs(uid, session)
        if remaining:
            next_default = remaining[0]
            next_default.is_default = True

    await session.commit()

    key_cache.remove_credentials(user_id, provider)
    if was_default:
        key_cache.set_default_llm(user_id, next_default.provider if next_default else None)


@router.post("/{provider}/test")
@limiter.limit("10/minute")
async def test_provider(
    request: Request,
    provider: str,
    body: ProviderTestRequest,
    user_id: str = Depends(get_current_user),
):
    _ensure_valid_provider(provider)
    _require_new_credentials(provider, body.credentials)
    ok = await provider_service.validate_credentials(provider, body.credentials, body.extra_fields)
    if ok:
        try:
            models = await provider_service.list_models(provider, body.credentials, body.extra_fields)
        except provider_service.ProviderError as exc:
            raise HTTPException(502, str(exc)) from exc
        return {
            "status": "ok",
            "message": f"{provider_service.get_provider_name(provider)} credentials validated",
            "models": models,
        }
    raise HTTPException(400, "Credential validation failed")
