# Academic Research Paper Search Integration

## Overview

Add dedicated academic search (Semantic Scholar, arXiv, OpenAlex) to the course generation pipeline so content is grounded in peer-reviewed research papers. A UI toggle on the course creation form enables academic search with configurable filters. When enabled, academic search runs in parallel with web search during both discovery and section research phases. Academic sources appear as inline citations and in a dedicated References section per course section.

## Academic Search Providers

### Semantic Scholar

- **Endpoint:** `GET https://api.semanticscholar.org/graph/v1/paper/search`
- **Params:** `query`, `fields=title,url,abstract,year,authors,venue,citationCount,externalIds,openAccessPdf,publicationTypes`, `limit` (max 100), `offset`, `year` (range filter, e.g. `2021-`), `minCitationCount`, `publicationTypes`
- **Auth:** Optional API key via `x-api-key` header. Recommended for reliability — unauthenticated rate limit is 1,000 req/sec shared among ALL unauthenticated users globally (unreliable under load). Authenticated: 1 RPS guaranteed.
- **Limits:** Up to 1,000 relevance-ranked results, 100 per request.
- **Response:** JSON `{ total, offset, next, data[] }`. Each paper: `{ paperId, title, url, abstract (nullable), year, authors: [{ authorId, name }], venue, citationCount, externalIds: { DOI, ... }, openAccessPdf: { url, status } | null, publicationTypes: [str] }`
- **URL construction:** API returns `url` field when requested. Fallback: `https://www.semanticscholar.org/paper/{paperId}`
- **Open access filter:** No server-side param. Request `openAccessPdf` field, filter client-side: keep results where `openAccessPdf` is not null.
- **Null abstracts:** Some papers have abstracts elided by publishers. Skip these results (no content to ground on).
- **Env var:** `SEMANTIC_SCHOLAR_API_KEY` (optional, improves reliability)

### arXiv

- **Endpoint:** `GET http://export.arxiv.org/api/query`
- **Params:** `search_query` (field prefixes: `ti:`, `au:`, `abs:`, `all:`, boolean: `AND`, `OR`, `ANDNOT`), `start`, `max_results` (max 2,000/request), `sortBy=relevance`, `sortOrder=descending`
- **Auth:** None required.
- **Limits:** **Hard limit: 1 request per 3 seconds, single connection.** Violation may result in access being blocked. This is a requirement, not a recommendation.
- **Response:** Atom XML. Each `<entry>`: `<title>`, `<summary>` (abstract), `<author><name>`, `<published>`, `<updated>`, `<arxiv:doi>`, `<link rel="alternate">` (abstract page), `<link title="pdf">` (PDF URL), `<arxiv:primary_category>`, `<arxiv:journal_ref>`
- **Year filter:** Via `submittedDate` range in query string: `AND submittedDate:[YYYYMMDD0000+TO+YYYYMMDD2359]`
- **Min citations filter:** Not supported (arXiv has no citation data). Skipped.
- **Open access filter:** All arXiv papers are open access. No filter needed.
- **Relevance ranking is weak** compared to Semantic Scholar and OpenAlex. arXiv is most valuable for coverage of preprints not yet indexed elsewhere.
- **Adapter must enforce:** Sequential requests with `asyncio.sleep(3)` between calls. Retry with exponential backoff on 429 responses.

### OpenAlex

- **Endpoint:** `GET https://api.openalex.org/works`
- **Params:** `search`, `per_page` (max 100), `page`, `filter` (composable: `publication_year:>YYYY`, `cited_by_count:>N`, `is_oa:true`), `api_key`
- **Auth:** **API key required** (as of February 13, 2026). Free tier: 100,000 credits/day. List queries cost 10 credits each (~10,000 searches/day on free tier). Get key at https://openalex.org/settings/api.
- **Limits:** 100 per page. Credit-based rate limiting (not request-based).
- **Response:** JSON `{ meta: { count, page, per_page }, results[] }`. Each work: `{ id, doi, title, display_name, relevance_score, publication_year, publication_date, type, authorships: [{ author: { display_name, orcid }, author_position }], abstract_inverted_index, primary_location: { source: { display_name }, landing_page_url, pdf_url }, open_access: { is_oa, oa_status, oa_url }, cited_by_count }`
- **Abstract reconstruction:** `abstract_inverted_index` is a dict mapping words to position arrays (e.g. `{"machine": [0, 5], "learning": [1]}`). Adapter must reconstruct plain text by inverting: sort all (word, position) pairs by position, join with spaces.
- **Env var:** `OPENALEX_API_KEY` (required)

