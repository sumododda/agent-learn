"""Multi-provider search service with unified interface.

Mirrors provider_service.py pattern but for web search providers.
Each adapter normalizes results into SearchResult dataclass.
"""
import logging
import re
import unicodedata
import xml.etree.ElementTree as ET
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

ACADEMIC_SEARCH_PROVIDERS = {
    "semantic_scholar": {
        "name": "Semantic Scholar",
        "fields": [
            {"key": "api_key", "label": "API Key (optional)", "type": "password", "required": False, "secret": True},
        ],
    },
    "arxiv": {
        "name": "arXiv",
        "fields": [],
    },
    "openalex": {
        "name": "OpenAlex",
        "fields": [
            {"key": "api_key", "label": "API Key", "type": "password", "required": True, "secret": True},
        ],
    },
}


def get_academic_search_provider_registry() -> dict:
    """Return academic search provider definitions for frontend form rendering."""
    return ACADEMIC_SEARCH_PROVIDERS


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


def _normalize_title(title: str) -> str:
    """Normalize a paper title for comparison."""
    title = unicodedata.normalize("NFKD", title).lower()
    title = re.sub(r"[^\w\s]", "", title)
    return " ".join(title.split())


def _metadata_richness(r: SearchResult) -> int:
    """Score how many metadata fields are populated (higher = richer)."""
    score = 0
    if r.authors:
        score += 1
    if r.year:
        score += 1
    if r.venue:
        score += 1
    if r.citation_count is not None:
        score += 1
    if r.doi:
        score += 1
    return score


def deduplicate_academic_results(results: list[SearchResult]) -> list[SearchResult]:
    """Remove duplicate papers, keeping the result with richest metadata."""
    seen_dois: dict[str, int] = {}
    seen_titles: dict[str, int] = {}
    output: list[SearchResult] = []

    for r in results:
        if r.doi:
            doi_key = r.doi.lower().strip()
            if doi_key in seen_dois:
                idx = seen_dois[doi_key]
                if _metadata_richness(r) > _metadata_richness(output[idx]):
                    output[idx] = r
                continue
            seen_dois[doi_key] = len(output)

        norm_title = _normalize_title(r.title)
        if norm_title in seen_titles:
            idx = seen_titles[norm_title]
            if _metadata_richness(r) > _metadata_richness(output[idx]):
                output[idx] = r
            continue
        seen_titles[norm_title] = len(output)

        output.append(r)

    return output


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


# ---------------------------------------------------------------------------
# Academic search adapters
# ---------------------------------------------------------------------------


def _year_range_to_s2_param(year_range: str) -> str | None:
    """Convert year_range option to Semantic Scholar year param."""
    if year_range == "all":
        return None
    from datetime import date
    current_year = date.today().year
    years_back = {"5y": 5, "10y": 10, "20y": 20}
    n = years_back.get(year_range, 5)
    return f"{current_year - n}-"


async def _search_semantic_scholar(
    query: str,
    credentials: dict,
    max_results: int,
    search_depth: str,
    academic_options: dict | None = None,
) -> list[SearchResult]:
    opts = academic_options or {}
    params: dict = {
        "query": query,
        "fields": "title,url,abstract,year,authors,venue,citationCount,externalIds,openAccessPdf,publicationTypes",
        "limit": min(max_results, 100),
    }
    year_param = _year_range_to_s2_param(opts.get("year_range", "all"))
    if year_param:
        params["year"] = year_param
    min_cit = opts.get("min_citations", 0)
    if min_cit and min_cit > 0:
        params["minCitationCount"] = str(min_cit)

    headers: dict[str, str] = {}
    api_key = credentials.get("api_key")
    if api_key:
        headers["x-api-key"] = api_key

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params=params,
            headers=headers,
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()

    open_access_only = opts.get("open_access_only", False)
    results = []
    for paper in data.get("data", []):
        abstract = paper.get("abstract")
        if not abstract:
            continue
        if open_access_only and not paper.get("openAccessPdf"):
            continue
        ext_ids = paper.get("externalIds") or {}
        results.append(SearchResult(
            title=paper.get("title", ""),
            url=paper.get("url") or f"https://www.semanticscholar.org/paper/{paper.get('paperId', '')}",
            content=abstract,
            score=0.0,
            authors=[a["name"] for a in paper.get("authors", [])],
            year=paper.get("year"),
            venue=paper.get("venue") or None,
            citation_count=paper.get("citationCount"),
            doi=ext_ids.get("DOI"),
            is_academic=True,
        ))
    return results


def _year_range_to_arxiv_date_filter(year_range: str) -> str:
    """Convert year_range to arXiv submittedDate query fragment."""
    if year_range == "all":
        return ""
    from datetime import date
    current_year = date.today().year
    years_back = {"5y": 5, "10y": 10, "20y": 20}
    n = years_back.get(year_range, 5)
    start_year = current_year - n
    return f"+AND+submittedDate:[{start_year}01010000+TO+{current_year}12312359]"


