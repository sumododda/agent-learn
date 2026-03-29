# Academic Research Paper Search — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Semantic Scholar, arXiv, and OpenAlex as academic search providers so course content is grounded in peer-reviewed research papers, controlled by a per-course UI toggle with filters.

**Architecture:** Three new search adapters slot into the existing `search_service.py` pattern. Per-user encrypted credentials follow the same `ProviderConfig` + `key_cache` flow as web search providers. The pipeline runs academic search in parallel with web search when enabled, and academic evidence cards flow through the existing verify → write → edit pipeline with Tier 1 confidence.

**Tech Stack:** Python/FastAPI (backend), Next.js/React (frontend), SQLAlchemy + Alembic (DB), httpx (HTTP client), xml.etree.ElementTree (arXiv XML parsing)

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/app/search_service.py` | Modify | `ACADEMIC_SEARCH_PROVIDERS` registry, 3 adapters, `SearchResult` fields, dedup helper, OpenAlex abstract reconstruction |
| `backend/app/routers/academic_provider_routes.py` | Create | CRUD routes for academic provider credentials |
| `backend/app/schemas.py` | Modify | `AcademicSearchOptions` model, `CourseCreate.academic_search` field |
| `backend/app/models.py` | Modify | 5 new columns on `EvidenceCard` |
| `backend/alembic/versions/xxxx_add_academic_fields.py` | Create | DB migration |
| `backend/app/agent_service.py` | Modify | Academic search calls in discovery + section research |
| `backend/app/agent.py` | Modify | Updated prompts for researcher, writer, editor |
| `backend/app/pipeline.py` | Modify | Pass academic config + credentials through |
| `backend/app/worker.py` | Modify | Resolve academic provider credentials |
| `backend/app/key_cache.py` | Modify | Academic provider credential caching |
| `backend/app/main.py` | Modify | Register academic provider router |
| `frontend/src/app/settings/page.tsx` | Modify | Academic Search Providers section |
| `frontend/src/app/page.tsx` | Modify | Toggle + filter options on Step 2 |
| `frontend/src/lib/api.ts` | Modify | Academic provider CRUD + `academic_search` in course creation |
| `backend/tests/test_academic_search.py` | Create | Tests for adapters, dedup, abstract reconstruction |

---

### Task 1: SearchResult Dataclass — Add Academic Fields

**Files:**
- Modify: `backend/app/search_service.py:45-50`
- Create: `backend/tests/test_academic_search.py`

- [ ] **Step 1: Write test for new SearchResult fields**

Create `backend/tests/test_academic_search.py`:

```python
from app.search_service import SearchResult


def test_search_result_academic_fields_default():
    r = SearchResult(title="Test", url="https://example.com", content="Abstract")
    assert r.is_academic is False
    assert r.authors is None
    assert r.year is None
    assert r.venue is None
    assert r.citation_count is None
    assert r.doi is None


def test_search_result_academic_fields_populated():
    r = SearchResult(
        title="Attention Is All You Need",
        url="https://arxiv.org/abs/1706.03762",
        content="The dominant sequence transduction models...",
        score=0.95,
        authors=["Vaswani, A.", "Shazeer, N."],
        year=2017,
        venue="NeurIPS",
        citation_count=90000,
        doi="10.48550/arXiv.1706.03762",
        is_academic=True,
    )
    assert r.is_academic is True
    assert r.authors == ["Vaswani, A.", "Shazeer, N."]
    assert r.year == 2017
    assert r.citation_count == 90000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sumo/agent-learn/backend && python -m pytest tests/test_academic_search.py -v`
Expected: FAIL — `SearchResult` does not accept `authors`, `year`, etc.

- [ ] **Step 3: Add academic fields to SearchResult**

In `backend/app/search_service.py`, replace the `SearchResult` dataclass (lines 45-50) with:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/sumo/agent-learn/backend && python -m pytest tests/test_academic_search.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/search_service.py backend/tests/test_academic_search.py
git commit -m "feat: add academic metadata fields to SearchResult dataclass"
```

---

### Task 2: OpenAlex Abstract Reconstruction Helper

**Files:**
- Modify: `backend/app/search_service.py`
- Modify: `backend/tests/test_academic_search.py`

- [ ] **Step 1: Write test for abstract reconstruction**

Append to `backend/tests/test_academic_search.py`:

```python
from app.search_service import reconstruct_abstract


def test_reconstruct_abstract_basic():
    inverted = {"Machine": [0], "learning": [1], "is": [2], "great": [3]}
    assert reconstruct_abstract(inverted) == "Machine learning is great"


def test_reconstruct_abstract_repeated_words():
    inverted = {"the": [0, 4], "cat": [1], "sat": [2], "on": [3], "mat": [5]}
    assert reconstruct_abstract(inverted) == "the cat sat on the mat"


def test_reconstruct_abstract_empty():
    assert reconstruct_abstract({}) == ""
    assert reconstruct_abstract(None) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sumo/agent-learn/backend && python -m pytest tests/test_academic_search.py::test_reconstruct_abstract_basic -v`
Expected: FAIL — `reconstruct_abstract` not defined

- [ ] **Step 3: Implement reconstruct_abstract**

Add to `backend/app/search_service.py` after the `SearchResult` dataclass:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/sumo/agent-learn/backend && python -m pytest tests/test_academic_search.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/search_service.py backend/tests/test_academic_search.py
git commit -m "feat: add OpenAlex abstract reconstruction helper"
```

---

### Task 3: Deduplication Helper

**Files:**
- Modify: `backend/app/search_service.py`
- Modify: `backend/tests/test_academic_search.py`

- [ ] **Step 1: Write test for deduplication**

Append to `backend/tests/test_academic_search.py`:

```python
from app.search_service import deduplicate_academic_results


def test_dedup_by_doi():
    results = [
        SearchResult(title="Paper A", url="https://s2.com/1", content="Abstract A",
                     doi="10.1234/a", is_academic=True, authors=["Smith"], year=2023,
                     venue="NeurIPS", citation_count=100),
        SearchResult(title="Paper A", url="https://openalex.org/1", content="Abstract A",
                     doi="10.1234/a", is_academic=True, authors=["Smith"], year=2023),
    ]
    deduped = deduplicate_academic_results(results)
    assert len(deduped) == 1
    # Keeps the one with richer metadata (citation_count not None)
    assert deduped[0].citation_count == 100


def test_dedup_by_title_similarity():
    results = [
        SearchResult(title="Attention Is All You Need", url="https://s2.com/1",
                     content="Abstract", doi="10.1234/a", is_academic=True,
                     authors=["Vaswani"], citation_count=90000),
        SearchResult(title="Attention is All You Need.", url="https://arxiv.org/1",
                     content="Abstract", doi=None, is_academic=True),
    ]
    deduped = deduplicate_academic_results(results)
    assert len(deduped) == 1
    assert deduped[0].citation_count == 90000


