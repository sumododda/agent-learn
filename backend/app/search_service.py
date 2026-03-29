"""Multi-provider search service with unified interface.

Mirrors provider_service.py pattern but for web search providers.
Each adapter normalizes results into SearchResult dataclass.
"""
import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

SEARCH_PROVIDERS = {
    "tavily": {
        "name": "Tavily",
        "fields": [
            {"key": "api_key", "label": "API Key", "type": "password", "required": True, "secret": True},
        ],
    },
    "exa": {
        "name": "Exa",
        "fields": [
            {"key": "api_key", "label": "API Key", "type": "password", "required": True, "secret": True},
        ],
    },
    "brave_search": {
        "name": "Brave Search",
        "fields": [
            {"key": "api_key", "label": "API Key", "type": "password", "required": True, "secret": True},
        ],
    },
    "serper": {
        "name": "Serper.dev",
        "fields": [
            {"key": "api_key", "label": "API Key", "type": "password", "required": True, "secret": True},
        ],
    },
    "duckduckgo": {
        "name": "DuckDuckGo",
        "fields": [],
    },
}


@dataclass
class SearchResult:
    title: str
    url: str
    content: str
    score: float = 0.0
    # Academic metadata (populated by academic search adapters)
    authors: list[str] | None = None
    year: int | None = None
    venue: str | None = None
    citation_count: int | None = None
    doi: str | None = None
    is_academic: bool = False


def reconstruct_abstract(inverted_index: dict | None) -> str:
    """Reconstruct plain text from OpenAlex abstract_inverted_index format."""
    if not inverted_index:
        return ""
    words: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        for pos in positions:
            words.append((pos, word))
    words.sort(key=lambda x: x[0])
    return " ".join(w for _, w in words)


def get_search_provider_registry() -> dict:
    """Return search provider definitions for frontend form rendering."""
    return SEARCH_PROVIDERS


def is_configured(provider: str | None, credentials: dict | None) -> bool:
    """Check whether a search provider is ready to use."""
    if not provider or provider not in SEARCH_PROVIDERS:
        return False
    if provider == "duckduckgo":
        return True
    return bool(credentials and credentials.get("api_key"))


async def search(
    provider: str,
    query: str,
    credentials: dict,
    max_results: int = 5,
    search_depth: str = "basic",
) -> list[SearchResult]:
    """Unified search dispatching to the correct provider adapter."""
    adapter = _ADAPTERS.get(provider)
    if adapter is None:
        raise ValueError(f"Unknown search provider: {provider}")
    return await adapter(query, credentials, max_results, search_depth)


async def search_with_fallback(
    primary_provider: str,
    query: str,
    primary_credentials: dict,
    user_id: str = "",
    max_results: int = 5,
    search_depth: str = "basic",
) -> list[SearchResult]:
    """Try the primary provider first, then fall back through other configured providers.

    Fallback order: primary → other user-configured providers → DuckDuckGo.
    Returns results from the first provider that succeeds. If all fail, returns [].
    """
    from app import key_cache

    # Try primary provider first
    if primary_provider and primary_provider != "duckduckgo" and is_configured(primary_provider, primary_credentials):
        try:
            results = await search(primary_provider, query, primary_credentials, max_results, search_depth)
            if results:
                return results
            logger.warning("[search_fallback] %s returned 0 results for '%s', trying fallbacks", primary_provider, query[:60])
        except Exception as e:
            logger.warning("[search_fallback] %s failed for '%s': %s — trying fallbacks", primary_provider, query[:60], e)

    # Try other configured providers (skipping the one we already tried)
    if user_id:
        other_providers = key_cache.get_all_search_providers(user_id)
        for provider_name, creds in other_providers:
            if provider_name == primary_provider:
                continue
            try:
                results = await search(provider_name, query, creds, max_results, search_depth)
                if results:
                    logger.info("[search_fallback] %s succeeded as fallback (%d results)", provider_name, len(results))
                    return results
                logger.warning("[search_fallback] %s returned 0 results, trying next", provider_name)
            except Exception as e:
                logger.warning("[search_fallback] %s failed: %s — trying next", provider_name, e)

    # DuckDuckGo as implicit last resort (no API key needed)
    try:
        results = await search("duckduckgo", query, {}, max_results, search_depth)
        if results:
            logger.info("[search_fallback] DuckDuckGo fallback returned %d results", len(results))
            return results
    except Exception as e:
        logger.warning("[search_fallback] DuckDuckGo fallback also failed: %s", e)

    return []


