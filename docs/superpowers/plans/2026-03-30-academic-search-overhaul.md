# Academic Search Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fragile multi-provider academic search (Semantic Scholar, arXiv, OpenAlex) with a clean two-provider system (OpenAlex + Serper Scholar) using server-level API keys, plus Unpaywall PDF enrichment.

**Architecture:** New standalone `backend/app/academic_search.py` module with server keys from `Settings`. Existing `search_service.py` retains only web search. All callers drop `academic_credentials` param threading. Per-user academic provider CRUD (routes, frontend, key_cache) gets deleted.

**Tech Stack:** Python/FastAPI, httpx, asyncio, Pydantic Settings, Kubernetes secrets

**Spec:** `docs/superpowers/specs/2026-03-30-academic-search-overhaul-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `backend/app/academic_search.py` | New module: AcademicResult dataclass, OpenAlex adapter, Serper Scholar adapter, Unpaywall enrichment, dedup, ranking, main `academic_search()` function |
| Create | `backend/tests/test_academic_search_new.py` | Tests for new academic_search module (rename to `test_academic_search.py` after old one deleted) |
| Modify | `backend/app/config.py` | Add `OPENALEX_API_KEY`, `SERPER_API_KEY`, `UNPAYWALL_EMAIL` |
| Modify | `backend/app/agent_service.py` | Remove `academic_credentials` param, import from `academic_search` |
| Modify | `backend/app/pipeline.py` | Remove `academic_credentials` param threading |
| Modify | `backend/app/worker.py` | Remove academic credential decryption |
| Modify | `backend/app/routers/courses.py` | Remove `_get_user_academic_credentials()`, simplify |
| Modify | `backend/app/paper_reader.py` | Import `AcademicResult` from `academic_search` instead of `SearchResult` |
| Modify | `backend/app/main.py` | Remove academic_provider_routes router |
| Modify | `backend/app/search_service.py` | Remove all academic code |
| Modify | `backend/app/key_cache.py` | Remove `get_all_academic_providers()` |
| Delete | `backend/app/routers/academic_provider_routes.py` | Per-user academic CRUD routes |
| Delete | `backend/tests/test_academic_search.py` | Old tests (replaced by new) |
| Modify | `backend/tests/test_courses.py` | Remove academic credential mocks |
| Modify | `backend/tests/test_discovery_streaming.py` | Update mock targets |
| Modify | `deploy/k8s/backend-api.yaml` | Add env vars for new secrets |
| Modify | `deploy/k8s/backend-worker.yaml` | Add env vars for new secrets |
| Modify | `frontend/src/app/settings/page.tsx` | Remove AcademicProviderSection |
| Modify | `frontend/src/lib/api.ts` | Remove academic provider API calls |

---

### Task 1: Add Server-Level Config Keys

**Files:**
- Modify: `backend/app/config.py:4-18`
- Modify: `backend/.env.example`

- [ ] **Step 1: Add settings fields**

In `backend/app/config.py`, add three new fields to the `Settings` class after `DOCS_ENABLED`:

```python
OPENALEX_API_KEY: str = ""
SERPER_API_KEY: str = ""
UNPAYWALL_EMAIL: str = ""
```

- [ ] **Step 2: Update .env.example**

Add to `backend/.env.example`:

```
OPENALEX_API_KEY=
SERPER_API_KEY=
UNPAYWALL_EMAIL=
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/config.py backend/.env.example
git commit -m "feat: add server-level academic search config keys"
```

---

### Task 2: Add K8s Secret References

**Files:**
- Modify: `deploy/k8s/backend-api.yaml:43-77`
- Modify: `deploy/k8s/backend-worker.yaml:30-64`

- [ ] **Step 1: Add env vars to backend-api.yaml**

Add these env entries to the `backend-api` container's `env:` section, after the existing `RESEND_FROM_EMAIL` block:

```yaml
            - name: OPENALEX_API_KEY
              valueFrom:
                secretKeyRef:
                  name: app-secrets
                  key: openalex-api-key
            - name: SERPER_API_KEY
              valueFrom:
                secretKeyRef:
                  name: app-secrets
                  key: serper-api-key
            - name: UNPAYWALL_EMAIL
              valueFrom:
                secretKeyRef:
                  name: app-secrets
                  key: unpaywall-email
```

- [ ] **Step 2: Add same env vars to backend-worker.yaml**

Add the identical three env entries to the `backend-worker` container's `env:` section, after `RESEND_FROM_EMAIL`.

- [ ] **Step 3: Commit**

```bash
git add deploy/k8s/backend-api.yaml deploy/k8s/backend-worker.yaml
git commit -m "feat: add academic search API keys to k8s deployments"
```

---

### Task 3: Create AcademicResult Dataclass and Utility Functions

**Files:**
- Create: `backend/app/academic_search.py`
- Create: `backend/tests/test_academic_search_new.py`

- [ ] **Step 1: Write failing tests for AcademicResult and utilities**

Create `backend/tests/test_academic_search_new.py`:

```python
from datetime import date

import pytest

from app.academic_search import (
    AcademicResult,
    deduplicate_results,
    reconstruct_abstract,
    rerank_results,
    score_result,
    select_for_discovery,
    rank_for_deep_reading,
)


def test_academic_result_defaults():
    r = AcademicResult(title="Test", url="http://ex.com", abstract="Abstract text", authors=["A"])
    assert r.year is None
    assert r.citation_count is None
    assert r.doi is None
    assert r.pdf_url is None
    assert r.score == 0.0


def test_reconstruct_abstract():
    inverted = {"Machine": [0], "learning": [1], "is": [2], "great": [3]}
    assert reconstruct_abstract(inverted) == "Machine learning is great"


