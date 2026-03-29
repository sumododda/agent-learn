# Academic Research Paper Search Integration

## Overview

Add dedicated academic search (Semantic Scholar, arXiv, OpenAlex) to the course generation pipeline so content is grounded in peer-reviewed research papers. A UI toggle on the course creation form enables academic search with configurable filters. When enabled, academic search runs in parallel with web search during both discovery and section research phases. Academic sources appear as inline citations and in a dedicated References section per course section.

## Academic Search Providers

### Semantic Scholar

- **Endpoint:** `GET https://api.semanticscholar.org/graph/v1/paper/search`
- **Params:** `query`, `fields=title,url,abstract,year,authors,venue,citationCount,externalIds,openAccessPdf,publicationTypes`, `limit` (max 100), `offset`
- **Auth:** None required. Optional API key via `x-api-key` header for higher rate limits.
- **Limits:** Up to 1,000 relevance-ranked results, 100 per request.
- **Response:** JSON `{ total, offset, next, data[] }`

### arXiv

- **Endpoint:** `GET http://export.arxiv.org/api/query`
- **Params:** `search_query` (field prefixes: `ti:`, `au:`, `abs:`, `all:`), `start`, `max_results` (max 2,000/request), `sortBy=relevance`
- **Auth:** None required.
- **Limits:** 3-second delay between requests recommended. 30,000 total results max.
- **Response:** Atom XML with `<entry>` elements containing `<title>`, `<summary>`, `<author>`, `<published>`, `<arxiv:doi>`, `<link>` (PDF/abstract).

### OpenAlex

- **Endpoint:** `GET https://api.openalex.org/works`
- **Params:** `search`, `per_page` (max 100), `page`, `filter` (e.g. `publication_year:>2020`, `cited_by_count:>10`, `is_oa:true`), `api_key`
- **Auth:** API key required (free, obtained at openalex.org/settings/api).
- **Limits:** 100 per page, cursor pagination available.
- **Response:** JSON `{ meta: { count, page }, results[] }` with `title`, `doi`, `publication_year`, `cited_by_count`, `authorships[]`, `abstract_inverted_index`, `primary_location`, `open_access`.

### Filter Application Per API

| Filter | Semantic Scholar | arXiv | OpenAlex |
|--------|-----------------|-------|----------|
| Year range | `year` param (e.g. `2021-`) | `submittedDate` in query | `filter=publication_year:>YYYY` |
| Min citations | `minCitationCount` param | N/A (no citation data) | `filter=cited_by_count:>N` |
| Open access | `openAccessPdf` param | Always OA (skip filter) | `filter=is_oa:true` |

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

## Pipeline Integration

### Phase 1 — Discovery

When `config.academic_search.enabled` is `True`:

1. Generate 5 search queries (existing).
2. Run web search (existing, parallel across queries).
3. Run academic search — same 5 queries sent to all 3 academic APIs simultaneously, with user-configured filters applied.
4. Merge web + academic results into a single list.
5. Pass merged results to discovery researcher. Researcher prompt updated to note which results are from academic sources and to prefer them for grounding the outline.

### Phase 2 — Section Research

When `config.academic_search.enabled` is `True`:

1. Per section: get research brief questions (existing).
2. Run web search per question (existing).
3. Run academic search per question — same questions sent to all 3 APIs with filters.
4. Merge results before passing to section researcher.
5. Section researcher extracts evidence cards. Academic sources automatically get `source_tier=1` and `is_academic=True`.

### Phases 3-5 — Unchanged Logic

- **Verifier:** Academic cards treated as high-confidence Tier 1. No logic changes.
- **Writer:** Inline `[N]` citations as today. Updated prompt to naturally mention author(s) and year for academic sources (e.g., "According to Smith et al. (2023), ... [3]").
- **Editor:** Appends a References section listing only academic papers in APA format. Only appears if the section has at least one academic evidence card.

### Fallback

If all 3 academic APIs fail for a query, the pipeline continues with web-only results. No hard failure. Logged as warning.

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
| `backend/app/search_service.py` | 3 new adapters (Semantic Scholar, arXiv, OpenAlex), `SearchResult` fields, `SEARCH_PROVIDERS` entries |
| `backend/app/schemas.py` | `AcademicSearchOptions` model, `CourseCreate` gains `academic_search` field |
| `backend/app/models.py` | 5 new columns on `EvidenceCard` |
| `backend/app/agent_service.py` | Parallel academic search calls in discovery + section research |
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
