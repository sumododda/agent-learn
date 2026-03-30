"""Academic search dataclass and ranking utilities.

Standalone module providing AcademicResult and associated scoring/dedup logic,
ported from search_service.py but adapted to use `abstract` instead of `content`.
"""
import logging
import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_ACADEMIC_STOPWORDS = {
    "about", "across", "after", "also", "among", "analysis", "and", "are", "build", "building",
    "common", "course", "design", "different", "exact", "for", "from", "guide", "how", "into",
    "latest", "learn", "overview", "paper", "papers", "practical", "recent", "research", "resources",
    "rules", "safe", "safely", "survey", "system", "systems", "that", "the", "their", "them", "these",
    "this", "those", "through", "using", "what", "when", "where", "which", "with", "without", "your",
}

_ACADEMIC_SIGNAL_TERMS = {
    "agent", "agents", "agentic", "alignment", "attack", "attacks", "audit", "autonomous", "defense",
    "defenses", "exploit", "exploits", "function", "functions", "governance", "guardrail", "guardrails",
    "injection", "jailbreak", "jailbreaks", "llm", "llms", "memory", "poison", "poisoning", "policy",
    "policies", "privacy", "prompt", "prompts", "risk", "risks", "safe", "safety", "sandbox",
    "sandboxing", "secure", "security", "tool", "tools", "vulnerability", "vulnerabilities",
}

_BIOMEDICAL_TERMS = {
    "alzheimer", "biomedical", "cancer", "clinical", "drug", "drugs", "gene", "genes", "health",
    "healthcare", "human", "humans", "medicine", "medical", "nanoparticles", "oncology", "patient",
    "patients", "prostate", "protein", "proteins", "therapy", "therapies", "tumor", "tumors",
}

_SECURITY_VENUE_TERMS = {
    "ccs", "crypto", "ndss", "privacy", "sec", "security", "sp", "usenix",
}


@dataclass
class AcademicResult:
    title: str
    url: str
    abstract: str
    authors: list[str]
    year: int | None = None
    venue: str | None = None
    citation_count: int | None = None
    doi: str | None = None
    pdf_url: str | None = None
    score: float = 0.0


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


def _metadata_richness(r: AcademicResult) -> int:
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


def deduplicate_results(results: list[AcademicResult]) -> list[AcademicResult]:
    """Remove duplicate papers, keeping the result with richest metadata."""
    seen_dois: dict[str, int] = {}
    seen_titles: dict[str, int] = {}
    output: list[AcademicResult] = []

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


def rank_for_deep_reading(result: AcademicResult) -> float:
    """Rank an academic paper for deep reading. Returns -1 if no PDF available."""
    if not result.pdf_url:
        return -1
    citations = result.citation_count or 0
    year = result.year or 2020
    age = date.today().year - year
    if age <= 2:
        recency = 3.0
    elif age <= 5:
        recency = 2.0
    else:
        recency = 1.0
    return math.log1p(citations) * recency


def _tokenize(text: str | None) -> set[str]:
    """Tokenize text into a small, lowercase keyword set for heuristic ranking."""
    if not text:
        return set()
    normalized = unicodedata.normalize("NFKD", text).lower()
    tokens = re.findall(r"[a-z0-9]+", normalized)
    return {
        token
        for token in tokens
        if len(token) >= 3 and token not in _ACADEMIC_STOPWORDS
    }


def _recency_score(year: int | None) -> float:
    """Return a freshness boost that strongly prefers the last 1-2 years."""
    if not year:
        return 0.8
    age = max(0, date.today().year - year)
    if age == 0:
        return 3.2
    if age == 1:
        return 2.8
    if age == 2:
        return 2.2
    if age <= 4:
        return 1.5
    if age <= 6:
        return 1.0
    return 0.6