def test_reconstruct_abstract_empty():
    assert reconstruct_abstract(None) == ""
    assert reconstruct_abstract({}) == ""


def test_deduplicate_by_doi():
    r1 = AcademicResult(title="Paper A", url="http://a", abstract="...", authors=["X"], doi="10.1/a", year=2024, citation_count=10)
    r2 = AcademicResult(title="Paper A (copy)", url="http://b", abstract="...", authors=["X"], doi="10.1/a", year=2024, venue="NeurIPS", citation_count=10)
    results = deduplicate_results([r1, r2])
    assert len(results) == 1
    assert results[0].venue == "NeurIPS"  # richer metadata wins


def test_deduplicate_by_title():
    r1 = AcademicResult(title="Attention Is All You Need", url="http://a", abstract="...", authors=["V"])
    r2 = AcademicResult(title="attention is all you need", url="http://b", abstract="...", authors=["V"], citation_count=100)
    results = deduplicate_results([r1, r2])
    assert len(results) == 1
    assert results[0].citation_count == 100


def test_rank_for_deep_reading_no_pdf():
    r = AcademicResult(title="T", url="u", abstract="a", authors=["A"], pdf_url=None)
    assert rank_for_deep_reading(r) == -1


def test_rank_for_deep_reading_with_pdf():
    r = AcademicResult(title="T", url="u", abstract="a", authors=["A"], pdf_url="http://pdf", citation_count=100, year=date.today().year)
    score = rank_for_deep_reading(r)
    assert score > 0


def test_score_result_penalizes_missing_signal_terms():
    r = AcademicResult(title="Cooking recipes", url="u", abstract="How to bake a cake", authors=["Chef"], year=2024)
    score = score_result(r, query="agent security vulnerabilities")
    assert score < 5.0


def test_rerank_orders_by_score():
    r1 = AcademicResult(title="agent security framework", url="u", abstract="security agents vulnerabilities", authors=["A"], year=2024, citation_count=50)
    r2 = AcademicResult(title="cooking tips", url="u2", abstract="how to cook", authors=["B"], year=2020, citation_count=5)
    ranked = rerank_results([r2, r1], query="agent security")
    assert ranked[0].title == "agent security framework"


def test_select_for_discovery_limits():
    results = [
        AcademicResult(title=f"Paper {i}", url=f"u{i}", abstract="abs", authors=["A"], year=2024 - i, citation_count=i * 10)
        for i in range(20)
    ]
    selected = select_for_discovery(results, query="test", limit=5)
    assert len(selected) == 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/sumo/agent-learn && python -m pytest backend/tests/test_academic_search_new.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.academic_search'`

- [ ] **Step 3: Create academic_search.py with dataclass and utilities**

Create `backend/app/academic_search.py`:

```python
"""Academic search module — server-level keys, OpenAlex + Serper Scholar + Unpaywall.

Standalone module with zero dependency on the per-user key system.
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


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class AcademicResult:
    title: str
    url: str
    abstract: str
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    citation_count: int | None = None
    doi: str | None = None
    pdf_url: str | None = None
    score: float = 0.0


# ---------------------------------------------------------------------------
# Abstract reconstruction (OpenAlex inverted index)
# ---------------------------------------------------------------------------

def reconstruct_abstract(inverted_index: dict | None) -> str:
    if not inverted_index:
        return ""
    words: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        for pos in positions:
            words.append((pos, word))
    words.sort(key=lambda x: x[0])
    return " ".join(w for _, w in words)


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def _normalize_title(title: str) -> str:
    title = unicodedata.normalize("NFKD", title).lower()
    title = re.sub(r"[^\w\s]", "", title)
    return " ".join(title.split())


def _metadata_richness(r: AcademicResult) -> int:
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


# ---------------------------------------------------------------------------
# Ranking / scoring
# ---------------------------------------------------------------------------

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


def _tokenize(text: str | None) -> set[str]:
    if not text:
        return set()
    normalized = unicodedata.normalize("NFKD", text).lower()
    tokens = re.findall(r"[a-z0-9]+", normalized)
    return {t for t in tokens if len(t) >= 3 and t not in _ACADEMIC_STOPWORDS}


def _recency_score(year: int | None) -> float:
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


def score_result(result: AcademicResult, query: str, topic: str | None = None) -> float:
    context_tokens = _tokenize(f"{query} {topic or ''}")
    result_tokens = _tokenize(
        " ".join([result.title, result.abstract, result.venue or "", " ".join(result.authors)])
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


def rerank_results(results: list[AcademicResult], query: str, topic: str | None = None) -> list[AcademicResult]:
    return sorted(
        results,
        key=lambda r: (score_result(r, query=query, topic=topic), r.citation_count or 0, r.year or 0),
        reverse=True,
    )


def select_for_discovery(
    results: list[AcademicResult],
    query: str,
    topic: str | None = None,
    limit: int = 10,
    recent_target: int = 3,
) -> list[AcademicResult]:
    ranked = rerank_results(results, query=query, topic=topic)
    recent_cutoff = date.today().year - 1

    selected: list[AcademicResult] = []
    for r in ranked:
        if len(selected) >= recent_target:
            break
        if r.year and r.year >= recent_cutoff:
            selected.append(r)

    for r in ranked:
        if len(selected) >= limit:
            break
        if r not in selected:
            selected.append(r)

    return selected[:limit]


def rank_for_deep_reading(result: AcademicResult) -> float:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/sumo/agent-learn && python -m pytest backend/tests/test_academic_search_new.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/academic_search.py backend/tests/test_academic_search_new.py
git commit -m "feat: add AcademicResult dataclass and ranking utilities"
```

---

### Task 4: OpenAlex Adapter

**Files:**
- Modify: `backend/app/academic_search.py`
- Modify: `backend/tests/test_academic_search_new.py`