async def _search_arxiv(
    query: str,
    credentials: dict,
    max_results: int,
    search_depth: str,
    academic_options: dict | None = None,
) -> list[SearchResult]:
    opts = academic_options or {}
    search_query = f"all:{query}"
    date_filter = _year_range_to_arxiv_date_filter(opts.get("year_range", "all"))
    if date_filter:
        search_query += date_filter

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "http://export.arxiv.org/api/query",
            params={
                "search_query": search_query,
                "start": 0,
                "max_results": min(max_results, 100),
                "sortBy": "relevance",
                "sortOrder": "descending",
            },
            timeout=30.0,
        )
        resp.raise_for_status()

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }
    root = ET.fromstring(resp.text)
    results = []

    for entry in root.findall("atom:entry", ns):
        title_el = entry.find("atom:title", ns)
        summary_el = entry.find("atom:summary", ns)
        title = " ".join((title_el.text or "").split()) if title_el is not None else ""
        abstract = " ".join((summary_el.text or "").split()) if summary_el is not None else ""
        if not abstract:
            continue

        authors = []
        for author_el in entry.findall("atom:author", ns):
            name_el = author_el.find("atom:name", ns)
            if name_el is not None and name_el.text:
                authors.append(name_el.text)

        published_el = entry.find("atom:published", ns)
        year = None
        if published_el is not None and published_el.text:
            year = int(published_el.text[:4])

        doi_el = entry.find("arxiv:doi", ns)
        doi = doi_el.text if doi_el is not None else None

        url = ""
        for link_el in entry.findall("atom:link", ns):
            if link_el.get("rel") == "alternate":
                url = link_el.get("href", "")
                break
        if not url:
            id_el = entry.find("atom:id", ns)
            url = id_el.text if id_el is not None else ""

        results.append(SearchResult(
            title=title,
            url=url,
            content=abstract,
            score=0.0,
            authors=authors,
            year=year,
            venue="arXiv",
            citation_count=None,
            doi=doi,
            is_academic=True,
        ))

    return results


def _year_range_to_openalex_filter(year_range: str) -> str:
    """Convert year_range to OpenAlex publication_year filter."""
    if year_range == "all":
        return ""
    from datetime import date
    current_year = date.today().year
    years_back = {"5y": 5, "10y": 10, "20y": 20}
    n = years_back.get(year_range, 5)
    return f"publication_year:>{current_year - n}"


async def _search_openalex(
    query: str,
    credentials: dict,
    max_results: int,
    search_depth: str,
    academic_options: dict | None = None,
) -> list[SearchResult]:
    opts = academic_options or {}
    api_key = credentials.get("api_key")
    if not api_key:
        logger.warning("[openalex] No API key configured, skipping")
        return []

    params: dict = {
        "search": query,
        "per_page": min(max_results, 100),
        "api_key": api_key,
    }

    filters: list[str] = []
    year_filter = _year_range_to_openalex_filter(opts.get("year_range", "all"))
    if year_filter:
        filters.append(year_filter)
    min_cit = opts.get("min_citations", 0)
    if min_cit and min_cit > 0:
        filters.append(f"cited_by_count:>{min_cit}")
    if opts.get("open_access_only", False):
        filters.append("is_oa:true")
    if filters:
        params["filter"] = ",".join(filters)

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.openalex.org/works",
            params=params,
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()

    results = []
    for work in data.get("results", []):
        abstract = reconstruct_abstract(work.get("abstract_inverted_index"))
        if not abstract:
            continue

        authors = [
            a["author"]["display_name"]
            for a in work.get("authorships", [])
            if a.get("author", {}).get("display_name")
        ]

        doi_raw = work.get("doi") or ""
        doi = doi_raw.replace("https://doi.org/", "") if doi_raw else None

        loc = work.get("primary_location") or {}
        source = loc.get("source") or {}
        venue = source.get("display_name")

        url = loc.get("landing_page_url") or work.get("id", "")

        results.append(SearchResult(
            title=work.get("title") or work.get("display_name", ""),
            url=url,
            content=abstract,
            score=work.get("relevance_score", 0.0),
            authors=authors,
            year=work.get("publication_year"),
            venue=venue,
            citation_count=work.get("cited_by_count"),
            doi=doi,
            is_academic=True,
        ))

    return results


_ACADEMIC_ADAPTERS: dict[str, str] = {
    "semantic_scholar": "_search_semantic_scholar",
    "arxiv": "_search_arxiv",
    "openalex": "_search_openalex",
}


def _get_academic_adapter(name: str):
    """Resolve an academic adapter function by provider name.

    Uses string-based lookup so that unittest.mock.patch on the module-level
    function names is respected at call time.
    """
    func_name = _ACADEMIC_ADAPTERS.get(name)
    if not func_name:
        return None
    import sys
    return getattr(sys.modules[__name__], func_name, None)


async def academic_search(
    query: str,
    academic_credentials: dict[str, dict],
    academic_options: dict,
    max_results: int = 5,
) -> list[SearchResult]:
    """Search all configured academic providers, deduplicate, return merged results."""
    import asyncio

    async def _run_provider(name: str, creds: dict) -> list[SearchResult]:
        adapter = _get_academic_adapter(name)
        if not adapter:
            return []
        try:
            return await adapter(query, creds, max_results, "basic", academic_options)
        except Exception as e:
            logger.warning("[academic_search] %s failed for '%s': %s", name, query[:60], e)
            return []

    # S2 + OpenAlex in parallel, arXiv sequential (rate limit)
    parallel_providers = {}
    arxiv_creds = None
    for name, creds in academic_credentials.items():
        if name == "arxiv":
            arxiv_creds = creds
        elif name in _ACADEMIC_ADAPTERS:
            parallel_providers[name] = creds

    tasks = [_run_provider(name, creds) for name, creds in parallel_providers.items()]
    parallel_results = await asyncio.gather(*tasks)
    all_results = [r for batch in parallel_results for r in batch]

    if arxiv_creds is not None:
        arxiv_results = await _run_provider("arxiv", arxiv_creds)
        all_results.extend(arxiv_results)

    return deduplicate_academic_results(all_results)


_ADAPTERS = {
    "tavily": _search_tavily,
    "exa": _search_exa,
    "brave_search": _search_brave,
    "serper": _search_serper,
    "duckduckgo": _search_duckduckgo,
}
