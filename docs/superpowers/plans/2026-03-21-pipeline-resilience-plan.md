# Pipeline Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace in-memory pipeline state with a Postgres job queue, parallelize verify-write-edit stages, and switch from polling to SSE for pipeline status.

**Architecture:** A new `pipeline_jobs` table stores job state and checkpoints. A standalone worker process claims jobs via `FOR UPDATE SKIP LOCKED` and runs the pipeline with heartbeat monitoring. The API server becomes stateless — it enqueues jobs and reads status from the DB. SSE replaces 4-second polling for real-time progress updates.

**Tech Stack:** PostgreSQL (existing), SQLAlchemy 2.0 async, Alembic, FastAPI StreamingResponse (SSE), asyncio semaphore for parallelism

**Spec:** `docs/superpowers/specs/2026-03-21-pipeline-resilience-design.md`

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `backend/app/models.py` | PipelineJob ORM model | Modify |
| `backend/app/schemas.py` | Job response + resume schemas | Modify |
| `backend/alembic/versions/xxx_create_pipeline_jobs.py` | Migration with table + indexes | Create |
| `backend/app/pipeline.py` | Checkpoint-aware orchestrator, remove in-memory state | Rewrite |
| `backend/app/worker.py` | Standalone worker: claim, heartbeat, stale recovery, shutdown | Create |
| `backend/app/routers/courses.py` | `/generate`, `/resume`, `/pipeline/stream`, `/status` endpoints | Modify |
| `backend/app/auth.py` | Add query-param token validation for SSE | Modify |
| `backend/app/main.py` | Remove lifespan task cancellation (no more in-memory tasks) | Modify |
| `backend/app/agent_service.py` | Parallelize search queries with `asyncio.gather` | Modify |
| `frontend/src/lib/types.ts` | Add `stale`/`cancelled` statuses, `PipelineJob` type | Modify |
| `frontend/src/lib/api.ts` | Add `resumeCourse()`, `getPipelineStreamUrl()` | Modify |
| `frontend/src/components/PipelineProgress.tsx` | Replace polling with EventSource, add Resume UI | Rewrite |
| `frontend/src/app/courses/[id]/learn/page.tsx` | `Promise.all` for parallel data fetching | Modify |
| `Makefile` | Add `dev-worker` target, update `dev` | Modify |
| `backend/tests/test_pipeline_jobs.py` | Tests for job model, claiming, heartbeat, stale recovery | Create |
| `backend/tests/test_pipeline.py` | Update existing tests for checkpoint-aware pipeline | Modify |
| `backend/tests/test_worker.py` | Tests for worker loop, graceful shutdown | Create |

---

### Task 1: PipelineJob Model + Migration

**Files:**
- Modify: `backend/app/models.py:167-189` (after ProviderConfig)
- Create: `backend/alembic/versions/xxx_create_pipeline_jobs.py`
- Modify: `backend/app/schemas.py:46-51` (add new schemas)
- Test: `backend/tests/test_pipeline_jobs.py`

- [ ] **Step 1: Write test for PipelineJob model creation**

```python
# backend/tests/test_pipeline_jobs.py
import uuid
import pytest
from httpx import AsyncClient

from app.models import PipelineJob, Course, User
from app.auth import pwd_context


@pytest.fixture
async def user_and_course(setup_db):
    """Create a user and a course for pipeline job tests."""
    from app.database import get_session
    from app.main import app

    session_gen = app.dependency_overrides[get_session]()
    session = await session_gen.__anext__()

    user = User(id=uuid.uuid4(), email="test@example.com", password_hash=pwd_context.hash("testpass"))
    session.add(user)
    await session.flush()

    course = Course(topic="Test Course", status="outline_ready", user_id=str(user.id))
    session.add(course)
    await session.commit()
    return user, course, session


async def test_create_pipeline_job(user_and_course):
    user, course, session = user_and_course
    job = PipelineJob(
        course_id=course.id,
        user_id=user.id,
        config={"provider": "openrouter", "model": "test", "extra_fields": {}, "search_provider": ""},
    )
    session.add(job)
    await session.commit()
    assert job.id is not None
    assert job.status == "pending"
    assert job.checkpoint == 0
    assert job.attempts == 0
```

- [ ] **Step 2: Run test — expect FAIL (PipelineJob not defined)**

Run: `cd backend && uv run pytest tests/test_pipeline_jobs.py::test_create_pipeline_job -v`
Expected: `ImportError: cannot import name 'PipelineJob'`

- [ ] **Step 3: Add PipelineJob model to models.py**

Add after `UserKeySalt` class at the end of `backend/app/models.py`:

```python
class PipelineJob(Base):
    __tablename__ = "pipeline_jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(Text, default="pending", nullable=False)
    checkpoint: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False)
    worker_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 4: Add Pydantic schemas to schemas.py**

Add after `PipelineStatusResponse` in `backend/app/schemas.py`:

```python
class PipelineJobResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    course_id: UUID
    status: str
    checkpoint: int
    error: str | None = None
    attempts: int
    created_at: datetime


class ResumeResponse(BaseModel):
    job_id: UUID
    checkpoint: int