- [ ] **Step 1: Write failing test for OpenAlex adapter**

Append to `backend/tests/test_academic_search_new.py`:

```python
import httpx
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.asyncio
async def test_openalex_adapter_maps_fields():
    from app.academic_search import _search_openalex

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "meta": {"count": 1},
        "results": [
            {
                "title": "Test Paper",
                "display_name": "Test Paper",
                "id": "https://openalex.org/W123",
                "doi": "https://doi.org/10.1234/test",
                "relevance_score": 15.5,
                "publication_year": 2024,
                "cited_by_count": 42,
                "authorships": [
                    {"author": {"display_name": "Alice Smith"}},
                    {"author": {"display_name": "Bob Jones"}},
                ],
                "abstract_inverted_index": {"Test": [0], "abstract": [1], "here": [2]},
                "primary_location": {
                    "landing_page_url": "https://example.com/paper",
                    "pdf_url": "https://example.com/paper.pdf",
                    "source": {"display_name": "Nature"},
                },
                "open_access": {"oa_url": None},
            }
        ],
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("app.academic_search.httpx.AsyncClient", return_value=mock_client):
        with patch("app.academic_search.settings") as mock_settings:
            mock_settings.OPENALEX_API_KEY = "test-key"
            results = await _search_openalex("machine learning", max_results=5)

    assert len(results) == 1
    r = results[0]
    assert r.title == "Test Paper"
    assert r.authors == ["Alice Smith", "Bob Jones"]
    assert r.year == 2024
    assert r.citation_count == 42
    assert r.doi == "10.1234/test"
    assert r.venue == "Nature"
    assert r.pdf_url == "https://example.com/paper.pdf"
    assert r.abstract == "Test abstract here"
    assert r.score == 15.5

    # Verify API key passed as query param
    call_kwargs = mock_client.get.call_args
    assert call_kwargs.kwargs["params"]["api_key"] == "test-key"


@pytest.mark.asyncio
async def test_openalex_adapter_skips_no_abstract():
    from app.academic_search import _search_openalex

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "meta": {"count": 1},
        "results": [{"title": "No Abstract", "abstract_inverted_index": None, "authorships": [], "primary_location": None, "open_access": {}}],
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("app.academic_search.httpx.AsyncClient", return_value=mock_client):
        with patch("app.academic_search.settings") as mock_settings:
            mock_settings.OPENALEX_API_KEY = "test-key"
            results = await _search_openalex("test", max_results=5)

    assert len(results) == 0


@pytest.mark.asyncio
async def test_openalex_adapter_returns_empty_when_no_key():
    from app.academic_search import _search_openalex

    with patch("app.academic_search.settings") as mock_settings:
        mock_settings.OPENALEX_API_KEY = ""
        results = await _search_openalex("test", max_results=5)

    assert results == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/sumo/agent-learn && python -m pytest backend/tests/test_academic_search_new.py::test_openalex_adapter_maps_fields -v`
Expected: FAIL — `ImportError: cannot import name '_search_openalex'`

- [ ] **Step 3: Implement OpenAlex adapter**

Add to `backend/app/academic_search.py` after the `rank_for_deep_reading` function:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/sumo/agent-learn && python -m pytest backend/tests/test_academic_search_new.py -v -k openalex`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/academic_search.py backend/tests/test_academic_search_new.py
git commit -m "feat: add OpenAlex adapter to academic_search module"
```

---

### Task 5: Serper Scholar Adapter

**Files:**
- Modify: `backend/app/academic_search.py`
- Modify: `backend/tests/test_academic_search_new.py`

- [ ] **Step 1: Write failing tests for Serper Scholar adapter**

Append to `backend/tests/test_academic_search_new.py`:

```python
@pytest.mark.asyncio
async def test_serper_scholar_adapter_maps_fields():
    from app.academic_search import _search_serper_scholar

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "searchParameters": {"q": "test", "type": "scholar"},
        "organic": [
            {
                "title": "Attention Is All You Need",
                "link": "https://proceedings.neurips.cc/paper/2017/123",
                "snippet": "The dominant sequence transduction models...",
                "publicationInfo": "A Vaswani, N Shazeer, N Parmar - Advances in neural information processing systems, 2017 - proceedings.neurips.cc",
                "citedBy": 119097,
                "year": 2017,
                "pdfLink": "https://example.com/paper.pdf",
            }
        ],
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("app.academic_search.httpx.AsyncClient", return_value=mock_client):
        with patch("app.academic_search.settings") as mock_settings:
            mock_settings.SERPER_API_KEY = "test-key"
            results = await _search_serper_scholar("attention mechanisms", max_results=10)

    assert len(results) == 1
    r = results[0]
    assert r.title == "Attention Is All You Need"
    assert r.url == "https://proceedings.neurips.cc/paper/2017/123"
    assert r.abstract == "The dominant sequence transduction models..."
    assert r.citation_count == 119097
    assert r.year == 2017
    assert r.pdf_url == "https://example.com/paper.pdf"
    assert r.doi is None  # Serper Scholar does not return DOIs
    assert "Vaswani" in r.authors[0]
    assert r.venue is not None

    # Verify correct endpoint and headers
    call_kwargs = mock_client.post.call_args
    assert "scholar" in str(call_kwargs.args[0])
    assert call_kwargs.kwargs["headers"]["X-API-KEY"] == "test-key"


@pytest.mark.asyncio
async def test_serper_scholar_parses_publication_info():
    from app.academic_search import _parse_publication_info

    authors, venue = _parse_publication_info(
        "A Vaswani, N Shazeer, N Parmar - Advances in neural information processing systems, 2017 - proceedings.neurips.cc"
    )
    assert len(authors) >= 3
    assert "A Vaswani" in authors
    assert venue is not None
    assert "neural" in venue.lower()


@pytest.mark.asyncio
async def test_serper_scholar_handles_missing_fields():
    from app.academic_search import _search_serper_scholar

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "organic": [
            {
                "title": "Minimal Paper",
                "link": "https://example.com",
                "snippet": "Some text",
                "publicationInfo": "Author Name - 2023",
            }
        ],
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("app.academic_search.httpx.AsyncClient", return_value=mock_client):
        with patch("app.academic_search.settings") as mock_settings:
            mock_settings.SERPER_API_KEY = "test-key"
            results = await _search_serper_scholar("test", max_results=5)

    assert len(results) == 1
    r = results[0]
    assert r.citation_count is None  # citedBy missing
    assert r.pdf_url is None  # pdfLink missing


@pytest.mark.asyncio
async def test_serper_scholar_returns_empty_when_no_key():
    from app.academic_search import _search_serper_scholar

    with patch("app.academic_search.settings") as mock_settings:
        mock_settings.SERPER_API_KEY = ""
        results = await _search_serper_scholar("test", max_results=5)

    assert results == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/sumo/agent-learn && python -m pytest backend/tests/test_academic_search_new.py -v -k serper`
