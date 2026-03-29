"""CRUD API for user academic search provider configurations."""
import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import key_cache, search_service
from app.limiter import limiter
from app.auth import get_current_user
from app.config import settings
from app.crypto import (
    derive_key,
    encrypt_credentials,
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
router = APIRouter(prefix="/academic-providers", tags=["academic-providers"])

_PROVIDER_NAMES = {k: v["name"] for k, v in search_service.ACADEMIC_SEARCH_PROVIDERS.items()}
_VALID_PROVIDERS = set(_PROVIDER_NAMES.keys())

# Provider names are stored in DB with this prefix to avoid collisions with
# web search providers and LLM providers in the same ProviderConfig table.
_DB_PREFIX = "academic:"


def _uid(user_id: str) -> uuid.UUID:
    return uuid.UUID(user_id)


def _db_key(provider: str) -> str:
    """Return the prefixed provider key used in the DB."""
    return f"{_DB_PREFIX}{provider}"


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
    """Derive encryption key from server pepper + user salt."""
    return derive_key(salt, settings.ENCRYPTION_PEPPER.encode("utf-8"))


@router.get("/registry")
async def get_registry(user_id: str = Depends(get_current_user)):
    return {"providers": search_service.get_academic_search_provider_registry()}


@router.get("", response_model=list[ProviderConfigResponse])
async def list_academic_providers(
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    uid = _uid(user_id)
    db_keys = [_db_key(p) for p in _VALID_PROVIDERS]
    result = await session.execute(
        select(ProviderConfig).where(
            ProviderConfig.user_id == uid,
            ProviderConfig.provider.in_(db_keys),
        )
    )
    configs = result.scalars().all()
    return [
        ProviderConfigResponse(
            provider=c.provider.removeprefix(_DB_PREFIX),
            name=_PROVIDER_NAMES.get(c.provider.removeprefix(_DB_PREFIX), c.provider),
            credential_hint=c.credential_hint,
            extra_fields=c.extra_fields or {},
            is_default=c.is_default,
        )
        for c in configs
    ]


@router.post("", response_model=ProviderConfigResponse, status_code=201)
async def save_academic_provider(
    body: ProviderSaveRequest,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if body.provider not in _VALID_PROVIDERS:
        raise HTTPException(400, f"Invalid academic provider: {body.provider}")

    uid = _uid(user_id)
    db_name = _db_key(body.provider)

    result = await session.execute(
        select(ProviderConfig).where(
            ProviderConfig.user_id == uid,
            ProviderConfig.provider == db_name,
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(409, f"Academic provider {body.provider} already configured. Use PUT to update.")

    salt, _ = await _get_or_create_salt(user_id, session)
    key = _get_key(salt)
    encrypted = encrypt_credentials(key, json.dumps(body.credentials))
    hint = generate_credential_hint(body.provider, body.credentials) if body.credentials else "No key required"

    config = ProviderConfig(
        user_id=uid,
        provider=db_name,
        encrypted_credentials=encrypted,
        credential_hint=hint,
        extra_fields=body.extra_fields or None,
        is_default=False,
    )
    session.add(config)
    await session.commit()

    # Store with the prefixed key so key_cache.get_all_academic_providers works
    key_cache.set_credentials(user_id, db_name, body.credentials)

    return ProviderConfigResponse(
        provider=body.provider,
        name=_PROVIDER_NAMES.get(body.provider, body.provider),
        credential_hint=config.credential_hint,
        extra_fields=config.extra_fields or {},
        is_default=config.is_default,
    )


@router.put("/{provider}", response_model=ProviderConfigResponse)
async def update_academic_provider(
    provider: str,
    body: ProviderUpdateRequest,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    uid = _uid(user_id)
    db_name = _db_key(provider)
    result = await session.execute(
        select(ProviderConfig).where(
            ProviderConfig.user_id == uid,
            ProviderConfig.provider == db_name,
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(404, f"Academic provider {provider} not configured")

    if body.credentials is not None:
        salt, _ = await _get_or_create_salt(user_id, session)
        key = _get_key(salt)
        config.encrypted_credentials = encrypt_credentials(key, json.dumps(body.credentials))
        config.credential_hint = generate_credential_hint(provider, body.credentials) if body.credentials else "No key required"
        key_cache.set_credentials(user_id, db_name, body.credentials)

    if body.extra_fields is not None:
        config.extra_fields = body.extra_fields

    await session.commit()

    return ProviderConfigResponse(
        provider=provider,
        name=_PROVIDER_NAMES.get(provider, provider),
        credential_hint=config.credential_hint,
        extra_fields=config.extra_fields or {},
        is_default=config.is_default,
    )


@router.delete("/{provider}", status_code=204)
async def delete_academic_provider(
    provider: str,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    uid = _uid(user_id)
    db_name = _db_key(provider)
    result = await session.execute(
        select(ProviderConfig).where(
            ProviderConfig.user_id == uid,
            ProviderConfig.provider == db_name,
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(404, f"Academic provider {provider} not configured")
    await session.delete(config)
    await session.commit()
    key_cache.remove_credentials(user_id, db_name)


@router.post("/{provider}/test")
@limiter.limit("10/minute")
async def test_academic_provider(
    request: Request,
    provider: str,
    body: ProviderTestRequest,
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if provider not in _VALID_PROVIDERS:
        raise HTTPException(400, f"Invalid academic provider: {provider}")

    adapter = search_service._get_academic_adapter(provider)
    if not adapter:
        raise HTTPException(400, f"No adapter found for academic provider: {provider}")

    # Use provided credentials, or fall back to stored credentials if form was blank
    creds = body.credentials
    if not any(creds.values()):
        uid = _uid(user_id)
        db_name = _db_key(provider)
        result = await session.execute(
            select(ProviderConfig).where(
                ProviderConfig.user_id == uid,
                ProviderConfig.provider == db_name,
            )
        )
        config = result.scalar_one_or_none()
        if config:
            salt_result = await session.execute(
                select(UserKeySalt).where(UserKeySalt.user_id == uid)
            )
            salt_row = salt_result.scalar_one_or_none()
            if salt_row:
                from app.crypto import decrypt_credentials as _decrypt
                key = derive_key(salt_row.salt, settings.ENCRYPTION_PEPPER.encode("utf-8"))
                creds = json.loads(_decrypt(key, config.encrypted_credentials))

    try:
        results = await adapter("test query", creds, 1, "basic", None)
        if len(results) > 0:
            return {"status": "ok", "message": "Academic credentials validated successfully"}
        raise HTTPException(400, "Academic credential validation failed — no results returned")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Academic credential validation failed for %s: %s", provider, e)
        raise HTTPException(400, f"Academic credential validation failed: {e}")