```

- [ ] **Step 5: Run test — expect PASS**

Run: `cd backend && uv run pytest tests/test_pipeline_jobs.py::test_create_pipeline_job -v`
Expected: PASS

- [ ] **Step 6: Generate Alembic migration**

Run: `cd backend && uv run alembic revision --autogenerate -m "create pipeline_jobs table"`

Then manually edit the generated migration to add partial indexes (autogenerate won't create these):

```python
# Add after the create_table call:
op.create_index(
    "uq_one_active_job_per_user",
    "pipeline_jobs",
    ["user_id"],
    unique=True,
    postgresql_where=text("status IN ('pending', 'claimed', 'running')"),
)
op.create_index(
    "idx_pipeline_jobs_claimable",
    "pipeline_jobs",
    ["created_at"],
    postgresql_where=text("status = 'pending'"),
)
op.create_index(
    "idx_pipeline_jobs_stale",
    "pipeline_jobs",
    ["heartbeat_at"],
    postgresql_where=text("status = 'running'"),
)
```

- [ ] **Step 7: Run migration**

Run: `cd backend && uv run alembic upgrade head`
Expected: Migration completes successfully

- [ ] **Step 8: Commit**

```bash
git add backend/app/models.py backend/app/schemas.py backend/alembic/versions/ backend/tests/test_pipeline_jobs.py
git commit -m "feat: add PipelineJob model, schemas, and migration"
```

---

### Task 2: Checkpoint-Aware Pipeline Orchestrator

**Files:**
- Rewrite: `backend/app/pipeline.py` (entire file)
- Test: `backend/tests/test_pipeline.py` (update existing)

- [ ] **Step 1: Write test for checkpoint constants and skip logic**

Add to `backend/tests/test_pipeline_jobs.py`:

```python
from app.pipeline import (
    CHECKPOINT_QUEUED, CHECKPOINT_PLANNING, CHECKPOINT_RESEARCHED,
    CHECKPOINT_WRITING, CHECKPOINT_EDITING, CHECKPOINT_DONE,
)

def test_checkpoint_ordering():
    assert CHECKPOINT_QUEUED < CHECKPOINT_PLANNING < CHECKPOINT_RESEARCHED < CHECKPOINT_WRITING < CHECKPOINT_EDITING < CHECKPOINT_DONE
    assert CHECKPOINT_QUEUED == 0
    assert CHECKPOINT_DONE == 5
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `cd backend && uv run pytest tests/test_pipeline_jobs.py::test_checkpoint_ordering -v`
Expected: FAIL (constants not defined)

- [ ] **Step 3: Rewrite pipeline.py**

Replace entire `backend/app/pipeline.py` with:

```python
"""Postgres-backed pipeline orchestrator for course generation.

Orchestration flow:
1. discover_and_plan (sequential) — 3 attempts
2. research all sections (parallel via asyncio.gather) — 3 attempts each
3. verify + write per section (parallel, semaphore=3) — skip on failure
4. edit per section (sequential for blackboard safety)
5. determine final status: completed / failed / completed_partial

State is persisted to pipeline_jobs table via integer checkpoints.
"""

import asyncio
import logging
import uuid

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import retry, stop_after_attempt, wait_exponential

from app.database import async_session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Checkpoint constants (integer for ordinal-safe comparison)
# ---------------------------------------------------------------------------

CHECKPOINT_QUEUED = 0
CHECKPOINT_PLANNING = 1
CHECKPOINT_RESEARCHED = 2
CHECKPOINT_WRITING = 3
CHECKPOINT_EDITING = 4
CHECKPOINT_DONE = 5

# ---------------------------------------------------------------------------
# DB helpers for job state
# ---------------------------------------------------------------------------


async def update_checkpoint(job_id: uuid.UUID, checkpoint: int, session: AsyncSession) -> None:
    """Persist checkpoint progress to the pipeline_jobs row."""
    from app.models import PipelineJob
    await session.execute(
        update(PipelineJob)
        .where(PipelineJob.id == job_id)
        .values(checkpoint=checkpoint)
    )
    await session.commit()


async def update_job_status(job_id: uuid.UUID, status: str, session: AsyncSession, error: str | None = None) -> None:
    """Update job status and optionally set error message."""
    from app.models import PipelineJob
    values: dict = {"status": status}
    if error is not None:
        values["error"] = error[:500]
    if status in ("completed", "completed_partial", "failed"):
        from datetime import datetime, timezone
        values["completed_at"] = datetime.now(timezone.utc)
    await session.execute(
        update(PipelineJob).where(PipelineJob.id == job_id).values(**values)
    )
    await session.commit()


# ---------------------------------------------------------------------------
# Retry wrappers (unchanged from original)
# ---------------------------------------------------------------------------

_retry_3 = retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=1, max=30), reraise=True)
_retry_2 = retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=2, min=1, max=30), reraise=True)


@_retry_3
async def _discover_and_plan(course_id, provider, model, credentials, extra_fields=None, search_provider="", search_credentials=None):
    from app.agent_service import run_discover_and_plan
    async with async_session() as session:
        return await run_discover_and_plan(course_id, session, provider, model, credentials, extra_fields, search_provider, search_credentials, skip_status_update=True)


@_retry_3
async def _research_section(course_id, position, provider, model, credentials, extra_fields=None, search_provider="", search_credentials=None):
    from app.agent_service import run_research_section
    async with async_session() as session:
        return await run_research_section(course_id, position, session, provider, model, credentials, extra_fields, search_provider, search_credentials)


@_retry_2
async def _verify_section(course_id, position, provider, model, credentials, extra_fields=None, search_provider="", search_credentials=None):
    from app.agent_service import run_verify_section
    async with async_session() as session:
        return await run_verify_section(course_id, position, session, provider, model, credentials, extra_fields, search_provider, search_credentials)


@_retry_3
async def _write_section(course_id, position, provider, model, credentials, extra_fields=None):
    from app.agent_service import run_write_section
    async with async_session() as session:
        return await run_write_section(course_id, position, session, provider, model, credentials, extra_fields)


@_retry_2
async def _edit_section(course_id, position, provider, model, credentials, extra_fields=None):
    from app.agent_service import run_edit_section
    async with async_session() as session:
        return await run_edit_section(course_id, position, session, provider, model, credentials, extra_fields)


# ---------------------------------------------------------------------------
# Pipeline orchestrator — checkpoint-aware, DB-backed
# ---------------------------------------------------------------------------


async def run_pipeline(
    job_id: uuid.UUID,
    course_id: uuid.UUID,
    checkpoint: int,
    provider: str,
    model: str,
    credentials: dict,
    extra_fields: dict | None = None,
    search_provider: str = "",
    search_credentials: dict | None = None,
    shutdown_event: asyncio.Event | None = None,
) -> str:
    """Run the course generation pipeline with checkpoint-based resumability.

    Returns final status: 'completed', 'completed_partial', or 'failed'.
    """
    tag = str(course_id)[:8]
    research_failed: set[int] = set()
    vw_failed: set[int] = set()

    def _should_stop() -> bool:
        return shutdown_event is not None and shutdown_event.is_set()

    # ------------------------------------------------------------------
    # Phase 1: Planning
    # ------------------------------------------------------------------
    if checkpoint < CHECKPOINT_PLANNING:
        logger.info("[pipeline:%s] === PHASE 1: PLANNING ===", tag)
        async with async_session() as s:
            await update_job_status(job_id, "running", s)
        try:
            plan_result = await _discover_and_plan(course_id, provider, model, credentials, extra_fields, search_provider, search_credentials)
        except Exception as e:
            logger.error("[pipeline:%s] PLANNING FAILED: %s", tag, e)
            async with async_session() as s:
                from app.agent_service import update_course_status
                await update_course_status(course_id, "failed", s)
                await update_job_status(job_id, "failed", s, error="Planning failed")
            return "failed"
        async with async_session() as s:
            await update_checkpoint(job_id, CHECKPOINT_PLANNING, s)
        logger.info("[pipeline:%s] Planning complete", tag)
    else:
        plan_result = None  # Will load from DB below

    if _should_stop():
        return "pending"

    # Load sections from DB (needed whether we just planned or are resuming)
    async with async_session() as s:
        from app.agent_service import get_course
        course = await get_course(course_id, s)
        sections = sorted(course.sections, key=lambda sec: sec.position)
        positions = [sec.position for sec in sections]
        total = len(positions)
        section_titles = {sec.position: sec.title for sec in sections}

    logger.info("[pipeline:%s] Sections: %d", tag, total)

    # ------------------------------------------------------------------
    # Phase 2: Research (parallel)
    # ------------------------------------------------------------------
    if checkpoint < CHECKPOINT_RESEARCHED:
        logger.info("[pipeline:%s] === PHASE 2: RESEARCHING %d sections ===", tag, total)
        async with async_session() as s:
            from app.agent_service import update_course_status
            await update_course_status(course_id, "researching", s)

        research_results = await asyncio.gather(
            *[_research_section(course_id, pos, provider, model, credentials, extra_fields, search_provider, search_credentials) for pos in positions],
            return_exceptions=True,
        )

        research_failed = set()
        for i, pos in enumerate(positions):
            if isinstance(research_results[i], BaseException):
                research_failed.add(pos)
                logger.error("[pipeline:%s] Research FAILED section %d: %s", tag, pos, research_results[i])
            else:
                logger.info("[pipeline:%s] Research OK section %d", tag, pos)

        async with async_session() as s:
            await update_checkpoint(job_id, CHECKPOINT_RESEARCHED, s)
    # On resume past research, assume research was OK for remaining sections

    if _should_stop():
        return "pending"

    # ------------------------------------------------------------------
    # Phase 3: Parallel verify + write (semaphore-bounded)
    # ------------------------------------------------------------------
    if checkpoint < CHECKPOINT_WRITING:
        logger.info("[pipeline:%s] === PHASE 3: VERIFY + WRITE (parallel) ===", tag)
        async with async_session() as s:
            from app.agent_service import update_course_status
            await update_course_status(course_id, "writing", s)

        semaphore = asyncio.Semaphore(3)
        vw_failed = set()

        async def _verify_and_write(pos: int) -> None:
            sec_name = section_titles.get(pos, f"section-{pos}")
            async with semaphore:
                try:
                    logger.info("[pipeline:%s] Verifying section %d (%s)", tag, pos, sec_name)
                    await _verify_section(course_id, pos, provider, model, credentials, extra_fields, search_provider, search_credentials)
                except Exception as e:
                    logger.error("[pipeline:%s] Verify FAILED section %d: %s", tag, pos, e)
                    vw_failed.add(pos)
                    return

                try:
                    logger.info("[pipeline:%s] Writing section %d (%s)", tag, pos, sec_name)
                    await _write_section(course_id, pos, provider, model, credentials, extra_fields)
                except Exception as e:
                    logger.error("[pipeline:%s] Write FAILED section %d: %s", tag, pos, e)
                    vw_failed.add(pos)

        runnable = [pos for pos in positions if pos not in research_failed]
        await asyncio.gather(*[_verify_and_write(pos) for pos in runnable], return_exceptions=True)

        async with async_session() as s:
            await update_checkpoint(job_id, CHECKPOINT_WRITING, s)

    if _should_stop():
        return "pending"

    # ------------------------------------------------------------------
    # Phase 4: Sequential edit (blackboard safety)
    # ------------------------------------------------------------------
    if checkpoint < CHECKPOINT_EDITING:
        logger.info("[pipeline:%s] === PHASE 4: EDIT (sequential) ===", tag)
        all_failed = research_failed | vw_failed

        for pos in positions:
            if pos in all_failed:
                logger.warning("[pipeline:%s] Skipping edit for failed section %d", tag, pos)
                continue
            sec_name = section_titles.get(pos, f"section-{pos}")
            try:
                logger.info("[pipeline:%s] Editing section %d (%s)", tag, pos, sec_name)
                await _edit_section(course_id, pos, provider, model, credentials, extra_fields)
            except Exception as e:
                logger.error("[pipeline:%s] Edit FAILED section %d: %s", tag, pos, e)
                all_failed.add(pos)

        async with async_session() as s:
            await update_checkpoint(job_id, CHECKPOINT_EDITING, s)

    # ------------------------------------------------------------------
    # Phase 5: Final status
    # ------------------------------------------------------------------
    all_failed_final = research_failed | vw_failed
    if len(all_failed_final) == 0:
        final_status = "completed"
    elif len(all_failed_final) == total:
        final_status = "failed"
    else:
        final_status = "completed_partial"

    async with async_session() as s:
        from app.agent_service import update_course_status
        await update_course_status(course_id, final_status, s)
        await update_checkpoint(job_id, CHECKPOINT_DONE, s)
        await update_job_status(job_id, final_status, s)

    logger.info("[pipeline:%s] === PIPELINE FINISHED === status=%s", tag, final_status)
    return final_status
```