Expected: FAIL — `ImportError: cannot import name '_search_serper_scholar'`

- [ ] **Step 3: Implement Serper Scholar adapter**

Add to `backend/app/academic_search.py`:

```python
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
        # Venue is the middle segment (may include year, strip trailing year)
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

    # Year filtering
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/sumo/agent-learn && python -m pytest backend/tests/test_academic_search_new.py -v -k serper`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/academic_search.py backend/tests/test_academic_search_new.py
git commit -m "feat: add Serper Scholar adapter to academic_search module"
```

---

### Task 6: Unpaywall Enrichment

**Files:**
- Modify: `backend/app/academic_search.py`
- Modify: `backend/tests/test_academic_search_new.py`

- [ ] **Step 1: Write failing tests for Unpaywall enrichment**

Append to `backend/tests/test_academic_search_new.py`:

```python
@pytest.mark.asyncio
async def test_unpaywall_enriches_missing_pdf_urls():
    from app.academic_search import _enrich_with_unpaywall

    mock_response_with_pdf = MagicMock()
    mock_response_with_pdf.status_code = 200
    mock_response_with_pdf.json.return_value = {
        "is_oa": True,
        "best_oa_location": {
            "url_for_pdf": "https://example.com/paper.pdf",
            "url": "https://example.com/paper",
            "url_for_landing_page": "https://example.com/landing",
        },
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response_with_pdf)

    results = [
        AcademicResult(title="Paper A", url="u", abstract="a", authors=["X"], doi="10.1/a", pdf_url=None),
        AcademicResult(title="Paper B", url="u", abstract="a", authors=["X"], doi="10.1/b", pdf_url="http://existing.pdf"),
        AcademicResult(title="Paper C", url="u", abstract="a", authors=["X"], doi=None, pdf_url=None),
    ]

    with patch("app.academic_search.httpx.AsyncClient", return_value=mock_client):
        with patch("app.academic_search.settings") as mock_settings:
            mock_settings.UNPAYWALL_EMAIL = "test@real.com"
            enriched = await _enrich_with_unpaywall(results)

    # Paper A: had DOI, no pdf_url -> enriched
    assert enriched[0].pdf_url == "https://example.com/paper.pdf"
    # Paper B: already had pdf_url -> unchanged
    assert enriched[1].pdf_url == "http://existing.pdf"
    # Paper C: no DOI -> unchanged (still None)
    assert enriched[2].pdf_url is None

    # Only one API call (for Paper A only)
    assert mock_client.get.call_count == 1


@pytest.mark.asyncio
async def test_unpaywall_falls_back_to_url_when_pdf_null():
    from app.academic_search import _enrich_with_unpaywall

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "is_oa": True,
        "best_oa_location": {
            "url_for_pdf": None,
            "url": "https://example.com/oa-version",
            "url_for_landing_page": "https://example.com/landing",
        },
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    results = [AcademicResult(title="Paper", url="u", abstract="a", authors=["X"], doi="10.1/x")]

    with patch("app.academic_search.httpx.AsyncClient", return_value=mock_client):
        with patch("app.academic_search.settings") as mock_settings:
            mock_settings.UNPAYWALL_EMAIL = "test@real.com"
            enriched = await _enrich_with_unpaywall(results)

    assert enriched[0].pdf_url == "https://example.com/oa-version"


@pytest.mark.asyncio
async def test_unpaywall_handles_closed_access():
    from app.academic_search import _enrich_with_unpaywall

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "is_oa": False,
        "best_oa_location": None,
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    results = [AcademicResult(title="Closed", url="u", abstract="a", authors=["X"], doi="10.1/closed")]

    with patch("app.academic_search.httpx.AsyncClient", return_value=mock_client):
        with patch("app.academic_search.settings") as mock_settings:
            mock_settings.UNPAYWALL_EMAIL = "test@real.com"
            enriched = await _enrich_with_unpaywall(results)

    assert enriched[0].pdf_url is None


@pytest.mark.asyncio
async def test_unpaywall_handles_404_gracefully():
    from app.academic_search import _enrich_with_unpaywall

    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.raise_for_status = MagicMock(side_effect=httpx.HTTPStatusError("Not Found", request=MagicMock(), response=mock_response))

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    results = [AcademicResult(title="Missing", url="u", abstract="a", authors=["X"], doi="10.1/missing")]

    with patch("app.academic_search.httpx.AsyncClient", return_value=mock_client):
        with patch("app.academic_search.settings") as mock_settings:
            mock_settings.UNPAYWALL_EMAIL = "test@real.com"
            enriched = await _enrich_with_unpaywall(results)

    assert enriched[0].pdf_url is None  # graceful degradation