### Filter Application Per API

| Filter | Semantic Scholar | arXiv | OpenAlex |
|--------|-----------------|-------|----------|
| Year range | `year` param (e.g. `2021-`) | `submittedDate` range in query | `filter=publication_year:>YYYY` |
| Min citations | `minCitationCount` param | N/A (no citation data) | `filter=cited_by_count:>N` |
| Open access | Client-side: filter where `openAccessPdf` is not null | Always OA (skip filter) | `filter=is_oa:true` |

### Deduplication

The same paper may appear from multiple APIs. Before merging academic results with web results, deduplicate by:
1. DOI match (primary — most reliable)
2. Title similarity fallback (normalized lowercase, strip punctuation, >90% match)

Keep the result with the richest metadata (prefer Semantic Scholar > OpenAlex > arXiv for metadata completeness).

## SearchResult Changes

```python
@dataclass
class SearchResult:
    title: str
    url: str
    content: str          # abstract text for academic sources
    score: float = 0.0
    # New academic fields
    authors: list[str] | None = None
    year: int | None = None
    venue: str | None = None
    citation_count: int | None = None
    doi: str | None = None
    is_academic: bool = False
```

Existing web search adapters unchanged (`is_academic=False` by default). All three academic adapters set `is_academic=True` and populate the academic metadata fields.

## UI — Toggle & Academic Options

On the course creation form (Step 2: Customization), add a section below existing options:

**"Use Research Papers" toggle** — off by default. When toggled on, reveals:

1. **Year Range** — dropdown:
   - "Last 5 years" (default) → `"5y"`
   - "Last 10 years" → `"10y"`
   - "Last 20 years" → `"20y"`
   - "All time" → `"all"`

2. **Minimum Citations** — dropdown:
   - "Any" (default) → `0`
   - "10+" → `10`
   - "50+" → `50`
   - "100+" → `100`

3. **Open Access Only** — toggle, off by default.

These options are only visible when the research papers toggle is on.

## Schema Changes

### Backend

```python
class AcademicSearchOptions(BaseModel):
    enabled: bool = False
    year_range: str = "5y"         # "5y" | "10y" | "20y" | "all"
    min_citations: int = 0         # 0, 10, 50, 100
    open_access_only: bool = False

class CourseCreate(BaseModel):
    topic: str = Field(max_length=500)
    instructions: str | None = Field(default=None, max_length=5000)
    academic_search: AcademicSearchOptions | None = None
```

Options stored in `PipelineJob.config["academic_search"]` so the worker has access.

### Frontend

`createCourse` and `createCourseStream` pass `academic_search` object in the request body. The `Course` type does not need changes — academic search is a pipeline concern, not a course property.

## Configuration

Two new optional environment variables:

- `SEMANTIC_SCHOLAR_API_KEY` — Optional. Improves rate limit reliability (guaranteed 1 RPS vs shared unauthenticated pool). Passed as `x-api-key` header.
- `OPENALEX_API_KEY` — **Required** for academic search to work. Free tier (100k credits/day) is sufficient. Without this key, the OpenAlex adapter is disabled and logged as warning.

These are app-level env vars (not per-user) since the keys are free/shared infrastructure.

## Pipeline Integration

### Phase 1 — Discovery

When `config.academic_search.enabled` is `True`:

1. Generate 5 search queries (existing).
2. Run web search (existing, parallel across queries).
3. Run academic search — same 5 queries sent to Semantic Scholar and OpenAlex in parallel. arXiv queries run sequentially (3s delay between each). User-configured filters applied per API.
4. Deduplicate academic results by DOI/title.
5. Merge web + academic results into a single list.
6. Pass merged results to discovery researcher. Researcher prompt updated to note which results are from academic sources (`is_academic=True`) and to prefer them for grounding the outline.

### Phase 2 — Section Research

When `config.academic_search.enabled` is `True`:

