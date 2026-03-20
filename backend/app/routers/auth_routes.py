"""Register and login endpoints for local JWT auth."""
import json
import logging
import uuid as uuid_mod

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.auth import create_access_token, get_current_user, pwd_context
from app.config import settings
from app.crypto import (
    decrypt_credentials,
    derive_key,
    encrypt_credentials,
    generate_credential_hint,
    zero_buffer,
)
from app.database import SessionDep
from app.models import ProviderConfig, User, UserKeySalt
from app.schemas import AuthResponse, LoginRequest, PasswordChangeRequest, RegisterRequest
import app.key_cache as key_cache

logger = logging.getLogger(__name__)
router = APIRouter(tags=["auth"])


async def _load_provider_keys(user_id_str: str, user_id_uuid, password: str, session) -> bool:
    """Derive encryption key and decrypt all provider configs into key_cache.

    Returns True if keys were loaded (or no keys to load), False on failure.
    user_id_uuid is the UUID for DB queries; user_id_str is for key_cache (string).
    """
    # Check if user has a salt (meaning they have provider configs)
    salt_result = await session.execute(
        select(UserKeySalt).where(UserKeySalt.user_id == user_id_uuid)
    )
    salt_row = salt_result.scalar_one_or_none()
    if not salt_row:
        # No provider configs yet — nothing to load
        return True

    # Load all provider configs
    configs_result = await session.execute(
        select(ProviderConfig).where(ProviderConfig.user_id == user_id_uuid)
    )
    configs = configs_result.scalars().all()
    if not configs:
        return True

    key = derive_key(password, salt_row.salt, settings.ENCRYPTION_PEPPER.encode("utf-8"))
    try:
        credentials: dict[str, dict] = {}
        default_provider: str | None = None

        for config in configs:
            try:
                decrypted = decrypt_credentials(key, config.encrypted_credentials)
                credentials[config.provider] = json.loads(decrypted)
                if config.is_default:
                    default_provider = config.provider
            except Exception:
                logger.warning(
                    "Failed to decrypt credentials for user=%s provider=%s — skipping",
                    user_id_str,
                    config.provider,
                )

        if credentials:
            key_cache.populate(user_id_str, credentials, default_provider)

        return bool(credentials) or not configs
    finally:
        zero_buffer(key)


@router.post("/register", response_model=AuthResponse)
async def register(body: RegisterRequest, session: SessionDep):
    # Check for existing user with this email
    existing = await session.execute(
        select(User).where(User.email == body.email)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(
        email=body.email,
        password_hash=pwd_context.hash(body.password),
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    token = create_access_token(str(user.id))
    # New user has no provider configs, so keys_loaded is trivially true
    return AuthResponse(token=token, user_id=str(user.id), provider_keys_loaded=True)


@router.post("/login", response_model=AuthResponse)
async def login(body: LoginRequest, session: SessionDep):
    result = await session.execute(
        select(User).where(User.email == body.email)
    )
    user = result.scalar_one_or_none()
    if not user or not pwd_context.verify(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(str(user.id))
    user_id = str(user.id)

    # Load provider keys into cache
    keys_loaded = await _load_provider_keys(user_id, user.id, body.password, session)

    return AuthResponse(token=token, user_id=user_id, provider_keys_loaded=keys_loaded)


@router.put("/password")
async def change_password(
    body: PasswordChangeRequest,
    session: SessionDep,
    user_id: str = Depends(get_current_user),
):
    """Change password and re-encrypt all provider credentials with new key."""
    uid = uuid_mod.UUID(user_id)
    # Verify current password
    result = await session.execute(
        select(User).where(User.id == uid)
    )
    user = result.scalar_one_or_none()
    if not user or not pwd_context.verify(body.old_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid current password")

    # Load salt
    salt_result = await session.execute(
        select(UserKeySalt).where(UserKeySalt.user_id == uid)
    )
    salt_row = salt_result.scalar_one_or_none()

    # Re-encrypt all provider configs if any exist
    if salt_row:
        old_key = derive_key(body.old_password, salt_row.salt, settings.ENCRYPTION_PEPPER.encode("utf-8"))
        new_key = derive_key(body.new_password, salt_row.salt, settings.ENCRYPTION_PEPPER.encode("utf-8"))
        try:
            configs_result = await session.execute(
                select(ProviderConfig).where(ProviderConfig.user_id == uid)
            )
            configs = configs_result.scalars().all()

            for config in configs:
                try:
                    plaintext = decrypt_credentials(old_key, config.encrypted_credentials)
                    config.encrypted_credentials = encrypt_credentials(new_key, plaintext)
                except Exception:
                    logger.error(
                        "Failed to re-encrypt credentials for user=%s provider=%s",
                        user_id,
                        config.provider,
                    )
                    raise HTTPException(
                        status_code=500,
                        detail=f"Failed to re-encrypt {config.provider} credentials. Password not changed.",
                    )
        finally:
            zero_buffer(old_key)
            zero_buffer(new_key)

    # Update password hash
    user.password_hash = pwd_context.hash(body.new_password)
    await session.commit()

    # Clear key cache so user re-authenticates with new password
    key_cache.clear(user_id)

    return {"message": "Password changed successfully"}