@pytest.mark.asyncio
async def test_unpaywall_skips_when_no_email():
    from app.academic_search import _enrich_with_unpaywall

    results = [AcademicResult(title="Paper", url="u", abstract="a", authors=["X"], doi="10.1/x")]

    with patch("app.academic_search.settings") as mock_settings:
        mock_settings.UNPAYWALL_EMAIL = ""
        enriched = await _enrich_with_unpaywall(results)

    assert enriched[0].pdf_url is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/sumo/agent-learn && python -m pytest backend/tests/test_academic_search_new.py -v -k unpaywall`
Expected: FAIL — `ImportError: cannot import name '_enrich_with_unpaywall'`

- [ ] **Step 3: Implement Unpaywall enrichment**

Add to `backend/app/academic_search.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/sumo/agent-learn && python -m pytest backend/tests/test_academic_search_new.py -v -k unpaywall`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/academic_search.py backend/tests/test_academic_search_new.py
git commit -m "feat: add Unpaywall PDF enrichment to academic_search module"
```

---

### Task 7: Main `academic_search()` Function

**Files:**
- Modify: `backend/app/academic_search.py`
- Modify: `backend/tests/test_academic_search_new.py`

- [ ] **Step 1: Write failing tests for main function**

Append to `backend/tests/test_academic_search_new.py`:

```python
@pytest.mark.asyncio
async def test_academic_search_parallel_both_providers():
    from app.academic_search import academic_search, AcademicResult

    openalex_results = [
        AcademicResult(title="OA Paper", url="http://oa", abstract="OA abstract", authors=["A"], doi="10.1/oa", year=2024, citation_count=50)
    ]
    serper_results = [
        AcademicResult(title="Serper Paper", url="http://serper", abstract="Serper abstract", authors=["B"], year=2023, citation_count=30)
    ]

    with patch("app.academic_search._search_openalex", new_callable=AsyncMock, return_value=openalex_results):
        with patch("app.academic_search._search_serper_scholar", new_callable=AsyncMock, return_value=serper_results):
            with patch("app.academic_search._enrich_with_unpaywall", new_callable=AsyncMock, side_effect=lambda r: r):
                results = await academic_search("test query", max_results=10)

    assert len(results) == 2
    titles = {r.title for r in results}
    assert "OA Paper" in titles
    assert "Serper Paper" in titles


@pytest.mark.asyncio
async def test_academic_search_deduplicates_cross_provider():
    from app.academic_search import academic_search, AcademicResult

    # Same paper from both providers (matched by title)
    openalex_results = [
        AcademicResult(title="Same Paper", url="http://oa", abstract="abstract", authors=["A"], doi="10.1/same", year=2024, citation_count=50, venue="NeurIPS")
    ]
    serper_results = [
        AcademicResult(title="Same Paper", url="http://serper", abstract="abstract", authors=["A"], year=2024, citation_count=50)
    ]

    with patch("app.academic_search._search_openalex", new_callable=AsyncMock, return_value=openalex_results):
        with patch("app.academic_search._search_serper_scholar", new_callable=AsyncMock, return_value=serper_results):
            with patch("app.academic_search._enrich_with_unpaywall", new_callable=AsyncMock, side_effect=lambda r: r):
                results = await academic_search("test", max_results=10)

    assert len(results) == 1
    assert results[0].venue == "NeurIPS"  # OpenAlex version kept (richer metadata)


@pytest.mark.asyncio
async def test_academic_search_one_provider_fails():
    from app.academic_search import academic_search, AcademicResult

    good_results = [
        AcademicResult(title="Good Paper", url="u", abstract="a", authors=["A"], year=2024)
    ]

    with patch("app.academic_search._search_openalex", new_callable=AsyncMock, side_effect=Exception("API down")):
        with patch("app.academic_search._search_serper_scholar", new_callable=AsyncMock, return_value=good_results):
            with patch("app.academic_search._enrich_with_unpaywall", new_callable=AsyncMock, side_effect=lambda r: r):
                results = await academic_search("test", max_results=10)

    assert len(results) == 1
    assert results[0].title == "Good Paper"


@pytest.mark.asyncio
async def test_academic_search_respects_max_results():
    from app.academic_search import academic_search, AcademicResult

    many_results = [
        AcademicResult(title=f"Paper {i}", url=f"u{i}", abstract="a", authors=["A"], year=2024)
        for i in range(20)
    ]

    with patch("app.academic_search._search_openalex", new_callable=AsyncMock, return_value=many_results):
        with patch("app.academic_search._search_serper_scholar", new_callable=AsyncMock, return_value=[]):
            with patch("app.academic_search._enrich_with_unpaywall", new_callable=AsyncMock, side_effect=lambda r: r):
                results = await academic_search("test", max_results=5)

    assert len(results) == 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/sumo/agent-learn && python -m pytest backend/tests/test_academic_search_new.py::test_academic_search_parallel_both_providers -v`
Expected: FAIL — `ImportError: cannot import name 'academic_search'`

- [ ] **Step 3: Implement main function**

Add to `backend/app/academic_search.py`:

```python
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
```

- [ ] **Step 4: Run all tests**

Run: `cd /Users/sumo/agent-learn && python -m pytest backend/tests/test_academic_search_new.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/academic_search.py backend/tests/test_academic_search_new.py
git commit -m "feat: add main academic_search() function with parallel providers"
```

---

### Task 8: Migrate Callers — agent_service.py

**Files:**
- Modify: `backend/app/agent_service.py:155-270` (discover_topic)
- Modify: `backend/app/agent_service.py:670-790` (research_section)
- Modify: `backend/app/agent_service.py:410-470` (generate_outline)
- Modify: `backend/app/agent_service.py:1415-1470` (run_discover_and_plan)
- Modify: `backend/app/agent_service.py:1550-1600` (run_research_section)

