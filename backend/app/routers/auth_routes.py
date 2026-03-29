"""Register and login endpoints for local JWT auth."""
import json
import logging
import secrets
import uuid as uuid_mod

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from app.auth import create_access_token, create_sse_token, get_current_user, hash_password, verify_password
from app.config import settings
from app.crypto import decrypt_credentials, derive_key
from app.database import SessionDep
from app.limiter import limiter
from app.models import ProviderConfig, User, UserKeySalt
from app.schemas import (
    AuthResponse,
    ForgotPasswordConfirmRequest,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    OtpResendRequest,
    OtpResendResponse,
    OtpVerifyRequest,
    PasswordChangeRequest,
    RegisterRequest,
    RegisterResponse,
)
from app.turnstile import verify_turnstile_token
from app.email_service import send_password_reset_email, send_verification_email
import app.key_cache as key_cache
import app.login_tracker as login_tracker
import app.pending_registration_cache as pending_cache
import app.password_reset_cache as password_reset_cache

logger = logging.getLogger(__name__)
router = APIRouter(tags=["auth"])


def _generate_otp() -> str:
    return str(secrets.randbelow(900000) + 100000)


async def _load_provider_keys(user_id_str: str, user_id_uuid, session) -> bool:
    """Decrypt all provider configs into key_cache using server-side key.

    Returns True if keys were loaded (or no keys to load), False on failure.
    """
    salt_result = await session.execute(
        select(UserKeySalt).where(UserKeySalt.user_id == user_id_uuid)
    )
    salt_row = salt_result.scalar_one_or_none()
    if not salt_row:
        return True

    configs_result = await session.execute(
        select(ProviderConfig).where(ProviderConfig.user_id == user_id_uuid)
    )
    configs = configs_result.scalars().all()
    if not configs:
        return True

    from app.search_service import SEARCH_PROVIDERS

    key = derive_key(salt_row.salt, settings.ENCRYPTION_PEPPER.encode("utf-8"))
    credentials: dict[str, dict] = {}
    default_provider: str | None = None
    default_search_provider: str | None = None

    for config in configs:
        try:
            decrypted = decrypt_credentials(key, config.encrypted_credentials)
            credentials[config.provider] = json.loads(decrypted)
            if config.is_default and not config.provider.startswith("academic:"):
                if config.provider in SEARCH_PROVIDERS:
                    default_search_provider = config.provider
                else:
                    default_provider = config.provider
        except Exception:
            logger.warning(
                "Failed to decrypt credentials for user=%s provider=%s — skipping",
                user_id_str,
                config.provider,
            )

    key_cache.populate(user_id_str, credentials, default_provider, default_search_provider)
    return bool(credentials) or not configs


@router.post("/register", response_model=RegisterResponse)
@limiter.limit("3/hour")
async def register(request: Request, body: RegisterRequest, session: SessionDep):
    # 1. Verify Turnstile token
    if not await verify_turnstile_token(body.turnstile_token):
        raise HTTPException(status_code=400, detail="Turnstile verification failed")

    # 2. Check email not already registered
    existing = await session.execute(
        select(User).where(User.email == body.email)
    )
    if existing.scalar_one_or_none():
        return RegisterResponse(message="Verification code sent", email=body.email)

    # 3. Check if email already has pending registration
    pending = pending_cache.get(body.email)
    if pending:
        return RegisterResponse(message="Verification code sent", email=body.email)

    # 4. Hash password, generate and hash OTP
    password_hash = await hash_password(body.password)
    otp = _generate_otp()
    otp_hash = await hash_password(otp)

    # 5. Store pending registration
    pending_cache.store(body.email, password_hash, otp_hash)

    # 6. Send verification email
    send_verification_email(body.email, otp)

    return RegisterResponse(message="Verification code sent", email=body.email)


