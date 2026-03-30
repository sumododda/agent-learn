"""Academic search dataclass and ranking utilities.

Standalone module providing AcademicResult and associated scoring/dedup logic,
ported from search_service.py but adapted to use `abstract` instead of `content`.
"""
import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

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