This is the biggest change. Every function that receives `academic_credentials` must be updated to call `academic_search.academic_search()` directly.

- [ ] **Step 1: Update `discover_topic` (lines ~155-270)**

Remove `academic_credentials: dict[str, dict] | None = None` parameter.

Replace the `academic_provider_names` block (lines ~216-231):

```python
    # Old: academic_provider_names block with get_active_academic_provider_names
    # New: academic search is always available if enabled in options
    academic_enabled = bool(academic_options and academic_options.get("enabled"))
    if academic_enabled and on_event:
        for i, query in enumerate(queries):
            await on_event(
                "academic_query",
                {"index": i, "total": len(queries), "query": query, "providers": ["openalex", "serper_scholar"]},
            )
```

Replace `_run_academic_query` (lines ~248-261):

```python
    async def _run_academic_query(index: int, query: str):
        from app.academic_search import academic_search as run_academic_search

        try:
            results = await run_academic_search(
                query,
                max_results=5,
                options=academic_options,
            )
            return ("academic", index, query, results, None)
        except Exception as e:
            return ("academic", index, query, [], e)
```

Update the task creation condition (line ~267):

```python
    if academic_enabled:
        pending_tasks.extend(
            asyncio.create_task(_run_academic_query(i, query))
```

Update the `on_event` calls at lines ~184-192 and ~200-209 to use the local `academic_enabled` variable instead of checking `academic_credentials`.

- [ ] **Step 2: Update `generate_outline` (lines ~410-470)**

Remove `academic_credentials: dict[str, dict] | None = None` parameter.

Update the call to `discover_topic` to stop passing `academic_credentials`:

```python
            topic_brief = await discover_topic(
                topic, instructions, provider, model, credentials, extra_fields,
                search_provider, search_credentials,
                on_event=on_event, user_id=user_id,
                academic_options=academic_options,
            )
```

- [ ] **Step 3: Update `research_section` (lines ~670-790)**

Remove `academic_credentials: dict[str, dict] | None = None` parameter.

Replace the academic search block (lines ~704-763):

```python
    academic_future = None
    academic_enabled = bool(academic_options and academic_options.get("enabled"))
    if academic_enabled:
        from app.academic_search import academic_search as run_academic_search

        academic_future = asyncio.gather(
            *[
                run_academic_search(
                    question,
                    max_results=5,
                    options=academic_options,
                )
                for question in brief.questions
            ],
            return_exceptions=True,
        )
```

Update the academic results processing block (lines ~744-763). The results are now `AcademicResult` objects instead of `SearchResult`:

```python
    if academic_future is not None:
        academic_batches = await academic_future
        for question, acad_results in zip(brief.questions, academic_batches):
            if isinstance(acad_results, BaseException):
                logger.warning("[research] Academic search failed for '%s': %s", question[:60], acad_results)
                continue
            academic_raw_results.extend(acad_results)
            for r in acad_results:
                all_results.append({
                    "title": f"[ACADEMIC] {r.title}",
                    "url": r.url,
                    "content": r.abstract,
                    "score": r.score,
                    "authors": ", ".join(r.authors) if r.authors else None,
                    "year": r.year,
                    "venue": r.venue,
                    "citations": r.citation_count,
                    "doi": r.doi,
                })
```

Update the deep reading condition (line ~767):

```python
    if academic_raw_results and academic_enabled:
```

- [ ] **Step 4: Update `run_discover_and_plan` (lines ~1415-1470)**

Remove `academic_credentials: dict[str, dict] | None = None` parameter.

Update the call to `generate_outline`:

```python
    outline_with_briefs, ungrounded = await generate_outline(
        course.topic, course.instructions, provider, model, credentials, extra_fields,
        search_provider, search_credentials, user_id=user_id,
        academic_options=academic_options,
    )
```

- [ ] **Step 5: Update `run_research_section` (lines ~1550-1600)**

Remove `academic_credentials: dict[str, dict] | None = None` parameter.

Update the call to `research_section`:

```python
    card_items = await research_section(brief, provider, model, credentials, extra_fields, search_provider, search_credentials, user_id=user_id, academic_options=academic_options)
```

- [ ] **Step 6: Run existing tests to verify nothing breaks**

Run: `cd /Users/sumo/agent-learn && python -m pytest backend/tests/ -v --timeout=30 -x`
Expected: Some tests may fail in test_courses.py and test_discovery_streaming.py (they still mock old signatures). That's expected — we fix those in Task 11.

- [ ] **Step 7: Commit**

```bash
git add backend/app/agent_service.py
git commit -m "refactor: remove academic_credentials param from agent_service"
```

---

### Task 9: Migrate Callers — pipeline.py and worker.py

**Files:**
- Modify: `backend/app/pipeline.py:115-155, 220-315`
- Modify: `backend/app/worker.py:150-260`

- [ ] **Step 1: Update pipeline.py**

In `run_discover_and_plan_step()` (line ~118): remove `academic_credentials: dict[str, dict] | None = None` parameter and remove it from the `run_discover_and_plan` call.

In `run_research_section_step()` (line ~136): remove `academic_credentials: dict[str, dict] | None = None` parameter and remove it from the `run_research_section` call.

In `_discover_and_plan()` (line ~110): remove `academic_credentials` param and its forwarding.

In `_research_section()` (line ~136): remove `academic_credentials` param and its forwarding.

In `run_pipeline()` (line ~220): remove `academic_credentials: dict[str, dict] | None = None` parameter. Remove it from the calls to `_discover_and_plan` (line ~251) and `_research_section` (line ~313).