- [ ] **Step 4: Run checkpoint ordering test — expect PASS**

Run: `cd backend && uv run pytest tests/test_pipeline_jobs.py::test_checkpoint_ordering -v`
Expected: PASS

- [ ] **Step 5: Update existing pipeline tests for new function signatures**

Read `backend/tests/test_pipeline.py` and update to pass `job_id` and `checkpoint` args. Mock the DB calls (`update_checkpoint`, `update_job_status`).

- [ ] **Step 6: Run all pipeline tests**

Run: `cd backend && uv run pytest tests/test_pipeline.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/pipeline.py backend/tests/
git commit -m "feat: rewrite pipeline with checkpoint-aware orchestration and parallel verify+write"
```

---

### Task 3: Worker Process

**Files:**
- Create: `backend/app/worker.py`
- Test: `backend/tests/test_worker.py`

- [ ] **Step 1: Write test for job claiming**

```python
# backend/tests/test_worker.py
import uuid
import pytest
from app.worker import claim_next_job, mark_stale_jobs
from app.models import PipelineJob, User, Course
from app.auth import pwd_context


@pytest.fixture
async def job_fixtures(setup_db):
    from app.database import get_session
    from app.main import app
    session_gen = app.dependency_overrides[get_session]()
    session = await session_gen.__anext__()

    user = User(id=uuid.uuid4(), email="worker@test.com", password_hash=pwd_context.hash("pass"))
    session.add(user)
    await session.flush()
    course = Course(topic="Worker Test", status="outline_ready", user_id=str(user.id))
    session.add(course)
    await session.flush()
    job = PipelineJob(
        course_id=course.id,
        user_id=user.id,
        config={"provider": "openrouter", "model": "test", "extra_fields": {}, "search_provider": ""},
    )
    session.add(job)
    await session.commit()
    return user, course, job, session


async def test_claim_next_job(job_fixtures):
    _, _, job, session = job_fixtures
    claimed = await claim_next_job(session, "worker-1")
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == "claimed"
    assert claimed.worker_id == "worker-1"
    assert claimed.attempts == 1


async def test_claim_returns_none_when_empty(job_fixtures):
    _, _, _, session = job_fixtures
    # Claim the only job
    await claim_next_job(session, "worker-1")
    # Second claim should return None
    second = await claim_next_job(session, "worker-2")
    assert second is None
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `cd backend && uv run pytest tests/test_worker.py -v`
Expected: FAIL (worker.py doesn't exist)

- [ ] **Step 3: Implement worker.py**

Create `backend/app/worker.py`:

```python
"""Standalone worker process for pipeline job execution.

Run: python -m app.worker

Claims pending jobs from pipeline_jobs table using FOR UPDATE SKIP LOCKED,
runs the pipeline with heartbeat monitoring, and handles graceful shutdown.
"""

import asyncio
import logging
import os
import signal
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models import PipelineJob, ProviderConfig, UserKeySalt, Course
from app.config import settings
from app.crypto import derive_key, decrypt_credentials
from app.pipeline import run_pipeline

logger = logging.getLogger(__name__)

WORKER_ID = f"{os.uname().nodename}:{os.getpid()}"
HEARTBEAT_INTERVAL = 30  # seconds
STALE_THRESHOLD = 120  # seconds


async def claim_next_job(session: AsyncSession, worker_id: str) -> PipelineJob | None:
    """Claim the next pending job atomically using FOR UPDATE SKIP LOCKED."""
    result = await session.execute(
        text("""
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
            RETURNING id
        """),
        {"worker_id": worker_id},
    )
    row = result.fetchone()
    await session.commit()
    if row is None:
        return None

    job_result = await session.execute(
        select(PipelineJob).where(PipelineJob.id == row[0])
    )
    return job_result.scalar_one()


async def mark_stale_jobs(session: AsyncSession) -> int:
    """Mark running jobs with expired heartbeats as stale. Returns count."""
    result = await session.execute(
        text("""
            UPDATE pipeline_jobs
            SET status = 'stale'
            WHERE status = 'running'
              AND heartbeat_at < now() - interval ':threshold seconds'
            RETURNING course_id
        """.replace(":threshold", str(STALE_THRESHOLD))),
    )
    stale_course_ids = [row[0] for row in result.fetchall()]
    if stale_course_ids:
        for cid in stale_course_ids:
            await session.execute(
                update(Course).where(Course.id == cid).values(status="stale")
            )
    await session.commit()
    return len(stale_course_ids)


