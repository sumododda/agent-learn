"""In-memory cache for decrypted provider credentials.

Populated on login or lazily on first use. Server-side encryption means
credentials can always be decrypted from DB without user password.
"""
from dataclasses import dataclass
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
    """Store decrypted credentials for a user."""
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
    for provider in entry.credentials:
        if provider in SEARCH_PROVIDERS:
            return (provider, entry.credentials[provider])
    return None


def set_credentials(user_id: str, provider: str, creds: dict) -> None:
    """Add or update decrypted credentials for a single provider in the cache."""
    entry = _cache.get(user_id)
    if entry is None:
        return
    entry.credentials[provider] = creds


def remove_credentials(user_id: str, provider: str) -> None:
    """Remove credentials for a single provider from cache."""
    entry = _cache.get(user_id)
    if entry is None:
        return
    entry.credentials.pop(provider, None)


def set_default_llm(user_id: str, provider: str) -> None:
    """Update the cached default LLM provider."""
    entry = _cache.get(user_id)
    if entry is None:
        return
    entry.default_provider = provider


def set_default_search(user_id: str, provider: str) -> None:
    """Update the cached default search provider."""
    entry = _cache.get(user_id)
    if entry is None:
        return
    entry.default_search_provider = provider


def clear(user_id: str) -> None:
    """Remove cached credentials for a user."""
    _cache.pop(user_id, None)


def _clear_all() -> None:
    """Clear entire cache. For testing only."""
    _cache.clear()
