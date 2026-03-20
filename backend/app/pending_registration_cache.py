"""In-memory cache for pending registrations awaiting OTP verification.

Stores the bcrypt-hashed password and OTP hash so that plaintext secrets
never persist beyond the initial request.  Entries auto-expire after
TTL_SECONDS and are limited to MAX_ATTEMPTS verification tries and
MAX_RESENDS OTP re-sends.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

TTL_SECONDS = 600  # 10 minutes
MAX_ATTEMPTS = 5
MAX_RESENDS = 3


@dataclass
class _PendingEntry:
    email: str
    password_hash: str
    otp_hash: str
    attempts: int = 0
    resend_count: int = 0
    expires_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc) + timedelta(seconds=TTL_SECONDS)
    )


_cache: dict[str, _PendingEntry] = {}


def store(email: str, password_hash: str, otp_hash: str) -> None:
    """Store a pending registration entry, keyed by email."""
    _cache[email] = _PendingEntry(
        email=email,
        password_hash=password_hash,
        otp_hash=otp_hash,
    )


def get(email: str) -> _PendingEntry | None:
    """Retrieve a pending entry if it exists and hasn't expired."""
    entry = _cache.get(email)
    if entry is None:
        return None
    if datetime.now(timezone.utc) > entry.expires_at:
        del _cache[email]
        return None
    return entry


def remove(email: str) -> None:
    """Delete a pending registration entry."""
    _cache.pop(email, None)


def increment_attempts(email: str) -> int:
    """Increment the failed attempt counter. Returns the new count."""
    entry = get(email)
    if entry is None:
        return 0
    entry.attempts += 1
    return entry.attempts


def replace_otp(email: str, new_otp_hash: str) -> bool:
    """Replace the OTP hash, reset TTL, and bump resend count.

    Returns False if the entry doesn't exist or resend limit is reached.
    """
    entry = get(email)
    if entry is None:
        return False
    if entry.resend_count >= MAX_RESENDS:
        return False
    entry.otp_hash = new_otp_hash
    entry.resend_count += 1
    entry.expires_at = datetime.now(timezone.utc) + timedelta(seconds=TTL_SECONDS)
    return True
