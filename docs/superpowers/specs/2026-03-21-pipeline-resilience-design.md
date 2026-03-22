# Pipeline Resilience: Persistent Job Queue & Parallelization

**Date:** 2026-03-21
**Status:** Draft
**Scope:** Backend pipeline orchestration, worker process, frontend status transport

## Problem

The course generation pipeline uses in-memory state (`_jobs` dict, `_active_tasks` set) for tracking pipeline progress. If the server restarts, all in-flight pipelines vanish silently. Users see "generating" forever with no recovery path. Additionally, the verify-write-edit loop processes sections sequentially when most stages could run in parallel, making a 7-section course take ~10 minutes when it could take ~4.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Job queue backend | Postgres (no new dependencies) | Already running; `FOR UPDATE SKIP LOCKED` provides atomic job claiming |
| Queue design | Course-per-row with phase checkpoints | Matches existing `run_pipeline()` structure; simple schema |
| Per-user concurrency | 1 active generation per user | Prevents resource hogging without global caps |
| Interrupted pipelines | Mark stale, show "Resume" button | User controls whether to spend more API calls |
| Verify-Write-Edit parallelism | Verify+Write parallel, Edit sequential | Protects blackboard coherence (shared glossary, concept ownership) |
| Pipeline status transport | SSE (replace 4s polling) | Real-time updates; consistent with existing chat SSE pattern |
| Worker deployment | Separate process | API latency unaffected by pipeline work; scales independently |

## Design

### 1. Job Queue Schema

New table `pipeline_jobs` and Alembic migration:

```sql
CREATE TABLE pipeline_jobs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id     UUID NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    user_id       UUID NOT NULL REFERENCES users(id),
    status        TEXT NOT NULL DEFAULT 'pending',
    checkpoint    INT NOT NULL DEFAULT 0,
    config        JSONB NOT NULL,
    worker_id     TEXT,
    heartbeat_at  TIMESTAMP WITH TIME ZONE,
    started_at    TIMESTAMP WITH TIME ZONE,
    completed_at  TIMESTAMP WITH TIME ZONE,
    error         TEXT,
    attempts      INT NOT NULL DEFAULT 0,
    max_attempts  INT NOT NULL DEFAULT 2,
    created_at    TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

-- Atomic job claiming: only one pending/running job per user
CREATE UNIQUE INDEX uq_one_active_job_per_user
    ON pipeline_jobs (user_id)
    WHERE status IN ('pending', 'claimed', 'running');

-- Worker claims next job
CREATE INDEX idx_pipeline_jobs_claimable
    ON pipeline_jobs (created_at)
    WHERE status = 'pending';

-- Stale detection
CREATE INDEX idx_pipeline_jobs_stale
    ON pipeline_jobs (heartbeat_at)
    WHERE status = 'running';
```

**`user_id` type:** `UUID REFERENCES users(id)`. The `User.id` column is UUID. The API layer casts the string user_id from the JWT `sub` claim to `uuid.UUID` before inserting the pipeline_jobs row, matching the existing pattern used by `ProviderConfig.user_id`. This differs from `Course.user_id` and `ChatMessage.user_id` which are loose `Text` fields — those are a pre-existing inconsistency, not a pattern to follow.

**`config` column (JSONB):** Stores the pipeline configuration captured at job creation time, so the worker is fully self-contained:

```json
{
    "provider": "openrouter",
    "model": "anthropic/claude-sonnet-4",
    "extra_fields": {"model": "anthropic/claude-sonnet-4"},
    "search_provider": "tavily",
    "search_credentials_ref": "provider_config:<uuid>"
}
```

Credentials are **not** stored in this column. Instead, `search_credentials_ref` and the provider reference point to the `ProviderConfig` row. The worker decrypts credentials at runtime using the same `crypto.py` infrastructure (HMAC-SHA256 key derivation from `ENCRYPTION_PEPPER` + `UserKeySalt`). The worker has access to the same env vars and database as the API server.

**Status lifecycle:**
```
pending → claimed → running → completed
                           → completed_partial (some sections failed)
                           → failed
                           → cancelled (user-initiated)
                           → stale (heartbeat timeout)
```

`completed_partial` courses are **not** resumable in this design. Resumability applies only to `stale` (interrupted) pipelines. Re-running failed sections within a completed_partial course is a separate future feature.