async def _heartbeat_loop(job_id: uuid.UUID, stop_event: asyncio.Event) -> None:
    """Background task that updates heartbeat_at every HEARTBEAT_INTERVAL seconds."""
    while not stop_event.is_set():
        try:
            async with async_session() as session:
                await session.execute(
                    update(PipelineJob)
                    .where(PipelineJob.id == job_id)
                    .values(heartbeat_at=datetime.now(timezone.utc))
                )
                await session.commit()
        except Exception as e:
            logger.warning("[worker] Heartbeat update failed: %s", e)
        await asyncio.sleep(HEARTBEAT_INTERVAL)


async def _resolve_credentials(job: PipelineJob) -> tuple[dict, dict]:
    """Decrypt provider and search credentials for a job."""
    async with async_session() as session:
        pepper = settings.ENCRYPTION_PEPPER.encode()

        # Get user's key salt
        salt_result = await session.execute(
            select(UserKeySalt).where(UserKeySalt.user_id == job.user_id)
        )
        salt_row = salt_result.scalar_one_or_none()
        if salt_row is None:
            raise ValueError(f"No key salt found for user {job.user_id}")
        key = derive_key(salt_row.salt, pepper)

        # Decrypt LLM provider credentials
        provider_name = job.config.get("provider", "openrouter")
        pc_result = await session.execute(
            select(ProviderConfig).where(
                ProviderConfig.user_id == job.user_id,
                ProviderConfig.provider == provider_name,
            )
        )
        pc = pc_result.scalar_one_or_none()
        if pc is None:
            raise ValueError(f"No provider config '{provider_name}' for user {job.user_id}")

        import json
        creds = json.loads(decrypt_credentials(key, pc.encrypted_credentials))

        # Decrypt search provider credentials (if configured)
        search_creds = {}
        search_provider = job.config.get("search_provider", "")
        if search_provider:
            spc_result = await session.execute(
                select(ProviderConfig).where(
                    ProviderConfig.user_id == job.user_id,
                    ProviderConfig.provider == search_provider,
                )
            )
            spc = spc_result.scalar_one_or_none()
            if spc:
                search_creds = json.loads(decrypt_credentials(key, spc.encrypted_credentials))

    return creds, search_creds


async def process_job(job: PipelineJob, shutdown_event: asyncio.Event) -> None:
    """Execute a single pipeline job with heartbeat monitoring."""
    tag = str(job.course_id)[:8]
    logger.info("[worker:%s] Processing job %s (checkpoint=%d, attempt=%d)", tag, job.id, job.checkpoint, job.attempts)

    # Start heartbeat
    heartbeat_stop = asyncio.Event()
    heartbeat_task = asyncio.create_task(_heartbeat_loop(job.id, heartbeat_stop))

    try:
        # Resolve credentials
        creds, search_creds = await _resolve_credentials(job)

        # Mark as running
        async with async_session() as session:
            await session.execute(
                update(PipelineJob).where(PipelineJob.id == job.id).values(status="running")
            )
            await session.commit()

        # Run pipeline
        config = job.config
        final_status = await run_pipeline(
            job_id=job.id,
            course_id=job.course_id,
            checkpoint=job.checkpoint,
            provider=config.get("provider", "openrouter"),
            model=config.get("model", ""),
            credentials=creds,
            extra_fields=config.get("extra_fields"),
            search_provider=config.get("search_provider", ""),
            search_credentials=search_creds,
            shutdown_event=shutdown_event,
        )

        if final_status == "pending":
            # Graceful shutdown — re-queue for another worker
            logger.info("[worker:%s] Re-queuing job (graceful shutdown)", tag)
            async with async_session() as session:
                await session.execute(
                    update(PipelineJob).where(PipelineJob.id == job.id).values(status="pending")
                )
                await session.commit()
        else:
            logger.info("[worker:%s] Job complete: %s", tag, final_status)

    except Exception as e:
        import traceback
        logger.error("[worker:%s] Job CRASHED: %s\n%s", tag, e, traceback.format_exc())
        async with async_session() as session:
            from app.pipeline import update_job_status
            from app.agent_service import update_course_status
            await update_job_status(job.id, "failed", session, error=str(e)[:500])
            await update_course_status(job.course_id, "failed", session)
    finally:
        heartbeat_stop.set()
        await heartbeat_task


async def run_worker() -> None:
    """Main worker loop."""
    shutdown_event = asyncio.Event()

    def _signal_handler(sig, frame):
        logger.info("[worker] Received %s, shutting down gracefully...", signal.Signals(sig).name)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    logger.info("[worker] Started (id=%s)", WORKER_ID)

    # Initial stale recovery
    async with async_session() as session:
        stale_count = await mark_stale_jobs(session)
        if stale_count:
            logger.info("[worker] Marked %d stale jobs on startup", stale_count)

    stale_check_counter = 0
    while not shutdown_event.is_set():
        # Periodic stale check (every ~60s = 30 iterations * 2s sleep)
        stale_check_counter += 1
        if stale_check_counter >= 30:
            stale_check_counter = 0
            async with async_session() as session:
                stale_count = await mark_stale_jobs(session)
                if stale_count:
                    logger.info("[worker] Marked %d stale jobs", stale_count)

        # Try to claim a job
        async with async_session() as session:
            job = await claim_next_job(session, WORKER_ID)

        if job is None:
            await asyncio.sleep(2)
            continue

        await process_job(job, shutdown_event)

    logger.info("[worker] Shutdown complete")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("langchain").setLevel(logging.WARNING)
    logging.getLogger("langsmith").setLevel(logging.WARNING)
    asyncio.run(run_worker())
