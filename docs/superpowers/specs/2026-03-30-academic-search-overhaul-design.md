# Academic Search Overhaul — Design Spec

**Date:** 2026-03-30
**Status:** Draft
**Problem:** Academic search fires Semantic Scholar, arXiv, and OpenAlex in parallel per query. With 4 questions/section across 5 sections, that's 20+ concurrent requests hitting providers with 1 req/sec (S2) and 1 req/3sec (arXiv) limits. Result: constant 429 cascades, failed searches, degraded course quality.

## Decision Summary

| Decision | Choice |
|----------|--------|
| Providers | OpenAlex (primary) + Serper Google Scholar |
| Execution | Both in parallel, deduplicate |
| PDF enrichment | Unpaywall (free, DOI-based) |
| Key management | Server-level via k8s secrets (not per-user) |
| Old providers | Remove Semantic Scholar + arXiv entirely |
| Architecture | New `academic_search.py` module, separate from `search_service.py` |

## 1. New Module: `backend/app/academic_search.py`

A standalone module with zero dependency on the per-user key system. Server keys loaded from `Settings` at import time.

### 1.1 Data Model

```python
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
```

This replaces the overloaded `SearchResult` with `is_academic=True` pattern. The `AcademicResult` is purpose-built for academic data.

### 1.2 Provider Adapters

**OpenAlex adapter** — `_search_openalex(query, max_results, options) -> list[AcademicResult]`
- Ported from existing `_search_openalex` in `search_service.py`
- Uses `settings.OPENALEX_API_KEY` passed as `?api_key=` query param (not header)
- Keeps: year filtering (`filter=publication_year:>YYYY`), min citations (`filter=cited_by_count:>N`), open access filter (`filter=is_oa:true`), inverted index reconstruction
- `select` param: root-level fields only (cannot select nested paths like `open_access.is_oa`). Current fields: `id,doi,title,display_name,relevance_score,publication_year,cited_by_count,authorships,abstract_inverted_index,primary_location,open_access`
- `relevance_score` only present when `search=` param is used
- PDF URL extraction: `primary_location.pdf_url` then `open_access.oa_url` then `best_oa_location.url_for_pdf` (fallback chain)
- Endpoint: `GET https://api.openalex.org/works`

**Serper Scholar adapter** — `_search_serper_scholar(query, max_results, options) -> list[AcademicResult]`
- New adapter targeting `POST https://google.serper.dev/scholar`
- Uses `settings.SERPER_API_KEY` directly
- Headers: `X-API-KEY` + `Content-Type: application/json`
- Request body: `{"q": query, "num": max_results}`. Year filtering via `as_ylo`/`as_yhi` params (not `tbs`).
- Response: `organic[]` array, each item has:
  - `title` (string) — paper title
  - `link` (string) — URL to paper
  - `snippet` (string) — abstract excerpt
  - `publicationInfo` (string) — mashed authors + venue + year + domain, format: `"A Author, B Author... - Journal Name, 2024 - publisher.com"`. Must be parsed to extract authors and venue.
  - `citedBy` (int, optional) — citation count as top-level integer
  - `year` (int, optional) — publication year as top-level integer
  - `pdfLink` (string, optional) — direct PDF URL, only present when available
- **Does NOT return DOIs.** Deduplication against OpenAlex results must use normalized title matching when no DOI is available from Serper.

**Unpaywall enrichment** — `_enrich_with_unpaywall(results) -> list[AcademicResult]`
- For results that have a DOI but no `pdf_url`, batch-query Unpaywall
- Endpoint: `GET https://api.unpaywall.org/v2/{doi}?email={settings.UNPAYWALL_EMAIL}`
- DOI goes in the URL path (not query param)
- Runs in parallel with `asyncio.gather` for all DOI-bearing results missing PDFs
- Response: check `best_oa_location` object. PDF URL extraction order:
  1. `best_oa_location.url_for_pdf` — direct PDF (can be `null` even when location exists)
  2. `best_oa_location.url` — always populated when location exists, often a PDF anyway
  3. Skip if `best_oa_location` is `null` (paper is closed access)
- Error handling:
  - HTTP 404 returns **HTML not JSON** — catch and skip
  - HTTP 422 means bad/missing email — log warning
  - Any other error: silently skip (enrichment is best-effort)
- Email must be a real address (rejects generic like `test@example.com`)
- Rate limit: 100K/day, no per-second limit documented

### 1.3 Main Function