**Checkpoint values** (integer, ordinal-safe):
```
0 = queued
1 = planning complete
2 = research complete
3 = verify-write complete
4 = editing complete
5 = done
```

Using integers avoids the lexicographic comparison bug that string checkpoints would introduce (e.g., `'queued' > 'planning'` in Python).

### 2. Worker Process

New file: `backend/app/worker.py`

Runs as a standalone process: `python -m app.worker`

**Main loop:**
```
while not shutting_down:
    job = claim_next_job()      # SELECT ... FOR UPDATE SKIP LOCKED
    if job is None:
        await asyncio.sleep(2)  # no work available
        continue

    start heartbeat task        # updates heartbeat_at every 30s
    run_pipeline(job)           # existing pipeline logic with checkpoints
    stop heartbeat task
```

**Job claiming query:**
```sql
UPDATE pipeline_jobs
SET status = 'claimed', worker_id = :worker_id, started_at = now(),
    heartbeat_at = now(), attempts = attempts + 1
WHERE id = (
    SELECT id FROM pipeline_jobs
    WHERE status = 'pending' AND attempts < max_attempts
    ORDER BY created_at
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
RETURNING *;
```

**Credential access:** The worker reads `config.provider` and `config.search_provider` from the job row, then loads the corresponding `ProviderConfig` + `UserKeySalt` from the database, and decrypts credentials using `crypto.decrypt_value()`. This uses the same `ENCRYPTION_PEPPER` environment variable that the API server uses. The worker process must have the same env vars as the API server.

**Heartbeat:** A background asyncio task updates `heartbeat_at` every 30 seconds while a job is running. This depends on cooperative multitasking — all pipeline phases must yield to the event loop via `await`. The existing `_invoke_agent()` uses `await agent.ainvoke()` (async) with a fallback to `asyncio.to_thread()` (which also yields). Both are safe. If any future code adds a blocking call that holds the event loop for >2 minutes, the heartbeat would stall and the job would be falsely marked stale.

**Stale recovery:** On worker startup (and every 60 seconds), scan for jobs where `status = 'running'` and `heartbeat_at < now() - interval '2 minutes'`. Mark these as `stale`. Update the corresponding course status to `stale`.

**Graceful shutdown:** The worker catches `SIGTERM` and `SIGINT`. On signal:
1. Set `shutting_down = True` (stops claiming new jobs)
2. If a job is in progress, let it continue to the next checkpoint boundary
3. At the next checkpoint, mark the job back to `pending` (so another worker can claim it)
4. Stop the heartbeat task and exit

This means deployments don't create stale jobs — the in-progress job gets re-queued cleanly.

**Resume flow:**
1. User clicks "Resume" on a stale course
2. Resume endpoint explicitly marks the old stale job's status to `failed` (prevents unique index violation if the stale scan hasn't run yet)
3. Resume endpoint re-fetches and validates that the user still has a valid `ProviderConfig` for the required provider and search provider. If the provider config was deleted or changed since the original run, returns 400 with a descriptive error
4. API creates a new `pipeline_jobs` row with `checkpoint` copied from the stale job, and a fresh `config` with current provider settings
5. Worker claims it and reads `checkpoint` to skip completed phases
6. Old job row is kept for audit (not deleted)

### 3. Pipeline Changes

**Modified file:** `backend/app/pipeline.py`

The `run_pipeline()` function is refactored to:
- Accept a `job_id` and `checkpoint` parameter indicating where to resume from
- Write checkpoint updates to the `pipeline_jobs` row after each phase
- Skip phases that are at or before the checkpoint on resume

**Phase execution with checkpoint awareness:**
```python
CHECKPOINT_PLANNING  = 1
CHECKPOINT_RESEARCHED = 2
CHECKPOINT_WRITING   = 3
CHECKPOINT_EDITING   = 4
CHECKPOINT_DONE      = 5

async def run_pipeline(job_id, course_id, checkpoint, ...):
    if checkpoint < CHECKPOINT_PLANNING:
        await plan_phase(...)
        await update_checkpoint(job_id, CHECKPOINT_PLANNING)

    if checkpoint < CHECKPOINT_RESEARCHED:
        await research_phase(...)           # already parallel via gather
        await update_checkpoint(job_id, CHECKPOINT_RESEARCHED)

    if checkpoint < CHECKPOINT_WRITING:
        await verify_write_phase(...)       # NEW: parallel verify+write
        await update_checkpoint(job_id, CHECKPOINT_WRITING)

    if checkpoint < CHECKPOINT_EDITING:
        await edit_phase(...)               # sequential for blackboard safety
        await update_checkpoint(job_id, CHECKPOINT_EDITING)

    await update_checkpoint(job_id, CHECKPOINT_DONE)
```