def score_result(
    result: AcademicResult,
    query: str,
    topic: str | None = None,
) -> float:
    """Heuristically score an academic result for discovery relevance."""
    context_tokens = _tokenize(f"{query} {topic or ''}")
    result_tokens = _tokenize(
        " ".join(
            [
                result.title,
                result.abstract,
                result.venue or "",
                " ".join(result.authors or []),
            ]
        )
    )

    lexical_overlap = len(context_tokens & result_tokens) / max(len(context_tokens), 1)

    signal_query_terms = context_tokens & _ACADEMIC_SIGNAL_TERMS
    signal_matches = signal_query_terms & result_tokens
    signal_overlap = len(signal_matches) / max(len(signal_query_terms), 1) if signal_query_terms else 0.0

    citation_score = math.log1p(result.citation_count or 0) * 0.35
    provider_score = math.log1p(max(result.score or 0.0, 0.0)) * 0.4
    recency = _recency_score(result.year)

    venue_tokens = _tokenize(result.venue or "")
    venue_boost = 0.6 if (signal_query_terms and venue_tokens & _SECURITY_VENUE_TERMS) else 0.0

    penalty = 0.0
    if signal_query_terms and not signal_matches:
        penalty -= 3.5

    biomedical_terms = result_tokens & _BIOMEDICAL_TERMS
    if biomedical_terms and not (context_tokens & _BIOMEDICAL_TERMS):
        penalty -= min(2.5, 0.9 * len(biomedical_terms))

    return (
        lexical_overlap * 4.0
        + signal_overlap * 2.5
        + citation_score
        + provider_score
        + recency
        + venue_boost
        + penalty
    )


def rerank_results(
    results: list[AcademicResult],
    query: str,
    topic: str | None = None,
) -> list[AcademicResult]:
    """Sort academic results by a hybrid topicality/freshness/authority score."""
    return sorted(
        results,
        key=lambda r: (
            score_result(r, query=query, topic=topic),
            r.citation_count or 0,
            r.year or 0,
        ),
        reverse=True,
    )


def select_for_discovery(
    results: list[AcademicResult],
    query: str,
    topic: str | None = None,
    limit: int = 10,
    recent_target: int = 3,
) -> list[AcademicResult]:
    """Keep a mix of fresh and foundational academic papers for discovery."""
    ranked = rerank_results(results, query=query, topic=topic)
    recent_cutoff = date.today().year - 1

    selected: list[AcademicResult] = []
    for result in ranked:
        if len(selected) >= recent_target:
            break
        if result.year and result.year >= recent_cutoff:
            selected.append(result)

    for result in ranked:
        if len(selected) >= limit:
            break
        if result not in selected:
            selected.append(result)

    return selected[:limit]


# ---------------------------------------------------------------------------
# Unpaywall PDF enrichment
# ---------------------------------------------------------------------------

async def _enrich_with_unpaywall(results: list[AcademicResult]) -> list[AcademicResult]:
    """Enrich results that have DOIs but no pdf_url via Unpaywall."""
    if not settings.UNPAYWALL_EMAIL:
        logger.warning("[unpaywall] No email configured, skipping enrichment")
        return results

    needs_enrichment = [
        (i, r) for i, r in enumerate(results)
        if r.doi and not r.pdf_url
    ]

    if not needs_enrichment:
        return results

    async def _fetch_pdf_url(doi: str) -> str | None:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"https://api.unpaywall.org/v2/{doi}",
                    params={"email": settings.UNPAYWALL_EMAIL},
                    timeout=10.0,
                )
                resp.raise_for_status()
                data = resp.json()
                oa_loc = data.get("best_oa_location")
                if not oa_loc:
                    return None
                return oa_loc.get("url_for_pdf") or oa_loc.get("url")
        except Exception:
            return None

    import asyncio
    pdf_urls = await asyncio.gather(
        *[_fetch_pdf_url(r.doi) for _, r in needs_enrichment],
        return_exceptions=True,
    )

    for (idx, _), pdf_url in zip(needs_enrichment, pdf_urls):
        if isinstance(pdf_url, str):
            results[idx].pdf_url = pdf_url

    return results


# ---------------------------------------------------------------------------
# OpenAlex adapter
# ---------------------------------------------------------------------------

def _year_range_to_openalex_filter(year_range: str) -> str:
    if year_range == "all":
        return ""
    current_year = date.today().year
    years_back = {"5y": 5, "10y": 10, "20y": 20}
    n = years_back.get(year_range, 5)
    return f"publication_year:>{current_year - n}"