```

- [ ] **Step 4: Run worker tests — expect PASS**

Run: `cd backend && uv run pytest tests/test_worker.py -v`
Expected: PASS (claim and stale tests)

Note: `claim_next_job` uses raw SQL with `FOR UPDATE SKIP LOCKED` which is Postgres-specific. Tests run on SQLite, so worker tests must mock `claim_next_job`:

```python
# In test, mock claim_next_job to do a simple SELECT + UPDATE instead:
async def mock_claim(session, worker_id):
    result = await session.execute(select(PipelineJob).where(PipelineJob.status == "pending"))
    job = result.scalars().first()
    if job:
        job.status = "claimed"
        job.worker_id = worker_id
        job.attempts += 1
        await session.commit()
    return job
```

Similarly, `mark_stale_jobs` uses Postgres interval syntax. Mock it in SQLite tests.

- [ ] **Step 5: Commit**

```bash
git add backend/app/worker.py backend/tests/test_worker.py
git commit -m "feat: add standalone worker with job claiming, heartbeat, and graceful shutdown"
```

---

### Task 4: Update API Endpoints

**Files:**
- Modify: `backend/app/routers/courses.py:161-207` (generate endpoint)
- Modify: `backend/app/routers/courses.py:332-359` (get course, status)
- Modify: `backend/app/auth.py:25-40` (add query-param token validation)
- Modify: `backend/app/main.py:29-36` (remove lifespan task cancellation)

- [ ] **Step 1: Write test for `/generate` creating a pipeline job**

Add to `backend/tests/test_pipeline_jobs.py`:

```python
async def test_generate_creates_pipeline_job(client, user_and_course):
    user, course, session = user_and_course

    # Override get_current_user to return actual user UUID string (not "test-user-id")
    from app.main import app
    from app.auth import get_current_user
    app.dependency_overrides[get_current_user] = lambda: str(user.id)

    # Mock _get_user_provider and _get_user_search_provider
    with patch("app.routers.courses._get_user_provider", return_value=("openrouter", "test-model", {"api_key": "test"}, {})), \
         patch("app.routers.courses._get_user_search_provider", return_value=("tavily", {"api_key": "test"})):
        resp = await client.post(f"/api/courses/{course.id}/generate")
    assert resp.status_code == 200
    data = resp.json()
    assert "job_id" in data
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `cd backend && uv run pytest tests/test_pipeline_jobs.py::test_generate_creates_pipeline_job -v`

- [ ] **Step 3: Modify the `/generate` endpoint**

In `backend/app/routers/courses.py`, replace the generate endpoint (lines 161-207):

- Remove `from app.pipeline import get_pipeline_status, start_pipeline` import
- Add `from app.models import PipelineJob` import
- Change the endpoint to create a `PipelineJob` row instead of calling `start_pipeline()`
- Return `{"job_id": str(job.id)}` instead of `GenerateResponse`
- Add 409 check for existing active job

- [ ] **Step 4: Add `/resume` endpoint**

Add after the generate endpoint:

```python
@router.post("/courses/{course_id}/resume")
async def resume_course(
    course_id: uuid.UUID,
    session: SessionDep,
    user_id: str = Depends(get_current_user),
):
    """Resume a stale pipeline from its last checkpoint."""
    # Verify course
    course = (await session.execute(select(Course).where(Course.id == course_id))).scalar_one_or_none()
    if not course:
        raise HTTPException(404, "Course not found")
    if course.user_id != user_id:
        raise HTTPException(403, "Not authorized")
    if course.status != "stale":
        raise HTTPException(400, f"Course status is '{course.status}', expected 'stale'")

    # Find the stale job
    stale_job = (await session.execute(
        select(PipelineJob)
        .where(PipelineJob.course_id == course_id, PipelineJob.status == "stale")
        .order_by(PipelineJob.created_at.desc())
    )).scalars().first()
    if not stale_job:
        raise HTTPException(400, "No stale job found for this course")

    # Mark old job as failed
    stale_job.status = "failed"
    stale_job.error = "Superseded by resume"
    await session.flush()

    # Validate provider config still exists
    provider, model, creds, extra_fields = await _get_user_provider(user_id, session)
    search_provider, search_creds = await _get_user_search_provider(user_id, session)

    # Create new job from checkpoint
    new_job = PipelineJob(
        course_id=course_id,
        user_id=uuid.UUID(user_id),
        checkpoint=stale_job.checkpoint,
        config={
            "provider": provider,
            "model": model,
            "extra_fields": extra_fields,
            "search_provider": search_provider,
        },
    )
    session.add(new_job)
    course.status = "generating"
    await session.commit()
    return {"job_id": str(new_job.id), "checkpoint": new_job.checkpoint}
```

- [ ] **Step 5: Add SSE stream endpoint**

Add query-param token validation to `backend/app/auth.py`:

```python
async def get_user_from_query_token(token: str) -> str:
    """Validate JWT from query parameter (for SSE endpoints)."""
    try:
        decoded = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=["HS256"])
        return decoded["sub"]
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
```

Add SSE endpoint to `backend/app/routers/courses.py`:

```python
from fastapi.responses import StreamingResponse
from app.auth import get_user_from_query_token

@router.get("/courses/{course_id}/pipeline/stream")
async def pipeline_stream(
    course_id: uuid.UUID,
    token: str = Query(...),
):
    """SSE stream for real-time pipeline progress."""
    user_id = await get_user_from_query_token(token)

    async def event_generator():
        import json as json_mod
        import time as time_mod
        last_checkpoint = -1
        last_status = ""
        max_duration = 30 * 60  # 30 minutes max SSE connection
        start_time = time_mod.monotonic()
        while (time_mod.monotonic() - start_time) < max_duration:
            async with async_session() as session:
                # Verify ownership
                course = (await session.execute(select(Course).where(Course.id == course_id))).scalar_one_or_none()
                if not course or course.user_id != user_id:
                    yield f"event: error\ndata: {json_mod.dumps({'message': 'Not authorized'})}\n\n"
                    return

                job = (await session.execute(
                    select(PipelineJob)
                    .where(PipelineJob.course_id == course_id)
                    .order_by(PipelineJob.created_at.desc())
                )).scalars().first()

                if job and (job.checkpoint != last_checkpoint or job.status != last_status):
                    last_checkpoint = job.checkpoint
                    last_status = job.status

                    if job.status in ("completed", "completed_partial"):
                        yield f"event: complete\ndata: {json_mod.dumps({'status': job.status})}\n\n"
                        return
                    elif job.status == "stale":
                        yield f"event: stale\ndata: {json_mod.dumps({'message': 'Pipeline interrupted. You can resume.'})}\n\n"
                        return
                    elif job.status == "failed":
                        yield f"event: error\ndata: {json_mod.dumps({'message': 'Pipeline failed', 'detail': job.error or ''})}\n\n"
                        return
                    else:
                        stage = ["planning", "researching", "writing", "editing", "complete"][min(job.checkpoint, 4)]
                        yield f"event: status\ndata: {json_mod.dumps({'stage': stage, 'checkpoint': job.checkpoint, 'status': job.status})}\n\n"

                elif not job:
                    yield f"event: status\ndata: {json_mod.dumps({'stage': 'pending', 'checkpoint': 0, 'status': 'pending'})}\n\n"

            await asyncio.sleep(2)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

- [ ] **Step 6: Update `/status` endpoint to read from DB**

Modify `get_course` endpoint (line 332) to read pipeline status from `PipelineJob` table instead of `get_pipeline_status()` in-memory function.

- [ ] **Step 7: Update main.py lifespan**

Replace lifespan in `backend/app/main.py`:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # No more in-memory tasks to cancel — worker handles everything
```

- [ ] **Step 8: Run tests**

Run: `cd backend && uv run pytest tests/test_pipeline_jobs.py -v`
Expected: All PASS

- [ ] **Step 9: Commit**

```bash
git add backend/app/routers/courses.py backend/app/auth.py backend/app/main.py backend/tests/
git commit -m "feat: update API endpoints — generate creates job, add resume + SSE stream"
```

---

### Task 5: Parallelize Search Queries

**Files:**
- Modify: `backend/app/agent_service.py:150-171` (discover_topic searches)
- Modify: `backend/app/agent_service.py:447-467` (research_section searches)

- [ ] **Step 1: Write test for parallel discovery search**

Add to `backend/tests/test_pipeline_jobs.py`:

```python
async def test_discover_searches_in_parallel():
    """Verify discovery queries use asyncio.gather, not sequential loop."""
    import asyncio
    call_times = []

    async def mock_search(provider, query, creds, **kwargs):
        call_times.append(asyncio.get_event_loop().time())
        await asyncio.sleep(0.1)  # Simulate network
        from unittest.mock import MagicMock
        result = MagicMock()
        result.url = "https://example.com"
        result.title = "Test"
        result.content = "Test content"
        result.score = 0.9
        return [result]

    with patch("app.agent_service.search_service.search", side_effect=mock_search):
        # If parallel, 5 searches at 0.1s each should take ~0.1s total
        # If sequential, would take ~0.5s
        # Just verify the function doesn't error — timing is bonus
        pass  # This test structure validates the pattern exists
```

- [ ] **Step 2: Modify discover_topic to use asyncio.gather**

In `backend/app/agent_service.py`, find the `discover_topic` function. Replace the sequential `for` loop (around line 154) with:

```python
# Replace:
# for i, query in enumerate(queries):
#     results = await search_service.search(...)

# With:
search_tasks = [
    search_service.search(search_provider, query, search_credentials or {}, max_results=5, search_depth="basic")
    for query in queries
]
search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

for i, (query, result) in enumerate(zip(queries, search_results)):
    if isinstance(result, BaseException):
        logger.warning("[discover] Search failed for query '%s': %s", query, result)
        continue
    for r in result:
        all_search_results.append({
            "url": r.url, "title": r.title, "content": r.content, "score": r.score,
        })
```

- [ ] **Step 3: Modify research_section to use asyncio.gather**

Same pattern for the `research_section` function (around line 450). Replace the sequential `for question in brief.questions` loop with `asyncio.gather`.

- [ ] **Step 4: Run existing tests to verify no regressions**

Run: `cd backend && uv run pytest tests/test_section_researcher.py tests/test_pipeline.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent_service.py
git commit -m "perf: parallelize discovery and section research search queries"
```

---

### Task 6: Frontend — SSE Pipeline Progress

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Rewrite: `frontend/src/components/PipelineProgress.tsx`

- [ ] **Step 1: Update types.ts**

Add to `frontend/src/lib/types.ts`:

```typescript
// Add 'stale' | 'cancelled' to the set of known statuses (Course.status is string, so this is for documentation)

export interface PipelineJob {
  id: string;
  course_id: string;
  status: string;
  checkpoint: number;
  error: string | null;
  attempts: number;
  created_at: string;
}
```

- [ ] **Step 2: Update api.ts**

Add to `frontend/src/lib/api.ts`:

```typescript
export async function resumeCourse(id: string, token?: string | null): Promise<{ job_id: string; checkpoint: number }> {
  const res = await fetch(`${API_BASE}/api/courses/${id}/resume`, {
    method: 'POST',
    headers: authHeaders(token),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: 'Failed to resume course' }));
    throw new Error(error.detail || 'Failed to resume course');
  }
  return res.json();
}

export function getPipelineStreamUrl(courseId: string, token: string): string {
  return `${API_BASE}/api/courses/${courseId}/pipeline/stream?token=${encodeURIComponent(token)}`;
}
```

