"""In-memory session cache for decrypted provider credentials.

Populated at login after deriving the encryption key from the user's password.
Intentionally per-process and non-persistent — server restarts clear all
cached credentials, requiring users to re-login.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta


@dataclass
class _CacheEntry:
    credentials: dict[str, dict]  # {provider: {api_key: ..., ...}}
    default_provider: str | None
    default_search_provider: str | None
    expires_at: datetime


_cache: dict[str, _CacheEntry] = {}


def populate(
    user_id: str,
    credentials: dict[str, dict],
    default_provider: str | None,
    default_search_provider: str | None = None,
    ttl_seconds: int = 86400,
) -> None:
    """Store decrypted credentials for a user. Called after login."""
    _cache[user_id] = _CacheEntry(
        credentials=credentials,
        default_provider=default_provider,
        default_search_provider=default_search_provider,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
    )


def get(user_id: str, provider: str) -> dict | None:
    """Get decrypted credentials for a specific provider, or None if expired/missing."""
    entry = _cache.get(user_id)
    if entry is None:
        return None
    if datetime.now(timezone.utc) > entry.expires_at:
        del _cache[user_id]
        return None
    return entry.credentials.get(provider)


def get_default(user_id: str) -> tuple[str, dict] | None:
    """Get (provider_name, credentials) for the user's default provider."""
    entry = _cache.get(user_id)
    if entry is None:
        return None
    if datetime.now(timezone.utc) > entry.expires_at:
        del _cache[user_id]
        return None
    if entry.default_provider and entry.default_provider in entry.credentials:
        return (entry.default_provider, entry.credentials[entry.default_provider])
    # Fall back to first configured provider
    if entry.credentials:
        first = next(iter(entry.credentials))
        return (first, entry.credentials[first])
    return None


def get_default_search(user_id: str) -> tuple[str, dict] | None:
    """Get (search_provider_name, credentials) for the user's default search provider."""
    from app.search_service import SEARCH_PROVIDERS

    entry = _cache.get(user_id)
    if entry is None:
        return None
    if datetime.now(timezone.utc) > entry.expires_at:
        del _cache[user_id]
        return None
    if entry.default_search_provider and entry.default_search_provider in entry.credentials:
        return (entry.default_search_provider, entry.credentials[entry.default_search_provider])
    # Fall back to first search provider found in credentials
    for provider in entry.credentials:
        if provider in SEARCH_PROVIDERS:
            return (provider, entry.credentials[provider])
    return None


def clear(user_id: str) -> None:
    """Remove cached credentials for a user. Called on logout."""
    _cache.pop(user_id, None)


def _clear_all() -> None:
    """Clear entire cache. For testing only."""
    _cache.clear()