async def _search_openalex(
    query: str,
    max_results: int = 10,
    options: dict | None = None,
) -> list[AcademicResult]:
    if not settings.OPENALEX_API_KEY:
        logger.warning("[openalex] No API key configured, skipping")
        return []

    opts = options or {}
    params: dict = {
        "search": query,
        "per_page": min(max_results, 100),
        "api_key": settings.OPENALEX_API_KEY,
        "select": "id,doi,title,display_name,relevance_score,publication_year,cited_by_count,authorships,abstract_inverted_index,primary_location,open_access",
    }

    filters: list[str] = []
    year_filter = _year_range_to_openalex_filter(opts.get("year_range", "all"))
    if year_filter:
        filters.append(year_filter)
        params["sort"] = "publication_date:desc"
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
        pdf_url = loc.get("pdf_url") or (work.get("open_access") or {}).get("oa_url")

        results.append(AcademicResult(
            title=work.get("title") or work.get("display_name", ""),
            url=url,
            abstract=abstract,
            authors=authors,
            year=work.get("publication_year"),
            venue=venue,
            citation_count=work.get("cited_by_count"),
            doi=doi,
            pdf_url=pdf_url,
            score=work.get("relevance_score", 0.0),
        ))

    return results


# ---------------------------------------------------------------------------
# Serper Scholar adapter
# ---------------------------------------------------------------------------

def _parse_publication_info(pub_info: str) -> tuple[list[str], str | None]:
    """Parse Serper Scholar publicationInfo string into (authors, venue).

    Format: "A Author, B Author... - Venue Name, Year - domain.com"
    """
    if not pub_info:
        return [], None
    parts = pub_info.split(" - ")
    authors = []
    venue = None
    if len(parts) >= 1:
        authors = [a.strip() for a in parts[0].split(", ") if a.strip() and not a.strip().startswith("…")]
    if len(parts) >= 2:
        venue_raw = parts[1].strip()
        venue_cleaned = re.sub(r",?\s*\d{4}\s*$", "", venue_raw).strip()
        venue = venue_cleaned or None
    return authors, venue


async def _search_serper_scholar(
    query: str,
    max_results: int = 10,
    options: dict | None = None,
) -> list[AcademicResult]:
    if not settings.SERPER_API_KEY:
        logger.warning("[serper_scholar] No API key configured, skipping")
        return []

    opts = options or {}
    body: dict = {"q": query, "num": min(max_results, 100)}

    year_range = opts.get("year_range", "all")
    if year_range != "all":
        years_back = {"5y": 5, "10y": 10, "20y": 20}
        n = years_back.get(year_range, 5)
        body["as_ylo"] = date.today().year - n

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://google.serper.dev/scholar",
            json=body,
            headers={
                "X-API-KEY": settings.SERPER_API_KEY,
                "Content-Type": "application/json",
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()

    results = []
    for item in data.get("organic", []):
        authors, venue = _parse_publication_info(item.get("publicationInfo", ""))
        results.append(AcademicResult(
            title=item.get("title", ""),
            url=item.get("link", ""),
            abstract=item.get("snippet", ""),
            authors=authors,
            year=item.get("year"),
            venue=venue,
            citation_count=item.get("citedBy"),
            doi=None,  # Serper Scholar does not return DOIs
            pdf_url=item.get("pdfLink"),
            score=0.0,
        ))

    return results


# ---------------------------------------------------------------------------
# Main academic search function
# ---------------------------------------------------------------------------

async def academic_search(
    query: str,
    max_results: int = 10,
    options: dict | None = None,
) -> list[AcademicResult]:
    """Search OpenAlex + Serper Scholar in parallel, deduplicate, enrich, rank."""
    import asyncio

    fetch_limit = min(max(max_results * 2, max_results), 12)

    async def _safe_openalex() -> list[AcademicResult]:
        try:
            return await _search_openalex(query, fetch_limit, options)
        except Exception as e:
            logger.warning("[academic_search] OpenAlex failed for '%s': %s", query[:60], e)
            return []

    async def _safe_serper() -> list[AcademicResult]:
        try:
            return await _search_serper_scholar(query, fetch_limit, options)
        except Exception as e:
            logger.warning("[academic_search] Serper Scholar failed for '%s': %s", query[:60], e)
            return []

    oa_results, serper_results = await asyncio.gather(_safe_openalex(), _safe_serper())

    all_results = oa_results + serper_results
    if not all_results:
        return []

    deduplicated = deduplicate_results(all_results)
    ranked = rerank_results(deduplicated, query=query)
    top = ranked[:max_results]

    enriched = await _enrich_with_unpaywall(top)
    return enriched