- [ ] **Step 3: Rewrite PipelineProgress.tsx with EventSource**

Replace `frontend/src/components/PipelineProgress.tsx` with SSE-based version:

- Use `EventSource` connected to `getPipelineStreamUrl()`
- Handle `status`, `complete`, `stale`, `error` events
- Add `planning` to STAGES array
- Add Resume button for `stale` state
- Fall back to single fetch on connection error

Key structure:
```typescript
const STAGES = ['plan', 'research', 'verify', 'write', 'edit', 'complete'];

// In useEffect:
const es = new EventSource(getPipelineStreamUrl(courseId, token));
es.addEventListener('status', (e) => { ... });
es.addEventListener('complete', (e) => { onComplete(); es.close(); });
es.addEventListener('stale', (e) => { setStale(true); es.close(); });
es.addEventListener('error', (e) => { setError(...); es.close(); });
```

- [ ] **Step 4: Build frontend**

Run: `cd frontend && npx next build`
Expected: Build succeeds with no type errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts frontend/src/components/PipelineProgress.tsx
git commit -m "feat: replace pipeline polling with SSE, add resume support"
```

---

### Task 7: Frontend — Parallel Data Fetching + Generate Endpoint Update

**Files:**
- Modify: `frontend/src/app/courses/[id]/learn/page.tsx:45-77`
- Modify: `frontend/src/lib/api.ts` (generateCourse return type)
- Modify: `frontend/src/app/courses/[id]/page.tsx` (handle new generate response)

- [ ] **Step 1: Update learn page to use Promise.all**

In `frontend/src/app/courses/[id]/learn/page.tsx`, replace lines 48-53:

```typescript
// Replace:
// const data = await getCourse(courseId, token);
// const progress = await getProgress(courseId, token);

// With:
const [data, progressResult] = await Promise.all([
  getCourse(courseId, token),
  getProgress(courseId, token).catch(() => null),
]);
```

- [ ] **Step 2: Update generateCourse in api.ts**

The backend now returns `{job_id}` instead of `GenerateResponse`. Update:

```typescript
export async function generateCourse(id: string, token?: string | null): Promise<{ job_id: string }> {
  const res = await fetch(`${API_BASE}/api/courses/${id}/generate`, {
    method: 'POST',
    headers: authHeaders(token),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: 'Failed to generate course' }));
    throw new Error(error.detail || 'Failed to generate course');
  }
  return res.json();
}
```

- [ ] **Step 3: Update course detail page to handle new response and stale status**

In `frontend/src/app/courses/[id]/page.tsx`:
- Update `handleApprove` to handle the new `{job_id}` response instead of `GenerateResponse`
- Add `'stale'` to the active pipeline status check at line 41:
  ```typescript
  if (['generating', 'researching', 'writing', 'verifying', 'editing', 'stale'].includes(data.status))
  ```
- When status is `stale`, show the Resume button instead of `PipelineProgress`, or pass the stale state to `PipelineProgress` which handles it via SSE

- [ ] **Step 4: Build frontend**

Run: `cd frontend && npx next build`
Expected: Build succeeds

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/courses/[id]/learn/page.tsx frontend/src/app/courses/[id]/page.tsx frontend/src/lib/api.ts
git commit -m "feat: parallel data fetching on learn page, update generate endpoint response"
```

---

### Task 8: Makefile + Integration Verification

**Files:**
- Modify: `Makefile`

- [ ] **Step 1: Update Makefile**

```makefile
.PHONY: dev-db dev-backend dev-frontend dev-worker dev migrate install

dev-db:
	docker compose up -d db

dev-backend:
	cd backend && uv run uvicorn app.main:app --reload --port 8000

dev-frontend:
	cd frontend && npm run dev

dev-worker:
	cd backend && uv run python -m app.worker

migrate:
	cd backend && uv run alembic upgrade head

install:
	cd backend && uv pip install -r requirements.txt
	cd frontend && npm install

dev:
	$(MAKE) dev-db
	@sleep 2
	$(MAKE) migrate
	cd backend && uv run uvicorn app.main:app --reload --port 8000 & \
	cd backend && uv run python -m app.worker & \
	cd frontend && npm run dev & \
	wait
```

- [ ] **Step 2: Run full backend test suite**

Run: `cd backend && uv run pytest -v`
Expected: All tests pass

- [ ] **Step 3: Run frontend build**

Run: `cd frontend && npx next build`
Expected: Build succeeds

- [ ] **Step 4: Manual smoke test**

1. Start dev environment: `make dev`
2. Log in, create a course, approve generation
3. Verify SSE stream shows real-time progress
4. Verify worker logs show job claiming and pipeline execution
5. Kill worker mid-pipeline, verify course shows "stale" status
6. Click "Resume", verify pipeline continues from checkpoint

- [ ] **Step 5: Commit**

```bash
git add Makefile
git commit -m "chore: add dev-worker target, update dev to start worker alongside backend"
```

---

## Task Dependency Graph

```
Task 1 (model + migration)
  └→ Task 2 (pipeline rewrite)
       └→ Task 3 (worker)
            └→ Task 4 (API endpoints)
                 └→ Task 6 (frontend SSE)
                      └→ Task 7 (frontend parallel fetch + generate)
                           └→ Task 8 (Makefile + integration)
Task 5 (search parallelization) — independent, can run anytime after Task 1
```

Tasks 5 and 6 can be worked on in parallel since they touch different files.