**Remove:** `_jobs` dict, `_active_tasks` set, `PipelineStatus` dataclass, `get_pipeline_status()`, `_update_status()`. All replaced by DB queries.

### 4. Verify-Write-Edit Parallelization

**Current flow (sequential):**
```
for section in sections:
    verify(section)
    write(section)
    edit(section)        # updates blackboard
```

**New flow (verify+write parallel, edit sequential):**
```
Phase 1 — Parallel verify+write:
    semaphore = Semaphore(3)    # limit concurrent LLM calls
    for each section (parallel via gather):
        async with semaphore:
            verify(section)
            write(section)

Phase 2 — Sequential edit:
    for each section (sequential):
        edit(section)           # safe blackboard updates, no conflicts
```

**Why semaphore(3):** Limits concurrent LLM calls to avoid OpenRouter rate limits. Configurable per deployment.

**Failed sections:** If verify or write fails for a section (after retries), that section is skipped in the edit phase. The final status is `completed_partial` if any sections failed, matching existing behavior.

**Impact:** A 7-section course goes from ~10 min (all sequential) to ~4-5 min (3 sections verifying+writing at once, then 7 sequential edits).

### 5. Search Query Parallelization

**Discovery searches** (`agent_service.py:discover_topic`):

Current: `for query in queries: await search(query)` — sequential, 3-5 queries.

New:
```python
results = await asyncio.gather(
    *[search_service.search(provider, q, creds, max_results=5)
      for q in queries],
    return_exceptions=True,
)
```

**Section research questions** (`agent_service.py:research_section`):

Current: `for question in brief.questions: await search(question)` — sequential, 3-5 questions per section.

Same fix: `asyncio.gather` over the questions within each section.

**Targeted gap research** (`agent_service.py:research_section_targeted`):

Current: `for gap in gaps: await search(gap)` — sequential, 1-3 gaps. Excluded from parallelization because gap count is typically 1-2, making the overhead of `asyncio.gather` negligible. Can be added later if gap counts increase.

**Impact:** Discovery: ~8s → ~2s. Section research: ~4s/section → ~1s/section. Total per course: ~30-40s saved.

### 6. SSE for Pipeline Status

**New endpoint:** `GET /api/courses/{course_id}/pipeline/stream`

Returns an SSE stream that pushes status updates as the pipeline progresses.

**Authentication:** `EventSource` does not support custom headers. The JWT token is passed as a query parameter: `/api/courses/{course_id}/pipeline/stream?token=<jwt>`. The endpoint validates the token the same way as `get_current_user`, but reads from the query string instead of the Authorization header. The token is not logged.

**Event format:**
```
event: status
data: {"stage": "planning", "section": 0, "total": 0, "checkpoint": 0}

event: status
data: {"stage": "researching", "section": 3, "total": 7, "checkpoint": 1}

event: status
data: {"stage": "writing", "section": 5, "total": 7, "checkpoint": 2}

event: complete
data: {"status": "completed"}

event: complete
data: {"status": "completed_partial", "failed_sections": [3, 5]}

event: stale
data: {"message": "Pipeline interrupted. You can resume."}

event: error
data: {"message": "Pipeline failed", "detail": "..."}
```

**Stage values in SSE events:** `planning`, `researching`, `verifying`, `writing`, `editing`, `complete`. The `planning` stage is included so the frontend can show progress from the very start.

**Implementation:** The endpoint polls the `pipeline_jobs` row every 2 seconds and pushes an event only when the checkpoint, status, or stage changes. This is a lightweight DB read, not an in-memory subscription.

**Why poll-and-push instead of true pub/sub:** Avoids adding Postgres LISTEN/NOTIFY complexity. A 2-second DB poll per active SSE connection is negligible load. Can upgrade to LISTEN/NOTIFY later if needed.

**Frontend:** Replace `setInterval` polling in `PipelineProgress.tsx` with an `EventSource` connection. On `complete` event, navigate to learn page. On `stale` event, show Resume button. On connection drop, fall back to a single fetch (graceful degradation).

**Keep:** `GET /api/courses/{course_id}/status` endpoint as a non-streaming fallback for the SSE connection drop case.

### 7. API Changes