```python
async def academic_search(
    query: str,
    max_results: int = 10,
    options: dict | None = None,
) -> list[AcademicResult]:
```

- No `academic_credentials` parameter — uses server keys from `settings`
- Fires OpenAlex and Serper Scholar **in parallel** via `asyncio.gather`
- If one provider errors, returns results from the other (no total failure)
- Deduplicates merged results by DOI (preferred, but Serper Scholar does not return DOIs) or normalized title match (primary dedup method for cross-provider results)
- Reranks using existing `score_academic_result` logic (ported into this module)
- Enriches top results with Unpaywall PDF URLs
- Returns up to `max_results`

### 1.4 Ported Utilities

Move these functions from `search_service.py` into `academic_search.py`:
- `reconstruct_abstract()` — needed by OpenAlex adapter
- `_normalize_title()` — needed by deduplication
- `_metadata_richness()` — needed by deduplication
- `deduplicate_academic_results()` — adapted to work with `AcademicResult`
- `score_academic_result()` and helpers (`_tokenize_academic_text`, `_academic_recency_score`, etc.) — needed by reranking
- `rerank_academic_results()` — adapted for `AcademicResult`
- `select_academic_results_for_discovery()` — adapted for `AcademicResult`
- `rank_for_deep_reading()` — adapted for `AcademicResult`

### 1.5 Rate Limits — No Throttling Needed

- OpenAlex: **1,000 search calls/day** free with API key (10K for filter/list calls, but `search=` costs more credits). Generous for our use case. No per-second limit documented.
- Serper: 300 QPS. 1 credit per 10 results, 2 credits for 20-100 results.
- Unpaywall: 100K/day. No per-second limit.

No locks, no retry loops, no backoff. If a request fails, it fails.

### 1.6 Serper Scholar `publicationInfo` Parsing

Since Serper Scholar mashes authors, venue, year, and domain into a single string, the adapter must parse it:

```
"A Vaswani, N Shazeer, N Parmar... - Advances in neural ..., 2017 - proceedings.neurips.cc"
 ^--- authors ---^                   ^--- venue ---^  ^year^   ^--- domain ---^
```

Strategy: split on ` - ` (space-dash-space). First segment = authors (split by `, `). Middle segments = venue. Last segment = domain (discard). Year comes from the top-level `year` field when available, else parse from venue segment. This parsing is best-effort — if it fails, store the full string as a single author entry and leave venue empty.

## 2. Configuration Changes

### 2.1 `backend/app/config.py` — Settings

Add three new fields:
```python
OPENALEX_API_KEY: str = ""
SERPER_API_KEY: str = ""
UNPAYWALL_EMAIL: str = ""
```

No startup validation for these — academic search gracefully degrades if keys are missing (logs a warning, returns empty results).

### 2.2 K8s Secrets

Add to `app-secrets`:
```
openalex-api-key: <key>
serper-api-key: <key>
unpaywall-email: <email>
```

### 2.3 K8s Deployments

Add env vars to both `backend-api.yaml` and `backend-worker.yaml`:
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

## 3. Caller Migration

Every caller currently passes `academic_credentials: dict[str, dict]` through the chain. This parameter gets removed everywhere.

### 3.1 `backend/app/agent_service.py`

- `run_discover_and_plan()` — remove `academic_credentials` param. Call `academic_search.academic_search(query, ...)` directly (no credentials).
- `research_section()` — same change.
- `_run_content_pipeline()` — same change.
- All internal helper calls that thread `academic_credentials` through — remove the param.
- The `academic_options` dict (year_range, min_citations, open_access_only, enabled) is still needed — pass it as `options` to the new `academic_search()`.
- The `enabled` flag check stays: if academic search is not enabled for a course, skip it.

### 3.2 `backend/app/routers/courses.py`

- Remove `_get_user_academic_credentials()` function entirely.
- Course creation: stop building `academic_creds` from key_cache. The `academic_search` options dict (year_range, etc.) still flows through `course.academic_search` column for pipeline jobs.
- SSE discovery path: remove `academic_credentials` from the call chain.

### 3.3 `backend/app/pipeline.py`

- `run_discover_and_plan_step()` — remove `academic_credentials` param.
- `run_research_section_step()` — remove `academic_credentials` param.
- `run_pipeline()` — remove `academic_credentials` from internal calls.

### 3.4 `backend/app/worker.py`

- `_resolve_credentials()` — stop decrypting academic credentials.
- Job execution: remove `academic_credentials` from the call to pipeline functions.
- The `academic_search` key in `job.config` still carries the options (year_range, etc.) — that stays.

