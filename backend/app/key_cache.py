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
_MAX_ENTRIES = 1000


def _evict_expired() -> None:
    """Remove expired entries when cache is at capacity."""
    now = datetime.now(timezone.utc)
    expired = [k for k, v in _cache.items() if now > v.expires_at]
    for k in expired:
        del _cache[k]


def populate(
    user_id: str,
    credentials: dict[str, dict],
    default_provider: str | None,
    default_search_provider: str | None = None,
    ttl_seconds: int = 86400,
) -> None:
    """Store decrypted credentials for a user."""
    if user_id not in _cache and len(_cache) >= _MAX_ENTRIES:
        _evict_expired()
        if len(_cache) >= _MAX_ENTRIES:
            oldest_key = next(iter(_cache))
            del _cache[oldest_key]
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
    if entry.default_search_provider == "duckduckgo":
        return ("duckduckgo", {})
    if entry.default_search_provider and entry.default_search_provider in entry.credentials:
        return (entry.default_search_provider, entry.credentials[entry.default_search_provider])
    for provider in entry.credentials:
        if provider in SEARCH_PROVIDERS:
            return (provider, entry.credentials[provider])
    if "duckduckgo" in SEARCH_PROVIDERS:
        return ("duckduckgo", {})
    return None


def get_all_search_providers(user_id: str) -> list[tuple[str, dict]]:
    """Return all configured search providers, default first. Excludes duckduckgo."""
    from app.search_service import SEARCH_PROVIDERS

    entry = _cache.get(user_id)
    if entry is None:
        return []
    if datetime.now(timezone.utc) > entry.expires_at:
        del _cache[user_id]
        return []
    result: list[tuple[str, dict]] = []
    # Default search provider first
    if entry.default_search_provider and entry.default_search_provider in entry.credentials:
        if entry.default_search_provider != "duckduckgo":
            result.append((entry.default_search_provider, entry.credentials[entry.default_search_provider]))
    # Then the rest
    for provider, creds in entry.credentials.items():
        if provider in SEARCH_PROVIDERS and provider != "duckduckgo":
            if not any(p == provider for p, _ in result):
                result.append((provider, creds))
    return result


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