1. Per section: get research brief questions (existing).
2. Run web search per question (existing).
3. Run academic search per question — Semantic Scholar + OpenAlex in parallel, arXiv sequential with 3s delay. Filters applied.
4. Deduplicate and merge results before passing to section researcher.
5. Section researcher extracts evidence cards. Academic sources automatically get `source_tier=1` and `is_academic=True`.

### Phases 3-5 — Unchanged Logic

- **Verifier:** Academic cards treated as high-confidence Tier 1. No logic changes.
- **Writer:** Inline `[N]` citations as today. Updated prompt to naturally mention author(s) and year for academic sources (e.g., "According to Smith et al. (2023), ... [3]").
- **Editor:** Appends a References section listing only academic papers in APA format. Only appears if the section has at least one academic evidence card.

### Fallback

If all 3 academic APIs fail for a query, the pipeline continues with web-only results. No hard failure. Logged as warning. Individual API failures don't block the others — each runs independently.

### Rate Limiting

- **arXiv:** Hard 3-second delay between requests, single connection. Enforced with `asyncio.sleep(3)`. Retry on 429 with exponential backoff (3s, 6s, 12s, max 3 retries).
- **Semantic Scholar:** No enforced delay at our volume. If `SEMANTIC_SCHOLAR_API_KEY` is set, include in `x-api-key` header. Retry on 429 with 1s backoff.
- **OpenAlex:** Credit-based (10 credits/list query). At 5-10 queries per course, negligible impact on 100k daily budget. Retry on 429 with 1s backoff.

## EvidenceCard Model Changes

5 new nullable columns on `evidence_cards`:

```python
is_academic: Mapped[bool] = mapped_column(default=False)
authors: Mapped[str | None] = mapped_column(Text, nullable=True)      # comma-separated
year: Mapped[int | None] = mapped_column(Integer, nullable=True)
venue: Mapped[str | None] = mapped_column(Text, nullable=True)
doi: Mapped[str | None] = mapped_column(Text, nullable=True)
```

Existing cards unaffected (default `is_academic=False`, nulls for the rest). Requires a DB migration.

## Writer & Citation Changes

### Inline Citations

No change to existing `[N]` citation mechanics. Writer prompt gains one instruction: for academic evidence cards, naturally incorporate author names and year in the text before the citation marker.

### References Section

The editor appends a References block at the end of each section, after Key Takeaways / What Comes Next:

```markdown
## References

[3] Smith, J., Lee, K., & Patel, R. (2023). Attention mechanisms in modern NLP.
    *Journal of Machine Learning Research*, 24(1), 1-45. https://doi.org/10.xxxx

[7] Chen, W. & Davis, M. (2022). Scaling laws for neural language models.
    *Proceedings of NeurIPS 2022*. https://doi.org/10.yyyy
```

- Only academic evidence cards cited in the section are listed.
- If a section has zero academic citations, no References block is added.
- Editor prompt updated to verify consistency between inline `[N]` markers and the References list.

## Files Changed

| File | Change |
|------|--------|
| `backend/app/search_service.py` | 3 new adapters (Semantic Scholar, arXiv, OpenAlex), `SearchResult` fields, `SEARCH_PROVIDERS` entries, deduplication helper, abstract reconstruction for OpenAlex |
| `backend/app/schemas.py` | `AcademicSearchOptions` model, `CourseCreate` gains `academic_search` field |
| `backend/app/models.py` | 5 new columns on `EvidenceCard` |
| `backend/app/agent_service.py` | Parallel academic search calls in discovery + section research, dedup before merge |
| `backend/app/agent.py` | Updated prompts for discovery researcher, writer, editor |
| `backend/app/pipeline.py` | Pass academic config through to agent_service calls |
| `frontend/src/app/page.tsx` | Toggle + sub-options UI on Step 2 |
| `frontend/src/lib/api.ts` | Pass `academic_search` in course creation payload |
| DB migration | Add 5 columns to `evidence_cards` |

## What Stays the Same

- Blackboard, glossary, concept ownership — untouched.
- Verification logic — unchanged, academic cards just score higher naturally.
- Checkpoint/resume — no new checkpoints needed.
- Existing search providers — unchanged.
- Course model — no new fields.