async def validate_search_credentials(provider: str, credentials: dict) -> bool:
    """Test credentials by running a lightweight search query."""
    try:
        results = await search(provider, "test query", credentials, max_results=1)
        return len(results) > 0
    except Exception as e:
        logger.warning("Search credential validation failed for %s: %s", provider, e)
        return False


# ---------------------------------------------------------------------------
# Per-provider adapters
# ---------------------------------------------------------------------------


async def _search_tavily(
    query: str, credentials: dict, max_results: int, search_depth: str
) -> list[SearchResult]:
    import asyncio
    from tavily import AsyncTavilyClient

    client = AsyncTavilyClient(api_key=credentials["api_key"])
    response = await asyncio.wait_for(
        client.search(
            query=query,
            search_depth=search_depth,
            max_results=max_results,
        ),
        timeout=30.0,
    )
    return [
        SearchResult(
            title=r.get("title", ""),
            url=r.get("url", ""),
            content=r.get("content", ""),
            score=r.get("score", 0.0),
        )
        for r in response.get("results", [])
    ]


async def _search_exa(
    query: str, credentials: dict, max_results: int, search_depth: str
) -> list[SearchResult]:
    import asyncio
    from exa_py import Exa

    exa = Exa(api_key=credentials["api_key"])
    response = await asyncio.wait_for(
        asyncio.to_thread(
            exa.search_and_contents,
            query,
            num_results=max_results,
            text=True,
        ),
        timeout=30.0,
    )
    return [
        SearchResult(
            title=r.title or "",
            url=r.url or "",
            content=(r.text or "")[:2000],
            score=min(max(r.score or 0.0, 0.0), 1.0),
        )
        for r in response.results
    ]


async def _search_brave(
    query: str, credentials: dict, max_results: int, search_depth: str
) -> list[SearchResult]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": max_results},
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": credentials["api_key"],
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()

    results = []
    for r in data.get("web", {}).get("results", [])[:max_results]:
        results.append(SearchResult(
            title=r.get("title", ""),
            url=r.get("url", ""),
            content=r.get("description", ""),
            score=0.0,
        ))
    return results


async def _search_serper(
    query: str, credentials: dict, max_results: int, search_depth: str
) -> list[SearchResult]:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://google.serper.dev/search",
            json={"q": query, "num": max_results},
            headers={
                "X-API-KEY": credentials["api_key"],
                "Content-Type": "application/json",
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()

    results = []
    for r in data.get("organic", [])[:max_results]:
        results.append(SearchResult(
            title=r.get("title", ""),
            url=r.get("link", ""),
            content=r.get("snippet", ""),
            score=0.0,
        ))
    return results


async def _search_duckduckgo(
    query: str, credentials: dict, max_results: int, search_depth: str
) -> list[SearchResult]:
    import asyncio
    from ddgs import DDGS

    def _sync_search():
        return DDGS().text(query, max_results=max_results)

    raw = await asyncio.wait_for(
        asyncio.to_thread(_sync_search),
        timeout=15.0,
    )

    return [
        SearchResult(
            title=r.get("title", ""),
            url=r.get("href", ""),
            content=r.get("body", ""),
            score=0.0,
        )
        for r in raw
    ]


_ADAPTERS = {
    "tavily": _search_tavily,
    "exa": _search_exa,
    "brave_search": _search_brave,
    "serper": _search_serper,
    "duckduckgo": _search_duckduckgo,
}