**Modified endpoints:**

| Endpoint | Change |
|---|---|
| `POST /api/courses/{course_id}/generate` | Creates `pipeline_jobs` row with `config` JSONB instead of `asyncio.create_task()`. Casts JWT `sub` to UUID for `user_id`. Returns `{job_id}`. Rejects with 409 if user already has an active job. |
| `GET /api/courses/{course_id}/status` | Reads from `pipeline_jobs` table instead of `_jobs` dict. Kept as non-streaming fallback. |
| `GET /api/courses/{course_id}/pipeline/stream?token=<jwt>` | **New.** SSE stream for real-time pipeline progress. Token via query param. |
| `POST /api/courses/{course_id}/resume` | **New.** Marks old stale job as `failed`, validates user still has valid provider config, creates new job from stale job's checkpoint with fresh config. Returns `{job_id}`. |

**New course status values:** `stale` (interrupted, resumable), `cancelled` (user-initiated stop). Added to the status enum alongside existing values.

### 8. Frontend Changes

**`PipelineProgress.tsx`:**
- Replace `setInterval` + fetch with `EventSource` to `/api/courses/{course_id}/pipeline/stream?token=<jwt>`
- Handle `status`, `complete`, `stale`, and `error` events
- Add `planning` to the STAGES array for the progress display
- On `stale`: show Resume button that calls `POST /resume`
- On connection error: single fetch fallback to `/status`

**`learn/page.tsx`:**
- Replace sequential `await getCourse()` then `await getProgress()` with `Promise.all([getCourse(), getProgress()])`

**`api.ts`:**
- Add `resumeCourse(courseId, token)` function
- Add `getPipelineStreamUrl(courseId, token)` returning the SSE URL with token query param

**`types.ts`:**
- Add `stale` and `cancelled` to course status union type
- Add `PipelineJob` type

### 9. Worker Deployment

**New file:** `backend/app/worker.py` — standalone entry point.

**Run:** `python -m app.worker`

**Makefile targets:**
```makefile
dev-worker:
    cd backend && uv run python -m app.worker

dev:
    $(MAKE) dev-db && $(MAKE) dev-backend & $(MAKE) dev-worker & $(MAKE) dev-frontend
```

The `dev` target is updated to start the worker alongside the backend and frontend.

**Production:** Deploy as a separate service. Connects to the same Postgres database. Must have the same environment variables as the API server (especially `ENCRYPTION_PEPPER` for credential decryption). No shared memory with the API server.

**Scaling:** Run N worker instances. `FOR UPDATE SKIP LOCKED` ensures no two workers claim the same job. Each worker processes one job at a time (claims → completes → claims next).

## Files Changed

| File | Change |
|---|---|
| `backend/app/models.py` | Add `PipelineJob` model, add `stale`/`cancelled` to course status |
| `backend/app/schemas.py` | Add `PipelineJobResponse`, `ResumeRequest` schemas |
| `backend/app/pipeline.py` | Refactor to checkpoint-aware phases, remove in-memory state, parallelize verify+write |
| `backend/app/agent_service.py` | `asyncio.gather` for discovery queries and section research questions |
| `backend/app/worker.py` | **New.** Worker main loop with claiming, heartbeat, stale recovery, graceful shutdown |
| `backend/app/routers/courses.py` | Modify `/generate`, add `/resume`, add `/pipeline/stream` SSE, update `/status` |
| `backend/alembic/versions/xxx_pipeline_jobs.py` | **New.** Migration for `pipeline_jobs` table + indexes |
| `frontend/src/components/PipelineProgress.tsx` | Replace polling with EventSource, add Resume UI, add `planning` stage |
| `frontend/src/app/courses/[id]/learn/page.tsx` | `Promise.all` for parallel data fetching |
| `frontend/src/lib/api.ts` | Add `resumeCourse()`, `getPipelineStreamUrl()` |
| `frontend/src/lib/types.ts` | Add `stale`/`cancelled` to course status type, add `PipelineJob` type |
| `Makefile` | Add `dev-worker` target, update `dev` target |

## Out of Scope

- TanStack Query adoption
- CI/CD setup
- Production Dockerfiles
- CORS configuration
- Form validation library
- Frontend tests
- Pagination
- FK constraints on existing `user_id` text fields
- Postgres LISTEN/NOTIFY (can upgrade SSE polling later)
- Resuming `completed_partial` courses (re-running failed sections only)