- [ ] **Step 2: Update worker.py**

Change `_resolve_credentials` return type from 3-tuple to 2-tuple. Remove the entire academic credentials section (lines ~196-217):

```python
async def _resolve_credentials(job: PipelineJob) -> tuple[dict, dict | None]:
    """Read ProviderConfig + UserKeySalt, decrypt, and return (creds, search_creds)."""
    pepper = settings.ENCRYPTION_PEPPER.encode()

    async with async_session() as session:
        salt_result = await session.execute(
            select(UserKeySalt).where(UserKeySalt.user_id == job.user_id)
        )
        salt_row = salt_result.scalar_one()
        key = derive_key(salt_row.salt, pepper)

        provider_name = job.config.get("provider", "")
        provider_result = await session.execute(
            select(ProviderConfig).where(
                ProviderConfig.user_id == job.user_id,
                ProviderConfig.provider == provider_name,
            )
        )
        provider_row = provider_result.scalar_one()
        creds = json.loads(decrypt_credentials(key, provider_row.encrypted_credentials))

        search_creds = None
        search_provider = job.config.get("search_provider")
        if search_provider:
            if search_provider == "duckduckgo":
                search_creds = {}
            else:
                search_result = await session.execute(
                    select(ProviderConfig).where(
                        ProviderConfig.user_id == job.user_id,
                        ProviderConfig.provider == search_provider,
                    )
                )
                search_row = search_result.scalar_one()
                search_creds = json.loads(
                    decrypt_credentials(key, search_row.encrypted_credentials)
                )

    return creds, search_creds
```

Update `process_job` to unpack the 2-tuple and remove `academic_credentials` from the `run_pipeline` call:

```python
        creds, search_creds = await _resolve_credentials(job)
        # ...
        result = await run_pipeline(
            job_id=job.id,
            course_id=job.course_id,
            checkpoint=job.checkpoint,
            provider=job.config.get("provider", ""),
            model=job.config.get("model", ""),
            credentials=creds,
            extra_fields=job.config.get("extra_fields"),
            search_provider=job.config.get("search_provider", ""),
            search_credentials=search_creds,
            shutdown_event=shutdown_event,
            user_id=str(job.user_id),
            academic_options=job.config.get("academic_search"),
        )
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/pipeline.py backend/app/worker.py
git commit -m "refactor: remove academic_credentials from pipeline and worker"
```

---

### Task 10: Migrate Callers — courses.py and paper_reader.py

**Files:**
- Modify: `backend/app/routers/courses.py:133-141, 196-211`
- Modify: `backend/app/paper_reader.py:10`

- [ ] **Step 1: Update courses.py**

Delete `_get_user_academic_credentials()` function (lines 133-141).

In `run_discovery()` inside `create_course` (lines ~196-211), remove the academic credential fetching and update the `generate_outline` call:

```python
        try:
            async with async_session() as sess:
                provider, model, creds, extra_fields = await _get_user_provider(user_id, sess)
                search_provider, search_creds = await _get_user_search_provider(user_id, sess)

                outline_with_briefs, ungrounded = await generate_outline(
                    body.topic, body.instructions, provider, model, creds, extra_fields,
                    search_provider, search_creds,
                    on_event=emit, user_id=user_id,
                    academic_options=academic_search_dict,
                )
```

Remove the `key_cache` import if it's only used for academic providers (check — it may still be used for `_get_user_search_provider` via `_ensure_cache`).

- [ ] **Step 2: Update paper_reader.py**

Change line 10 from:

```python
from app.search_service import SearchResult, rank_for_deep_reading
```

To:

```python
from app.academic_search import AcademicResult, rank_for_deep_reading
```

Update any type hints in the file that reference `SearchResult` to use `AcademicResult`. The `deep_read_top_papers` function receives a list of results — update its type annotation accordingly.

- [ ] **Step 3: Commit**

```bash
git add backend/app/routers/courses.py backend/app/paper_reader.py
git commit -m "refactor: remove academic_credentials from courses router, update paper_reader imports"
```

---

### Task 11: Remove Old Academic Code

**Files:**
- Modify: `backend/app/search_service.py`
- Modify: `backend/app/key_cache.py`
- Modify: `backend/app/main.py:25,59`
- Delete: `backend/app/routers/academic_provider_routes.py`
- Delete: `backend/tests/test_academic_search.py`
- Rename: `backend/tests/test_academic_search_new.py` -> `backend/tests/test_academic_search.py`

- [ ] **Step 1: Gut academic code from search_service.py**