def test_dedup_different_papers():
    results = [
        SearchResult(title="Paper A", url="u1", content="c1", doi="10.1/a", is_academic=True),
        SearchResult(title="Paper B", url="u2", content="c2", doi="10.1/b", is_academic=True),
    ]
    deduped = deduplicate_academic_results(results)
    assert len(deduped) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sumo/agent-learn/backend && python -m pytest tests/test_academic_search.py::test_dedup_by_doi -v`
Expected: FAIL — `deduplicate_academic_results` not defined

- [ ] **Step 3: Implement deduplication**

Add to `backend/app/search_service.py`:

```python
import re
import unicodedata


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
    seen_dois: dict[str, int] = {}  # doi -> index in output
    seen_titles: dict[str, int] = {}  # normalized title -> index in output
    output: list[SearchResult] = []

    for r in results:
        # Check DOI match first
        if r.doi:
            doi_key = r.doi.lower().strip()
            if doi_key in seen_dois:
                idx = seen_dois[doi_key]
                if _metadata_richness(r) > _metadata_richness(output[idx]):
                    output[idx] = r
                continue
            seen_dois[doi_key] = len(output)

        # Check title similarity
        norm_title = _normalize_title(r.title)
        if norm_title in seen_titles:
            idx = seen_titles[norm_title]
            if _metadata_richness(r) > _metadata_richness(output[idx]):
                output[idx] = r
            continue
        seen_titles[norm_title] = len(output)

        output.append(r)

    return output
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/sumo/agent-learn/backend && python -m pytest tests/test_academic_search.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/search_service.py backend/tests/test_academic_search.py
git commit -m "feat: add academic result deduplication by DOI and title"
```

---

### Task 4: Semantic Scholar Adapter

**Files:**
- Modify: `backend/app/search_service.py`
- Modify: `backend/tests/test_academic_search.py`

- [ ] **Step 1: Write test for Semantic Scholar adapter**

Append to `backend/tests/test_academic_search.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_search_semantic_scholar_parses_response():
    from app.search_service import _search_semantic_scholar

    mock_response = {
        "total": 1,
        "offset": 0,
        "data": [
            {
                "paperId": "abc123",
                "title": "Test Paper",
                "url": "https://www.semanticscholar.org/paper/abc123",
                "abstract": "This is a test abstract about machine learning.",
                "year": 2023,
                "authors": [{"authorId": "1", "name": "Smith, J."}, {"authorId": "2", "name": "Lee, K."}],
                "venue": "NeurIPS",
                "citationCount": 42,
                "externalIds": {"DOI": "10.1234/test"},
                "openAccessPdf": {"url": "https://example.com/paper.pdf", "status": "GREEN"},
                "publicationTypes": ["JournalArticle"],
            }
        ],
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_resp = AsyncMock()
        mock_resp.json.return_value = mock_response
        mock_resp.raise_for_status = lambda: None
        mock_get.return_value = mock_resp

        results = await _search_semantic_scholar(
            query="machine learning",
            credentials={},
            max_results=5,
            search_depth="basic",
            academic_options={"year_range": "all", "min_citations": 0, "open_access_only": False},
        )

    assert len(results) == 1
    r = results[0]
    assert r.title == "Test Paper"
    assert r.is_academic is True
    assert r.authors == ["Smith, J.", "Lee, K."]
    assert r.year == 2023
    assert r.venue == "NeurIPS"
    assert r.citation_count == 42
    assert r.doi == "10.1234/test"
    assert "test abstract" in r.content


@pytest.mark.asyncio
async def test_search_semantic_scholar_skips_null_abstract():
    from app.search_service import _search_semantic_scholar

    mock_response = {
        "total": 2,
        "offset": 0,
        "data": [
            {"paperId": "1", "title": "No Abstract", "url": "u1", "abstract": None,
             "year": 2023, "authors": [], "venue": "", "citationCount": 0,
             "externalIds": {}, "openAccessPdf": None, "publicationTypes": []},
            {"paperId": "2", "title": "Has Abstract", "url": "u2", "abstract": "Real content",
             "year": 2023, "authors": [], "venue": "", "citationCount": 0,
             "externalIds": {}, "openAccessPdf": None, "publicationTypes": []},
        ],
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_resp = AsyncMock()
        mock_resp.json.return_value = mock_response
        mock_resp.raise_for_status = lambda: None
        mock_get.return_value = mock_resp

        results = await _search_semantic_scholar(
            "test", {}, 5, "basic",
            academic_options={"year_range": "all", "min_citations": 0, "open_access_only": False},
        )

    assert len(results) == 1
    assert results[0].title == "Has Abstract"


@pytest.mark.asyncio
async def test_search_semantic_scholar_open_access_filter():
    from app.search_service import _search_semantic_scholar

    mock_response = {
        "total": 2,
        "offset": 0,
        "data": [
            {"paperId": "1", "title": "OA Paper", "url": "u1", "abstract": "Abstract 1",
             "year": 2023, "authors": [], "venue": "", "citationCount": 0,
             "externalIds": {}, "openAccessPdf": {"url": "https://pdf.com", "status": "GREEN"},
             "publicationTypes": []},
            {"paperId": "2", "title": "Closed Paper", "url": "u2", "abstract": "Abstract 2",
             "year": 2023, "authors": [], "venue": "", "citationCount": 0,
             "externalIds": {}, "openAccessPdf": None, "publicationTypes": []},
        ],
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_resp = AsyncMock()
        mock_resp.json.return_value = mock_response
        mock_resp.raise_for_status = lambda: None
        mock_get.return_value = mock_resp

        results = await _search_semantic_scholar(
            "test", {}, 5, "basic",
            academic_options={"year_range": "all", "min_citations": 0, "open_access_only": True},
        )

    assert len(results) == 1
    assert results[0].title == "OA Paper"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/sumo/agent-learn/backend && python -m pytest tests/test_academic_search.py::test_search_semantic_scholar_parses_response -v`
Expected: FAIL — `_search_semantic_scholar` not defined

- [ ] **Step 3: Implement Semantic Scholar adapter**

Add to `backend/app/search_service.py` before the `_ADAPTERS` dict:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/sumo/agent-learn/backend && python -m pytest tests/test_academic_search.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/search_service.py backend/tests/test_academic_search.py
git commit -m "feat: add Semantic Scholar search adapter"
```

---

### Task 5: arXiv Adapter

**Files:**
- Modify: `backend/app/search_service.py`
- Modify: `backend/tests/test_academic_search.py`

- [ ] **Step 1: Write test for arXiv adapter**

Append to `backend/tests/test_academic_search.py`:

```python
@pytest.mark.asyncio
async def test_search_arxiv_parses_xml():
    from app.search_service import _search_arxiv

    xml_response = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:arxiv="http://arxiv.org/schemas/atom">
      <opensearch:totalResults xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">1</opensearch:totalResults>
      <entry>
        <id>http://arxiv.org/abs/1706.03762v5</id>
        <title>Attention Is All You Need</title>
        <summary>The dominant sequence transduction models are based on complex recurrent or convolutional neural networks.</summary>
        <published>2017-06-12T17:57:34Z</published>
        <author><name>Ashish Vaswani</name></author>
        <author><name>Noam Shazeer</name></author>
        <arxiv:doi>10.48550/arXiv.1706.03762</arxiv:doi>
        <link href="http://arxiv.org/abs/1706.03762v5" rel="alternate" type="text/html"/>
        <link href="http://arxiv.org/pdf/1706.03762v5" title="pdf" type="application/pdf" rel="related"/>
      </entry>
    </feed>"""

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_resp = AsyncMock()
        mock_resp.text = xml_response
        mock_resp.raise_for_status = lambda: None
        mock_get.return_value = mock_resp

        results = await _search_arxiv(
            "attention", {}, 5, "basic",
            academic_options={"year_range": "all", "min_citations": 0, "open_access_only": False},
        )

    assert len(results) == 1
    r = results[0]
    assert r.title == "Attention Is All You Need"
    assert r.is_academic is True
    assert "Ashish Vaswani" in r.authors
    assert "Noam Shazeer" in r.authors
    assert r.year == 2017
    assert r.doi == "10.48550/arXiv.1706.03762"
    assert "sequence transduction" in r.content
    assert "arxiv.org" in r.url
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sumo/agent-learn/backend && python -m pytest tests/test_academic_search.py::test_search_arxiv_parses_xml -v`
Expected: FAIL — `_search_arxiv` not defined

- [ ] **Step 3: Implement arXiv adapter**

Add to `backend/app/search_service.py`:

```python
import xml.etree.ElementTree as ET


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

        # Get abstract page URL
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/sumo/agent-learn/backend && python -m pytest tests/test_academic_search.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/search_service.py backend/tests/test_academic_search.py
git commit -m "feat: add arXiv search adapter with XML parsing"
```

---

### Task 6: OpenAlex Adapter

**Files:**
- Modify: `backend/app/search_service.py`
- Modify: `backend/tests/test_academic_search.py`

- [ ] **Step 1: Write test for OpenAlex adapter**

Append to `backend/tests/test_academic_search.py`:

```python
@pytest.mark.asyncio
async def test_search_openalex_parses_response():
    from app.search_service import _search_openalex

    mock_response = {
        "meta": {"count": 1, "page": 1, "per_page": 25},
        "results": [
            {
                "id": "https://openalex.org/W12345",
                "doi": "https://doi.org/10.1234/test",
                "title": "Test Paper on Deep Learning",
                "display_name": "Test Paper on Deep Learning",
                "relevance_score": 42.5,
                "publication_year": 2023,
                "publication_date": "2023-06-15",
                "cited_by_count": 150,
                "authorships": [
                    {"author": {"display_name": "Chen, W."}, "author_position": "first"},
                    {"author": {"display_name": "Davis, M."}, "author_position": "last"},
                ],
                "abstract_inverted_index": {
                    "Deep": [0], "learning": [1], "has": [2],
                    "transformed": [3], "AI": [4], "research": [5],
                },
                "primary_location": {
                    "source": {"display_name": "Nature Machine Intelligence"},
                    "landing_page_url": "https://nature.com/articles/test",
                },
                "open_access": {"is_oa": True, "oa_url": "https://nature.com/articles/test.pdf"},
            }
        ],
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_resp = AsyncMock()
        mock_resp.json.return_value = mock_response
        mock_resp.raise_for_status = lambda: None
        mock_get.return_value = mock_resp

        results = await _search_openalex(
            "deep learning", {"api_key": "test_key"}, 5, "basic",
            academic_options={"year_range": "all", "min_citations": 0, "open_access_only": False},
        )

    assert len(results) == 1
    r = results[0]
    assert r.title == "Test Paper on Deep Learning"
    assert r.is_academic is True
    assert r.authors == ["Chen, W.", "Davis, M."]
    assert r.year == 2023
    assert r.venue == "Nature Machine Intelligence"
    assert r.citation_count == 150
    assert r.doi == "10.1234/test"
    assert r.content == "Deep learning has transformed AI research"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sumo/agent-learn/backend && python -m pytest tests/test_academic_search.py::test_search_openalex_parses_response -v`
Expected: FAIL — `_search_openalex` not defined

- [ ] **Step 3: Implement OpenAlex adapter**

Add to `backend/app/search_service.py`:

```python
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

    # Build filter string
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

        venue = None
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/sumo/agent-learn/backend && python -m pytest tests/test_academic_search.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/search_service.py backend/tests/test_academic_search.py
git commit -m "feat: add OpenAlex search adapter with abstract reconstruction"
```

---

### Task 7: Academic Search Provider Registry & Unified Search Function

**Files:**
- Modify: `backend/app/search_service.py`
- Modify: `backend/tests/test_academic_search.py`

- [ ] **Step 1: Write test for academic search orchestration**

Append to `backend/tests/test_academic_search.py`:

```python
import asyncio


@pytest.mark.asyncio
async def test_academic_search_all_providers():
    from app.search_service import academic_search

    s2_result = SearchResult(
        title="S2 Paper", url="s2.com", content="S2 abstract",
        doi="10.1/s2", is_academic=True, authors=["A"], year=2023,
        venue="NeurIPS", citation_count=50,
    )
    arxiv_result = SearchResult(
        title="arXiv Paper", url="arxiv.org", content="arXiv abstract",
        doi=None, is_academic=True, authors=["B"], year=2023,
    )
    openalex_result = SearchResult(
        title="OA Paper", url="openalex.org", content="OA abstract",
        doi="10.1/oa", is_academic=True, authors=["C"], year=2023,
        citation_count=30,
    )

    with patch("app.search_service._search_semantic_scholar", new_callable=AsyncMock, return_value=[s2_result]) as mock_s2, \
         patch("app.search_service._search_arxiv", new_callable=AsyncMock, return_value=[arxiv_result]) as mock_arxiv, \
         patch("app.search_service._search_openalex", new_callable=AsyncMock, return_value=[openalex_result]) as mock_oa:

        results = await academic_search(
            query="test",
            academic_credentials={"semantic_scholar": {}, "arxiv": {}, "openalex": {"api_key": "k"}},
            academic_options={"year_range": "all", "min_citations": 0, "open_access_only": False},
            max_results=5,
        )

    assert len(results) == 3
    assert all(r.is_academic for r in results)


@pytest.mark.asyncio
async def test_academic_search_deduplicates():
    from app.search_service import academic_search

    paper = SearchResult(
        title="Same Paper", url="s2.com", content="Abstract",
        doi="10.1/same", is_academic=True, authors=["A"], year=2023,
        citation_count=50,
    )
    paper_dup = SearchResult(
        title="Same Paper", url="oa.org", content="Abstract",
        doi="10.1/same", is_academic=True, authors=["A"], year=2023,
    )

    with patch("app.search_service._search_semantic_scholar", new_callable=AsyncMock, return_value=[paper]), \
         patch("app.search_service._search_arxiv", new_callable=AsyncMock, return_value=[]), \
         patch("app.search_service._search_openalex", new_callable=AsyncMock, return_value=[paper_dup]):

        results = await academic_search(
            query="test",
            academic_credentials={"semantic_scholar": {}, "openalex": {"api_key": "k"}},
            academic_options={"year_range": "all", "min_citations": 0, "open_access_only": False},
        )

    assert len(results) == 1
    assert results[0].citation_count == 50  # richer metadata kept


@pytest.mark.asyncio
async def test_academic_search_skips_missing_providers():
    from app.search_service import academic_search

    paper = SearchResult(title="P", url="u", content="c", is_academic=True)

    with patch("app.search_service._search_semantic_scholar", new_callable=AsyncMock, return_value=[paper]), \
         patch("app.search_service._search_arxiv", new_callable=AsyncMock) as mock_arxiv, \
         patch("app.search_service._search_openalex", new_callable=AsyncMock) as mock_oa:

        results = await academic_search(
            query="test",
            academic_credentials={"semantic_scholar": {}},  # only S2 configured
            academic_options={"year_range": "all", "min_citations": 0, "open_access_only": False},
        )

    assert len(results) == 1
    mock_arxiv.assert_not_called()
    mock_oa.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/sumo/agent-learn/backend && python -m pytest tests/test_academic_search.py::test_academic_search_all_providers -v`
Expected: FAIL — `academic_search` not defined

- [ ] **Step 3: Add ACADEMIC_SEARCH_PROVIDERS registry and academic_search function**

Add to `backend/app/search_service.py` after `SEARCH_PROVIDERS`:

```python
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
```

Add the orchestration function after the adapters:

```python
_ACADEMIC_ADAPTERS = {
    "semantic_scholar": _search_semantic_scholar,
    "arxiv": _search_arxiv,
    "openalex": _search_openalex,
}


async def academic_search(
    query: str,
    academic_credentials: dict[str, dict],
    academic_options: dict,
    max_results: int = 5,
) -> list[SearchResult]:
    """Search all configured academic providers, deduplicate, and return merged results.

    academic_credentials: {provider_name: {api_key: ...}} — only configured providers included.
    """
    import asyncio

    async def _run_provider(name: str, creds: dict) -> list[SearchResult]:
        adapter = _ACADEMIC_ADAPTERS.get(name)
        if not adapter:
            return []
        try:
            return await adapter(query, creds, max_results, "basic", academic_options)
        except Exception as e:
            logger.warning("[academic_search] %s failed for '%s': %s", name, query[:60], e)
            return []

    # arXiv must run sequentially (rate limit), S2 + OpenAlex can run in parallel
    parallel_providers = {}
    arxiv_creds = None
    for name, creds in academic_credentials.items():
        if name == "arxiv":
            arxiv_creds = creds
        elif name in _ACADEMIC_ADAPTERS:
            parallel_providers[name] = creds

    # Run S2 + OpenAlex in parallel
    tasks = [_run_provider(name, creds) for name, creds in parallel_providers.items()]
    parallel_results = await asyncio.gather(*tasks)
    all_results = [r for batch in parallel_results for r in batch]

    # Run arXiv sequentially if configured
    if arxiv_creds is not None:
        arxiv_results = await _run_provider("arxiv", arxiv_creds)
        all_results.extend(arxiv_results)

    return deduplicate_academic_results(all_results)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/sumo/agent-learn/backend && python -m pytest tests/test_academic_search.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/search_service.py backend/tests/test_academic_search.py
git commit -m "feat: add academic search registry and unified orchestration function"
```

---

### Task 8: Backend Schema & Model Changes

**Files:**
- Modify: `backend/app/schemas.py:9-11`
- Modify: `backend/app/models.py:95-116`
- Create: `backend/alembic/versions/xxxx_add_academic_evidence_card_fields.py`

- [ ] **Step 1: Add AcademicSearchOptions schema**

In `backend/app/schemas.py`, add before `CourseCreate`:

```python
class AcademicSearchOptions(BaseModel):
    enabled: bool = False
    year_range: str = Field(default="5y", pattern=r"^(5y|10y|20y|all)$")
    min_citations: int = Field(default=0, ge=0)
    open_access_only: bool = False
```

Modify `CourseCreate` to:

```python
class CourseCreate(BaseModel):
    topic: str = Field(max_length=500)
    instructions: str | None = Field(default=None, max_length=5000)
    academic_search: AcademicSearchOptions | None = None
```

- [ ] **Step 2: Add academic fields to EvidenceCard model**

In `backend/app/models.py`, add after the existing `verification_note` field on `EvidenceCard` (line ~114):

```python
    is_academic: Mapped[bool] = mapped_column(default=False)
    academic_authors: Mapped[str | None] = mapped_column(Text, nullable=True)
    academic_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    academic_venue: Mapped[str | None] = mapped_column(Text, nullable=True)
    academic_doi: Mapped[str | None] = mapped_column(Text, nullable=True)
```

Add `Integer` to the imports from `sqlalchemy` if not already present.

- [ ] **Step 3: Generate and verify migration**

Run:
```bash
cd /Users/sumo/agent-learn/backend && alembic revision --autogenerate -m "add academic fields to evidence_cards"
```

Verify the generated migration adds 5 columns (`is_academic`, `academic_authors`, `academic_year`, `academic_venue`, `academic_doi`) to `evidence_cards`.

- [ ] **Step 4: Run migration**

Run: `cd /Users/sumo/agent-learn/backend && alembic upgrade head`
Expected: Migration applied successfully.

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas.py backend/app/models.py backend/alembic/versions/
git commit -m "feat: add AcademicSearchOptions schema and EvidenceCard academic fields"
```

---

### Task 9: Academic Provider Backend Routes

**Files:**
- Create: `backend/app/routers/academic_provider_routes.py`
- Modify: `backend/app/main.py:54-58`

- [ ] **Step 1: Create academic_provider_routes.py**

Create `backend/app/routers/academic_provider_routes.py` following the pattern from `search_provider_routes.py`:

```python
"""CRUD routes for academic search provider credentials.

Mirrors search_provider_routes.py — same encryption, same key_cache pattern.
Provider names stored with 'academic:' prefix to avoid ProviderConfig collisions.
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import key_cache
from app.crypto import decrypt_credentials, derive_key, encrypt_credentials, generate_credential_hint
from app.database import get_session
from app.dependencies import require_user
from app.models import ProviderConfig, UserKeySalt
from app.search_service import ACADEMIC_SEARCH_PROVIDERS, get_academic_search_provider_registry
from app.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/academic-providers", tags=["academic-providers"])

PREFIX = "academic:"


class SaveBody(BaseModel):
    provider: str
    credentials: dict[str, str] = {}
    extra_fields: dict[str, str] | None = None


async def _get_or_create_salt(user_id, session: AsyncSession):
    result = await session.execute(
        select(UserKeySalt).where(UserKeySalt.user_id == user_id)
    )
    salt_row = result.scalar_one_or_none()
    if salt_row:
        return salt_row.salt, False
    import os
    salt = os.urandom(16)
    session.add(UserKeySalt(user_id=user_id, salt=salt))
    await session.flush()
    return salt, True


def _get_key(salt: bytes) -> bytearray:
    return derive_key(salt, settings.ENCRYPTION_PEPPER.encode("utf-8"))


@router.get("/registry")
async def get_registry():
    return get_academic_search_provider_registry()


@router.get("")
async def list_providers(
    user=Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    uid = user["user_id"]
    result = await session.execute(
        select(ProviderConfig).where(
            ProviderConfig.user_id == uid,
            ProviderConfig.provider.like(f"{PREFIX}%"),
        )
    )
    configs = result.scalars().all()
    return [
        {
            "provider": c.provider.removeprefix(PREFIX),
            "credential_hint": c.credential_hint,
            "extra_fields": c.extra_fields,
        }
        for c in configs
    ]


@router.post("")
async def save_provider(
    body: SaveBody,
    user=Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    uid = user["user_id"]
    if body.provider not in ACADEMIC_SEARCH_PROVIDERS:
        raise HTTPException(400, f"Unknown academic provider: {body.provider}")

    prefixed = f"{PREFIX}{body.provider}"

    # Check for existing config
    existing = await session.execute(
        select(ProviderConfig).where(
            ProviderConfig.user_id == uid,
            ProviderConfig.provider == prefixed,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, f"Provider {body.provider} already configured. Use PUT to update.")

    salt, _ = await _get_or_create_salt(uid, session)
    key = _get_key(salt)

    creds_to_store = body.credentials or {}
    encrypted = encrypt_credentials(key, json.dumps(creds_to_store))
    hint = generate_credential_hint(body.provider, creds_to_store) if creds_to_store.get("api_key") else "No key required"

    config = ProviderConfig(
        user_id=uid,
        provider=prefixed,
        encrypted_credentials=encrypted,
        credential_hint=hint,
        extra_fields=body.extra_fields,
        is_default=False,
    )
    session.add(config)
    await session.commit()

    key_cache.set_credentials(str(uid), prefixed, creds_to_store)

    return {"provider": body.provider, "credential_hint": hint}


@router.put("/{provider}")
async def update_provider(
    provider: str,
    body: SaveBody,
    user=Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    uid = user["user_id"]
    prefixed = f"{PREFIX}{provider}"

    result = await session.execute(
        select(ProviderConfig).where(
            ProviderConfig.user_id == uid,
            ProviderConfig.provider == prefixed,
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(404, f"Provider {provider} not configured")

    salt, _ = await _get_or_create_salt(uid, session)
    key = _get_key(salt)

    creds_to_store = body.credentials or {}
    config.encrypted_credentials = encrypt_credentials(key, json.dumps(creds_to_store))
    config.credential_hint = generate_credential_hint(provider, creds_to_store) if creds_to_store.get("api_key") else "No key required"
    config.extra_fields = body.extra_fields

    await session.commit()
    key_cache.set_credentials(str(uid), prefixed, creds_to_store)

    return {"provider": provider, "credential_hint": config.credential_hint}


@router.delete("/{provider}")
async def delete_provider(
    provider: str,
    user=Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    uid = user["user_id"]
    prefixed = f"{PREFIX}{provider}"

    result = await session.execute(
        select(ProviderConfig).where(
            ProviderConfig.user_id == uid,
            ProviderConfig.provider == prefixed,
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(404, f"Provider {provider} not configured")

    await session.delete(config)
    await session.commit()
    key_cache.remove_credentials(str(uid), prefixed)

    return {"deleted": provider}


@router.post("/{provider}/test")
async def test_provider(
    provider: str,
    body: SaveBody,
    user=Depends(require_user),
):
    if provider not in ACADEMIC_SEARCH_PROVIDERS:
        raise HTTPException(400, f"Unknown academic provider: {provider}")

    from app.search_service import _ACADEMIC_ADAPTERS

    adapter = _ACADEMIC_ADAPTERS.get(provider)
    if not adapter:
        raise HTTPException(400, f"No adapter for provider: {provider}")

    try:
        results = await adapter(
            "test query", body.credentials or {}, 1, "basic",
            academic_options={"year_range": "all", "min_citations": 0, "open_access_only": False},
        )
        return {"status": "ok", "results": len(results)}
    except Exception as e:
        raise HTTPException(400, f"Test failed: {e}")
```

- [ ] **Step 2: Register router in main.py**

In `backend/app/main.py`, add after the existing router registrations (line ~58):

```python
from app.routers import academic_provider_routes
app.include_router(academic_provider_routes.router, prefix="/api")
```

- [ ] **Step 3: Verify routes load**

Run: `cd /Users/sumo/agent-learn/backend && python -c "from app.main import app; print([r.path for r in app.routes if 'academic' in str(r.path)])""`
Expected: Shows paths like `/api/academic-providers`, `/api/academic-providers/registry`, etc.

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/academic_provider_routes.py backend/app/main.py
git commit -m "feat: add academic provider CRUD routes with encrypted credentials"
```

---

### Task 10: Worker Credential Resolution for Academic Providers

**Files:**
- Modify: `backend/app/worker.py:148-192` (`_resolve_credentials`)
- Modify: `backend/app/worker.py:219-231` (`run_pipeline` call)
- Modify: `backend/app/pipeline.py:208-220` (`run_pipeline` signature)

- [ ] **Step 1: Extend _resolve_credentials to return academic credentials**

In `backend/app/worker.py`, modify `_resolve_credentials` (lines 148-192) to return a third value — `academic_credentials`:

After the search credentials block (around line 190), add:

```python
        # Fetch academic provider configs if academic search enabled
        academic_creds: dict[str, dict] = {}
        academic_config = job.config.get("academic_search", {})
        if academic_config.get("enabled"):
            academic_result = await session.execute(
                select(ProviderConfig).where(
                    ProviderConfig.user_id == job.user_id,
                    ProviderConfig.provider.like("academic:%"),
                )
            )
            for ac in academic_result.scalars().all():
                provider_name = ac.provider.removeprefix("academic:")
                try:
                    ac_creds = json.loads(decrypt_credentials(key, ac.encrypted_credentials))
                    academic_creds[provider_name] = ac_creds
                except Exception as e:
                    logger.warning("Failed to decrypt academic provider %s: %s", ac.provider, e)

    return creds, search_creds, academic_creds
```

Update the return type to `tuple[dict, dict | None, dict[str, dict]]`.

- [ ] **Step 2: Update process_job to pass academic_credentials**

In `backend/app/worker.py`, update the call site (around line 207):

```python
creds, search_creds, academic_creds = await _resolve_credentials(job)
```

And update the `run_pipeline` call (lines 219-231) to pass `academic_credentials=academic_creds` and `academic_options=job.config.get("academic_search")`.

- [ ] **Step 3: Update run_pipeline signature**

In `backend/app/pipeline.py`, update `run_pipeline` signature (lines 208-220) to accept:

```python
    academic_credentials: dict[str, dict] | None = None,
    academic_options: dict | None = None,
```

Pass these through to `_discover_and_plan` and `_research_section` calls.

- [ ] **Step 4: Verify worker starts without errors**

Run: `cd /Users/sumo/agent-learn/backend && python -c "from app.worker import process_job; print('OK')"`
Expected: `OK` — no import errors.

- [ ] **Step 5: Commit**

```bash
git add backend/app/worker.py backend/app/pipeline.py
git commit -m "feat: resolve academic provider credentials in worker pipeline"
```

---

### Task 11: Pipeline — Academic Search in Discovery & Section Research

**Files:**
- Modify: `backend/app/agent_service.py:261-351` (`generate_outline`)
- Modify: `backend/app/agent_service.py:522-593` (`research_section`)
- Modify: `backend/app/pipeline.py` (pass-through in `_discover_and_plan` and `_research_section`)

- [ ] **Step 1: Add academic search to generate_outline**

In `backend/app/agent_service.py`, modify `generate_outline()` to accept `academic_credentials` and `academic_options` params. After the existing web search calls (around line 293), add:

```python
    # Academic search (if enabled)
    academic_results: list[SearchResult] = []
    if academic_credentials and academic_options and academic_options.get("enabled"):
        from app.search_service import academic_search as run_academic_search
        academic_tasks = []
        for q in queries:
            academic_tasks.append(
                run_academic_search(q, academic_credentials, academic_options, max_results=5)
            )
        academic_batches = await asyncio.gather(*academic_tasks, return_exceptions=True)
        for batch in academic_batches:
            if isinstance(batch, list):
                academic_results.extend(batch)
        # Deduplicate across all queries
        academic_results = deduplicate_academic_results(academic_results)

    all_results = web_results + academic_results
```

Pass `all_results` to the discovery researcher instead of just web results.

- [ ] **Step 2: Add academic search to research_section**

In `backend/app/agent_service.py`, modify `research_section()` to accept `academic_credentials` and `academic_options` params. After the existing web search calls (around line 550), add the same pattern:

```python
    # Academic search per question (if enabled)
    if academic_credentials and academic_options and academic_options.get("enabled"):
        from app.search_service import academic_search as run_academic_search
        for question in brief.questions:
            try:
                acad_results = await run_academic_search(
                    question, academic_credentials, academic_options, max_results=5,
                )
                all_search_results.extend(acad_results)
            except Exception as e:
                logger.warning("[research] Academic search failed for question '%s': %s", question[:60], e)
```

- [ ] **Step 3: Update pipeline pass-through functions**

In `backend/app/pipeline.py`, update `_discover_and_plan` and `_research_section` to accept and pass `academic_credentials` and `academic_options` to the agent_service functions.

In `backend/app/agent_service.py`, update `run_discover_and_plan` and `run_research_section` to accept and pass the same parameters.

- [ ] **Step 4: Verify no import errors**

Run: `cd /Users/sumo/agent-learn/backend && python -c "from app.pipeline import run_pipeline; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent_service.py backend/app/pipeline.py
git commit -m "feat: integrate academic search into discovery and section research phases"
```

---

### Task 12: Agent Prompt Updates

**Files:**
- Modify: `backend/app/agent.py:180-202` (DISCOVERY_RESEARCHER_PROMPT)
- Modify: `backend/app/agent.py:114-149` (WRITER_PROMPT)
- Modify: `backend/app/agent.py:242-261` (EDITOR_PROMPT)

- [ ] **Step 1: Update discovery researcher prompt**

In `backend/app/agent.py`, append to `DISCOVERY_RESEARCHER_PROMPT` (around line 200):

```
Some search results may be from academic research papers (marked with [ACADEMIC] in the source list). When available, prefer academic sources for grounding your analysis — they provide peer-reviewed evidence. Note which findings come from academic vs. web sources in your synthesis.
```

- [ ] **Step 2: Update writer prompt**

In `backend/app/agent.py`, append to `WRITER_PROMPT` citation instructions (around line 145):

```
For evidence cards marked as academic sources, naturally incorporate the author(s) and year into the text before the citation marker. Example: "According to Smith et al. (2023), transformers outperform RNNs on sequence tasks [3]."
```

- [ ] **Step 3: Update editor prompt**

In `backend/app/agent.py`, append to `EDITOR_PROMPT` (around line 258):

```
After the "What Comes Next" section, if the section cites any academic evidence cards (those with is_academic=True), append a "## References" section listing only the academic papers. Format each entry in APA style:

[N] Last, F., Last, F., & Last, F. (Year). Title. *Venue*. DOI_URL

Only include papers actually cited with [N] markers in the section. If no academic papers are cited, do not add a References section.
```

- [ ] **Step 4: Verify prompts load correctly**

Run: `cd /Users/sumo/agent-learn/backend && python -c "from app.agent import DISCOVERY_RESEARCHER_PROMPT, WRITER_PROMPT, EDITOR_PROMPT; print('Prompts loaded OK')"`
Expected: `Prompts loaded OK`

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent.py
git commit -m "feat: update agent prompts for academic source handling and references"
```

---

### Task 13: Evidence Card Creation — Academic Metadata

**Files:**
- Modify: `backend/app/agent_service.py` (where evidence cards are created from SearchResults)

- [ ] **Step 1: Find and update evidence card creation**

In `backend/app/agent_service.py`, locate where `EvidenceCard` rows are created from researcher output (in `run_research_section`). When creating cards, if the source search result was academic, populate the academic fields:

```python
card = EvidenceCard(
    course_id=course_id,
    section_position=position,
    claim=item.claim,
    source_url=item.source_url,
    source_title=item.source_title,
    source_tier=item.source_tier,
    passage=item.passage,
    retrieved_date=date.today(),
    confidence=item.confidence,
    caveat=item.caveat,
    explanation=item.explanation,
    # Academic metadata — populated when source is academic
    is_academic=getattr(item, "is_academic", False),
    academic_authors=getattr(item, "academic_authors", None),
    academic_year=getattr(item, "academic_year", None),
    academic_venue=getattr(item, "academic_venue", None),
    academic_doi=getattr(item, "academic_doi", None),
)
```

The section researcher's output schema (`EvidenceCardItem` in `agent.py`) needs academic fields added too:

```python
class EvidenceCardItem(BaseModel):
    claim: str
    source_url: str
    source_title: str
    source_tier: int
    passage: str
    confidence: float
    caveat: str | None = None
    explanation: str
    # Academic metadata
    is_academic: bool = False
    academic_authors: str | None = None
    academic_year: int | None = None
    academic_venue: str | None = None
    academic_doi: str | None = None
```

- [ ] **Step 2: Update section researcher prompt to populate academic fields**

Add to the section researcher prompt instructions to set `is_academic=True` and populate `authors`, `year`, `venue`, `doi` when the source is an academic paper (identifiable by `[ACADEMIC]` tag or by URL containing arxiv.org, semanticscholar.org, doi.org, etc.).

- [ ] **Step 3: Verify no import errors**

Run: `cd /Users/sumo/agent-learn/backend && python -c "from app.agent_service import research_section; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add backend/app/agent_service.py backend/app/agent.py
git commit -m "feat: populate academic metadata on evidence cards from research results"
```

---

### Task 14: Frontend — Academic Provider Settings Section

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/app/settings/page.tsx`

- [ ] **Step 1: Add API functions for academic providers**

In `frontend/src/lib/api.ts`, add after the existing search provider functions:

```typescript
// Academic search providers
export async function getAcademicProviderRegistry(token?: string | null): Promise<Record<string, ProviderDefinition>> {
  const res = await fetch(`${API_BASE}/api/academic-providers/registry`, { headers: authHeaders(token) });
  if (!res.ok) throw new Error('Failed to fetch academic provider registry');
  return res.json();
}

export async function getAcademicProviders(token?: string | null): Promise<ProviderConfig[]> {
  const res = await fetch(`${API_BASE}/api/academic-providers`, { headers: authHeaders(token) });
  if (!res.ok) throw new Error('Failed to fetch academic providers');
  return res.json();
}

export async function saveAcademicProvider(
  data: { provider: string; credentials: Record<string, string>; extra_fields?: Record<string, string> },
  token?: string | null
): Promise<ProviderConfig> {
  const res = await fetch(`${API_BASE}/api/academic-providers`, {
    method: 'POST',
    headers: authHeaders(token),
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed' }));
    throw new Error(err.detail || 'Failed to save');
  }
  return res.json();
}

export async function updateAcademicProvider(
  provider: string,
  data: { provider: string; credentials: Record<string, string>; extra_fields?: Record<string, string> },
  token?: string | null
): Promise<ProviderConfig> {
  const res = await fetch(`${API_BASE}/api/academic-providers/${provider}`, {
    method: 'PUT',
    headers: authHeaders(token),
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed' }));
    throw new Error(err.detail || 'Failed to update');
  }
  return res.json();
}

export async function deleteAcademicProvider(provider: string, token?: string | null): Promise<void> {
  const res = await fetch(`${API_BASE}/api/academic-providers/${provider}`, {
    method: 'DELETE',
    headers: authHeaders(token),
  });
  if (!res.ok) throw new Error('Failed to delete');
}

export async function testAcademicProvider(
  provider: string,
  data: { provider: string; credentials: Record<string, string> },
  token?: string | null
): Promise<{ status: string }> {
  const res = await fetch(`${API_BASE}/api/academic-providers/${provider}/test`, {
    method: 'POST',
    headers: authHeaders(token),
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Test failed' }));
    throw new Error(err.detail || 'Test failed');
  }
  return res.json();
}
```

- [ ] **Step 2: Add Academic Search Providers section to settings page**

In `frontend/src/app/settings/page.tsx`, add a third section below the existing Search Provider section. Follow the exact same pattern: provider button grid, dynamic form fields from registry, save/test/delete buttons, green dot for configured providers. The section title is "Academic Search Providers" with a description "Configure providers for research paper search (Semantic Scholar, arXiv, OpenAlex)."

Use the same state management pattern — new state variables: `academicRegistry`, `academicProviders`, `selectedAcademic`, `academicFormValues`, `academicSaving`, `academicTesting`.

Load `getAcademicProviderRegistry` and `getAcademicProviders` in the existing `useEffect`.

- [ ] **Step 3: Test the settings page renders**

Run: `cd /Users/sumo/agent-learn/frontend && npm run build`
Expected: Build succeeds with no type errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/app/settings/page.tsx
git commit -m "feat: add academic search provider settings UI section"
```

---

### Task 15: Frontend — Course Creation Toggle & Options

**Files:**
- Modify: `frontend/src/app/page.tsx`
- Modify: `frontend/src/lib/api.ts` (createCourse/createCourseStream payloads)

- [ ] **Step 1: Add state and academic search options to Step 2**

In `frontend/src/app/page.tsx`, add state variables:

```typescript
const [useResearchPapers, setUseResearchPapers] = useState(false);
const [yearRange, setYearRange] = useState('5y');
const [minCitations, setMinCitations] = useState(0);
const [openAccessOnly, setOpenAccessOnly] = useState(false);
const [hasAcademicProviders, setHasAcademicProviders] = useState(false);
```

Add a `useEffect` to check if academic providers are configured on page load:

```typescript
useEffect(() => {
  if (token) {
    getAcademicProviders(token).then(providers => {
      setHasAcademicProviders(providers.length > 0);
    }).catch(() => {});
  }
}, [token]);
```

In Step 2 UI, after the Extra Instructions textarea, add:

```tsx
{/* Research Papers Toggle */}
<div className="space-y-3">
  <div className="flex items-center justify-between">
    <Label htmlFor="research-toggle">Use Research Papers</Label>
    <button
      id="research-toggle"
      role="switch"
      aria-checked={useResearchPapers}
      disabled={!hasAcademicProviders}
      onClick={() => setUseResearchPapers(!useResearchPapers)}
      className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
        useResearchPapers ? 'bg-primary' : 'bg-muted'
      } ${!hasAcademicProviders ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
      title={!hasAcademicProviders ? 'Configure academic search providers in Settings first' : ''}
    >
      <span className={`inline-block h-4 w-4 rounded-full bg-white transition-transform ${
        useResearchPapers ? 'translate-x-6' : 'translate-x-1'
      }`} />
    </button>
  </div>

  {useResearchPapers && (
    <div className="space-y-3 pl-1 border-l-2 border-primary/20 ml-1">
      <div className="space-y-1">
        <Label className="text-sm">Year Range</Label>
        <select
          value={yearRange}
          onChange={(e) => setYearRange(e.target.value)}
          className="w-full rounded-md border bg-background px-3 py-2 text-sm"
        >
          <option value="5y">Last 5 years</option>
          <option value="10y">Last 10 years</option>
          <option value="20y">Last 20 years</option>
          <option value="all">All time</option>
        </select>
      </div>

      <div className="space-y-1">
        <Label className="text-sm">Minimum Citations</Label>
        <select
          value={minCitations}
          onChange={(e) => setMinCitations(Number(e.target.value))}
          className="w-full rounded-md border bg-background px-3 py-2 text-sm"
        >
          <option value={0}>Any</option>
          <option value={10}>10+</option>
          <option value={50}>50+</option>
          <option value={100}>100+</option>
        </select>
      </div>

      <div className="flex items-center justify-between">
        <Label className="text-sm" htmlFor="oa-toggle">Open Access Only</Label>
        <button
          id="oa-toggle"
          role="switch"
          aria-checked={openAccessOnly}
          onClick={() => setOpenAccessOnly(!openAccessOnly)}
          className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
            openAccessOnly ? 'bg-primary' : 'bg-muted'
          } cursor-pointer`}
        >
          <span className={`inline-block h-3 w-3 rounded-full bg-white transition-transform ${
            openAccessOnly ? 'translate-x-5' : 'translate-x-1'
          }`} />
        </button>
      </div>
    </div>
  )}
</div>
```

- [ ] **Step 2: Pass academic_search in course creation payload**

In `frontend/src/app/page.tsx`, update the course creation call to include academic search options:

```typescript
const academicSearch = useResearchPapers
  ? { enabled: true, year_range: yearRange, min_citations: minCitations, open_access_only: openAccessOnly }
  : undefined;
```

Update `createCourse` and `createCourseStream` calls in `frontend/src/lib/api.ts` to accept and pass `academic_search`:

```typescript
export async function createCourse(
  topic: string,
  instructions?: string,
  token?: string | null,
  academicSearch?: { enabled: boolean; year_range: string; min_citations: number; open_access_only: boolean },
): Promise<Course> {
  const res = await fetch(`${API_BASE}/api/courses`, {
    method: 'POST',
    headers: authHeaders(token),
    body: JSON.stringify({
      topic,
      instructions: instructions || null,
      academic_search: academicSearch || null,
    }),
  });
  // ... rest unchanged
}
```

Apply same change to `createCourseStream`.

- [ ] **Step 3: Update course creation handler in page.tsx**

Update the `handleGenerate` function (or equivalent) to pass `academicSearch` to `createCourse`/`createCourseStream`.

- [ ] **Step 4: Verify build succeeds**

Run: `cd /Users/sumo/agent-learn/frontend && npm run build`
Expected: Build succeeds.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/page.tsx frontend/src/lib/api.ts
git commit -m "feat: add research papers toggle and filters to course creation UI"
```

---

### Task 16: Backend — Pass academic_search Through Course Creation

**Files:**
- Modify: `backend/app/routers/courses.py` (course creation endpoint)

- [ ] **Step 1: Update course creation to store academic_search in pipeline config**

In `backend/app/routers/courses.py`, find the endpoint that creates courses and pipeline jobs. When building `PipelineJob.config`, include the `academic_search` options from the request:

```python
config = {
    "provider": provider,
    "model": model,
    "search_provider": search_provider,
    "extra_fields": extra_fields,
}
if body.academic_search and body.academic_search.enabled:
    config["academic_search"] = body.academic_search.model_dump()
```

- [ ] **Step 2: Verify the endpoint accepts the new field**

Run: `cd /Users/sumo/agent-learn/backend && python -c "
from app.schemas import CourseCreate
c = CourseCreate(topic='test', academic_search={'enabled': True, 'year_range': '5y', 'min_citations': 10, 'open_access_only': False})
print(c.academic_search)
"`
Expected: Shows `AcademicSearchOptions(enabled=True, year_range='5y', min_citations=10, open_access_only=False)`

- [ ] **Step 3: Commit**

```bash
git add backend/app/routers/courses.py
git commit -m "feat: store academic_search options in pipeline job config"
```

---

### Task 17: Key Cache — Academic Provider Support

**Files:**
- Modify: `backend/app/key_cache.py`
- Modify: `backend/app/routers/auth_routes.py` (login credential loading)

- [ ] **Step 1: Update key_cache to handle academic: prefix**

In `backend/app/key_cache.py`, add a helper function:

```python
def get_all_academic_providers(user_id: str) -> dict[str, dict]:
    """Return all configured academic provider credentials for a user.

    Returns: {provider_name (without prefix): credentials_dict}
    """
    entry = _get_entry(user_id)
    if not entry:
        return {}
    return {
        k.removeprefix("academic:"): v
        for k, v in entry.credentials.items()
        if k.startswith("academic:")
    }
```

- [ ] **Step 2: Update login credential loading**

In `backend/app/routers/auth_routes.py`, the existing `_load_provider_keys()` function already loads ALL `ProviderConfig` rows for a user and populates the cache. Since academic providers use the same table with an `academic:` prefix, they'll be loaded automatically. Verify this is the case — no changes should be needed if the function iterates all configs without filtering.

- [ ] **Step 3: Commit**

```bash
git add backend/app/key_cache.py
git commit -m "feat: add academic provider credential retrieval to key_cache"
```

---

### Task 18: End-to-End Smoke Test

**Files:**
- No new files — manual verification

- [ ] **Step 1: Run backend tests**

Run: `cd /Users/sumo/agent-learn/backend && python -m pytest tests/ -v`
Expected: All tests pass, including new academic search tests.

- [ ] **Step 2: Build frontend**

Run: `cd /Users/sumo/agent-learn/frontend && npm run build`
Expected: Build succeeds with no errors.

- [ ] **Step 3: Run database migration**

Run: `cd /Users/sumo/agent-learn/backend && alembic upgrade head`
Expected: Migration applied.

- [ ] **Step 4: Start services and verify manually**

Start backend + frontend. Navigate to Settings → Academic Search Providers:
- Verify 3 providers shown (Semantic Scholar, arXiv, OpenAlex)
- Configure arXiv (no key needed — just save)
- Navigate to course creation → Step 2
- Verify "Use Research Papers" toggle appears and is enabled
- Toggle on → verify Year Range, Min Citations, Open Access Only appear

- [ ] **Step 5: Final commit with any fixes**

```bash
git add -A
git commit -m "fix: address any issues found during smoke testing"
```