### 3.5 `backend/app/paper_reader.py`

- If it imports `rank_for_deep_reading` from `search_service`, update import to `academic_search`.
- If it receives `SearchResult` objects, update to accept `AcademicResult`.

## 4. Code Removal

### 4.1 From `search_service.py` — Remove

- `ACADEMIC_SEARCH_PROVIDERS` registry dict
- `get_academic_search_provider_registry()`
- `_search_semantic_scholar()` and all S2 rate-limit machinery (`_s2_locks`, `_s2_last_request`, `_year_range_to_s2_param`)
- `_search_arxiv()` and arXiv rate-limit machinery (`_arxiv_lock`, `_arxiv_last_request`, `_year_range_to_arxiv_date_filter`)
- `_search_openalex()` and `_year_range_to_openalex_filter` (moved to new module)
- `_ACADEMIC_ADAPTERS`, `_get_academic_adapter()`, `_should_run_academic_provider()`, `get_active_academic_provider_names()`
- `academic_search()` function
- `deduplicate_academic_results()`, `rerank_academic_results()`, `select_academic_results_for_discovery()`, `rank_for_deep_reading()`, `score_academic_result()` and all helper functions (moved to new module)
- `reconstruct_abstract()`, `_normalize_title()`, `_metadata_richness()` (moved to new module)
- Academic stopwords, signal terms, biomedical terms, security venue terms (moved to new module)
- The `SearchResult` dataclass keeps `is_academic` field for now but the academic-specific fields (`authors`, `year`, etc.) can stay since web search results don't populate them — no harm.

### 4.2 `backend/app/routers/academic_provider_routes.py` — Delete Entire File

The per-user academic provider CRUD routes are no longer needed.

### 4.3 `backend/app/key_cache.py`

- Remove `get_all_academic_providers()` method.
- Remove any `academic:` prefix handling.

### 4.4 Frontend

- **`frontend/src/app/settings/page.tsx`** — remove the academic providers configuration section.
- **`frontend/src/lib/api.ts`** — remove academic provider API calls (list, save, update, delete, test).
- **`frontend/src/lib/types.ts`** — remove academic provider types if any.
- **`frontend/src/app/page.tsx`** — if academic search toggle/config is on the course creation form, simplify it (keep the enable/disable toggle + options like year range, but remove the "configure your keys" messaging).
- **`frontend/src/app/courses/[id]/generating/page.tsx`** — update status messages (no longer shows individual provider names like "Searching Semantic Scholar...").

### 4.5 Router Registration

- Remove `academic_provider_routes.router` from the FastAPI app's router includes.

## 5. Test Changes

### 5.1 `backend/tests/test_academic_search.py`

Rewrite tests to target `academic_search.academic_search()`:
- Test OpenAlex + Serper Scholar parallel execution
- Test deduplication (same paper from both providers)
- Test graceful degradation (one provider fails, other's results returned)
- Test Unpaywall enrichment (DOI with no pdf_url gets enriched)
- Test reranking logic (ported from existing tests)
- Mock `httpx.AsyncClient` — no real API calls in tests

### 5.2 `backend/tests/test_courses.py`

- Remove `_mock_get_user_academic_credentials`
- Update `test_create_course_passes_academic_search_context` — no longer passes `academic_credentials`

### 5.3 `backend/tests/test_discovery_streaming.py`

- Update mock to target `academic_search.academic_search` instead of `search_service.academic_search`
- Remove `academic_credentials` from mock setup

### 5.4 `backend/tests/test_pdf_export.py`

- `academic_search=None` field on the test course model — unchanged (column still exists)

## 6. Database

- The `courses.academic_search` JSON column stays — it stores options (year_range, min_citations, open_access_only, enabled), not credentials.
- No migration needed. The `ProviderConfig` rows with `academic:*` prefixed providers become orphaned but harmless. Optional: write a data migration to clean them up, but not blocking.

## 7. What Stays Unchanged

- `search_service.py` web search: `SEARCH_PROVIDERS`, per-user key_cache, `search_with_fallback()`, all web adapters (Tavily, Exa, Brave, Serper web, DuckDuckGo)
- `SearchResult` dataclass stays in `search_service.py` for web results
- LLM provider key system — completely untouched
- Course creation flow — `academic_search` options (enabled, year_range, etc.) still work, just no provider key config needed
- The `academic_search` column on the Course model