@router.post("/verify-otp", response_model=AuthResponse)
@limiter.limit("10/minute")
async def verify_otp(request: Request, body: OtpVerifyRequest, session: SessionDep):
    # 1. Look up pending registration
    pending = pending_cache.get(body.email)
    if pending is None:
        # Multi-replica fallback: check if another replica already verified this user
        existing = await session.execute(
            select(User).where(User.email == body.email)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=409,
                detail="Account already verified. Please log in.",
            )
        raise HTTPException(status_code=410, detail="Verification expired or not found. Please register again.")

    # 2. Check attempt limit
    if pending.attempts >= pending_cache.MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many failed attempts")

    # 3. Verify OTP
    if not await verify_password(body.otp, pending.otp_hash):
        pending_cache.increment_attempts(body.email)
        raise HTTPException(status_code=400, detail="Invalid verification code")

    # 4. Create user
    user = User(
        email=body.email,
        password_hash=pending.password_hash,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    # 5. Remove pending entry
    pending_cache.remove(body.email)

    # 6. Create and return JWT
    token = create_access_token(str(user.id))
    return AuthResponse(token=token, user_id=str(user.id), provider_keys_loaded=False)


@router.post("/resend-otp", response_model=OtpResendResponse)
@limiter.limit("10/minute")
async def resend_otp(request: Request, body: OtpResendRequest):
    # Return same response regardless of state to prevent enumeration
    pending = pending_cache.get(body.email)
    if pending is None:
        return OtpResendResponse(message="If a pending registration exists, a new code has been sent")

    otp = _generate_otp()
    otp_hash = await hash_password(otp)

    if not pending_cache.replace_otp(body.email, otp_hash):
        return OtpResendResponse(message="If a pending registration exists, a new code has been sent")

    send_verification_email(body.email, otp)

    return OtpResendResponse(message="If a pending registration exists, a new code has been sent")


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
@limiter.limit("5/hour")
async def forgot_password(request: Request, body: ForgotPasswordRequest, session: SessionDep):
    result = await session.execute(
        select(User).where(User.email == body.email)
    )
    user = result.scalar_one_or_none()

    if user:
        otp = _generate_otp()
        otp_hash = await hash_password(otp)
        pending = password_reset_cache.get(body.email)
        if pending is None:
            password_reset_cache.store(body.email, otp_hash)
            send_password_reset_email(body.email, otp)
        elif password_reset_cache.replace_otp(body.email, otp_hash):
            send_password_reset_email(body.email, otp)

    return ForgotPasswordResponse(
        message="If an account with that email exists, a password reset code has been sent."
    )


@router.post("/forgot-password/confirm", response_model=ForgotPasswordResponse)
@limiter.limit("10/minute")
async def confirm_forgot_password(
    request: Request,
    body: ForgotPasswordConfirmRequest,
    session: SessionDep,
):
    pending = password_reset_cache.get(body.email)
    if pending is None:
        raise HTTPException(
            status_code=410,
            detail="Password reset code expired or not found. Request a new code.",
        )

    if pending.attempts >= password_reset_cache.MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many failed attempts")

    if not await verify_password(body.otp, pending.otp_hash):
        password_reset_cache.increment_attempts(body.email)
        raise HTTPException(status_code=400, detail="Invalid reset code")

    result = await session.execute(
        select(User).where(User.email == body.email)
    )
    user = result.scalar_one_or_none()
    if user is None:
        password_reset_cache.remove(body.email)
        raise HTTPException(
            status_code=410,
            detail="Password reset code expired or not found. Request a new code.",
        )

    user.password_hash = await hash_password(body.new_password)
    await session.commit()
    password_reset_cache.remove(body.email)
    login_tracker.reset(body.email)

    return ForgotPasswordResponse(message="Password reset successful. Please sign in.")


@router.post("/login", response_model=AuthResponse)
@limiter.limit("10/minute")
async def login(request: Request, body: LoginRequest, session: SessionDep):
    # Per-email lockout check
    if login_tracker.is_locked_out(body.email):
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again later.")

    result = await session.execute(
        select(User).where(User.email == body.email)
    )
    user = result.scalar_one_or_none()
    if not user or not await verify_password(body.password, user.password_hash):
        login_tracker.record_failure(body.email)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    login_tracker.reset(body.email)
    token = create_access_token(str(user.id))
    user_id = str(user.id)

    keys_loaded = await _load_provider_keys(user_id, user.id, session)

    return AuthResponse(token=token, user_id=user_id, provider_keys_loaded=keys_loaded)


@router.put("/password")
@limiter.limit("5/minute")
async def change_password(
    request: Request,
    body: PasswordChangeRequest,
    session: SessionDep,
    user_id: str = Depends(get_current_user),
):
    """Change password (auth only — credentials use server-side encryption, no re-encrypt needed)."""
    uid = uuid_mod.UUID(user_id)
    result = await session.execute(
        select(User).where(User.id == uid)
    )
    user = result.scalar_one_or_none()
    if not user or not await verify_password(body.old_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid current password")

    user.password_hash = await hash_password(body.new_password)
    await session.commit()

    return {"message": "Password changed successfully"}


@router.post("/refresh", response_model=AuthResponse)
@limiter.limit("30/minute")
async def refresh_token(request: Request, session: SessionDep, user_id: str = Depends(get_current_user)):
    """Issue a fresh access token if the current one is still valid."""
    uid = uuid_mod.UUID(user_id)
    result = await session.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    token = create_access_token(user_id)
    keys_loaded = await _load_provider_keys(user_id, uid, session)
    return AuthResponse(token=token, user_id=user_id, provider_keys_loaded=keys_loaded)


@router.post("/sse-ticket")
@limiter.limit("60/minute")
async def get_sse_ticket(request: Request, user_id: str = Depends(get_current_user)):
    """Issue a short-lived (60s) SSE-scoped token for stream endpoints."""
    return {"ticket": create_sse_token(user_id)}