Remove from `search_service.py`:
- `ACADEMIC_SEARCH_PROVIDERS` dict (lines 49-66)
- `get_academic_search_provider_registry()` (lines 69-71)
- Academic fields from `SearchResult` can stay (they're harmless defaults)
- `_ACADEMIC_STOPWORDS`, `_ACADEMIC_SIGNAL_TERMS`, `_BIOMEDICAL_TERMS`, `_SECURITY_VENUE_TERMS` (lines 90-114)
- `reconstruct_abstract()` (lines 117-126)
- `_normalize_title()` (lines 129-133)
- `_metadata_richness()` (lines 136-149)
- `deduplicate_academic_results()` (lines 152-178)
- `rank_for_deep_reading()` (lines 181-194)
- `_tokenize_academic_text()` (lines 197-207)
- `_academic_recency_score()` (lines 210-225)
- `score_academic_result()` (lines 228-275)
- `rerank_academic_results()` (lines 278-292)
- `select_academic_results_for_discovery()` (lines 295-319)
- `_year_range_to_s2_param()` (lines 555-563)
- All S2 rate-limit machinery: `_s2_locks`, `_s2_last_request` (lines 566-572)
- `_search_semantic_scholar()` (lines 575-658)
- `_year_range_to_arxiv_date_filter()` (lines 661-670)
- `_arxiv_lock`, `_arxiv_last_request` (lines 673-675)
- `_search_arxiv()` (lines 678-769)
- `_year_range_to_openalex_filter()` (lines 772-780)
- `_search_openalex()` (lines 783-861)
- `_ACADEMIC_ADAPTERS` (lines 864-868)
- `_get_academic_adapter()` (lines 871-881)
- `_should_run_academic_provider()` (lines 884-892)
- `get_active_academic_provider_names()` (lines 895-901)
- `academic_search()` (lines 904-959)
- The `import asyncio as _asyncio` and `import time as _time` (line 566-567) if only used by academic code

Keep: `SEARCH_PROVIDERS`, `SearchResult`, `search()`, `search_with_fallback()`, `validate_search_credentials()`, `get_search_provider_registry()`, `is_configured()`, all web adapters (`_search_tavily`, `_search_exa`, `_search_brave`, `_search_serper`, `_search_duckduckgo`), `_ADAPTERS`.

- [ ] **Step 2: Remove `get_all_academic_providers` from key_cache.py**

Delete the `get_all_academic_providers()` function (lines 123-137 based on grep).

- [ ] **Step 3: Remove academic_provider_routes from main.py**

In `backend/app/main.py` line 25, remove `academic_provider_routes` from the import.
In line 59, remove `app.include_router(academic_provider_routes.router, prefix="/api")`.

- [ ] **Step 4: Delete academic_provider_routes.py**

```bash
rm backend/app/routers/academic_provider_routes.py
```

- [ ] **Step 5: Swap test files**

```bash
rm backend/tests/test_academic_search.py
mv backend/tests/test_academic_search_new.py backend/tests/test_academic_search.py
```

- [ ] **Step 6: Run all backend tests**

Run: `cd /Users/sumo/agent-learn && python -m pytest backend/tests/ -v --timeout=30`
Expected: All PASS (or known failures in test_courses.py / test_discovery_streaming.py which we fix next)

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: remove old academic search code (S2, arXiv, per-user keys)"
```

---

### Task 12: Fix Remaining Tests

**Files:**
- Modify: `backend/tests/test_courses.py:20, 114-137`
- Modify: `backend/tests/test_discovery_streaming.py:37-88`

- [ ] **Step 1: Fix test_courses.py**

Remove `_mock_get_user_academic_credentials` (line ~20).

Update `test_create_course_passes_academic_search_context` (lines ~114-137): remove the `_get_user_academic_credentials` mock. Update the assertion — the call to `generate_outline` no longer includes `academic_credentials`. Verify `academic_options` is still passed.

- [ ] **Step 2: Fix test_discovery_streaming.py**

Update `mock_academic_search` (lines ~37-42) to match the new signature:

```python
    async def mock_academic_search(
        query: str,
        max_results: int = 10,
        options: dict | None = None,
    ):
```

Update the mock target from `app.search_service.academic_search` to `app.academic_search.academic_search` (line ~76).

Remove `academic_credentials` from the test call setup (line ~88).

- [ ] **Step 3: Run all tests**

Run: `cd /Users/sumo/agent-learn && python -m pytest backend/tests/ -v --timeout=30`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_courses.py backend/tests/test_discovery_streaming.py
git commit -m "test: fix tests for new academic_search module"
```

---

### Task 13: Frontend Cleanup

**Files:**
- Modify: `frontend/src/app/settings/page.tsx:869-930`
- Modify: `frontend/src/lib/api.ts:499-556`

- [ ] **Step 1: Remove academic provider API calls from api.ts**

Delete the following functions from `frontend/src/lib/api.ts`:
- `fetchAcademicProviderRegistry` (around line 499)
- `fetchAcademicProviders` (around line 505)
- `saveAcademicProvider` (around line 514)
- `updateAcademicProvider` (around line 531)
- `deleteAcademicProvider` (around line 544)
- `testAcademicProvider` (around line 556)

- [ ] **Step 2: Remove AcademicProviderSection from settings page**

In `frontend/src/app/settings/page.tsx`:
- Remove the `academicRegistry` and `academicConfigs` state variables (lines ~869-870)
- Remove the `academicRegInner` fetch logic (lines ~886-887)
- Remove the `<AcademicProviderSection ... />` component render (line ~930)
- If `AcademicProviderSection` is defined in the same file, remove the component definition. If it's imported, remove the import.

- [ ] **Step 3: Verify frontend builds**

Run: `cd /Users/sumo/agent-learn/frontend && npm run build`
Expected: Build succeeds with no errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/app/settings/page.tsx frontend/src/lib/api.ts
git commit -m "feat: remove per-user academic provider config from frontend"
```

---

### Task 14: Final Verification

- [ ] **Step 1: Run full backend test suite**

Run: `cd /Users/sumo/agent-learn && python -m pytest backend/tests/ -v --timeout=60`
Expected: All PASS

- [ ] **Step 2: Run frontend build**

Run: `cd /Users/sumo/agent-learn/frontend && npm run build`
Expected: Build succeeds

- [ ] **Step 3: Verify no stale imports**

Run: `cd /Users/sumo/agent-learn && grep -r "academic_provider_routes" backend/`
Expected: No results

Run: `cd /Users/sumo/agent-learn && grep -r "get_all_academic_providers" backend/`
Expected: No results

Run: `cd /Users/sumo/agent-learn && grep -r "_search_semantic_scholar\|_search_arxiv" backend/`
Expected: No results

Run: `cd /Users/sumo/agent-learn && grep -r "academic_credentials" backend/`
Expected: No results (or only in the `courses.academic_search` JSON column references which are the options dict, not credentials)

- [ ] **Step 4: Commit any final fixes**

If any stale references found, fix and commit.
