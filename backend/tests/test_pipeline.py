"""Tests for checkpoint-aware pipeline orchestrator (app.pipeline).

Tests cover:
- run_pipeline full happy path: plan → research → verify+write → edit → done
- Checkpoint resumption: skip already-completed phases
- Shutdown event: return "pending" between phases
- Partial failure: some sections fail, pipeline returns "completed_partial"
- Total failure: all sections fail, pipeline returns "failed"
- Planning failure: pipeline returns "failed" immediately
- update_checkpoint and update_job_status DB helpers
- Verify+write runs in parallel (semaphore-bounded)
- Edit runs sequentially
"""

import asyncio
import uuid
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest
from sqlalchemy import select, event as sa_event, update
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.models import (
    Base,
    Course,
    PipelineJob,
    Section,
    User,
)
from app.pipeline import (
    CHECKPOINT_QUEUED,
    CHECKPOINT_PLANNING,
    CHECKPOINT_RESEARCHED,
    CHECKPOINT_WRITING,
    CHECKPOINT_EDITING,
    CHECKPOINT_DONE,
    run_pipeline,
    update_checkpoint,
    update_job_status,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def pipeline_db():
    """Create a fresh in-memory SQLite DB with all tables."""
    engine = create_async_engine("sqlite+aiosqlite://")

    @sa_event.listens_for(engine.sync_engine, "connect")
    def _set_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def seeded(pipeline_db):
    """Create a user, course with 3 sections, and a pipeline_job row.

    Returns (job_id, course_id, positions, session).
    """
    session = pipeline_db

    user = User(email="test@test.com", password_hash="hashed")
    session.add(user)
    await session.commit()

    course = Course(topic="Python Basics", status="outline_ready", user_id=user.id)
    session.add(course)
    await session.commit()

    for i in range(1, 4):
        section = Section(
            course_id=course.id,
            position=i,
            title=f"Section {i}",
            summary=f"Summary for section {i}",
        )
        session.add(section)
    await session.commit()

    job = PipelineJob(
        course_id=course.id,
        user_id=user.id,
        status="running",
        checkpoint=CHECKPOINT_QUEUED,
        config={},
    )
    session.add(job)
    await session.commit()

    return job.id, course.id, [1, 2, 3], session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PROVIDER = "anthropic"
MODEL = "claude-sonnet-4-20250514"
CREDENTIALS = {"api_key": "sk-fake"}


def _make_plan_result(positions):
    """Build a plan result dict matching _discover_and_plan return shape."""
    return {
        "sections": [
            {"position": p, "title": f"Section {p}", "summary": f"Summary {p}"}
            for p in positions
        ],
        "research_briefs": [],
        "ungrounded": False,
    }


def _make_research_result(position):
    """Build a research result dict."""
    return {"evidence_cards": [{"id": str(uuid.uuid4()), "claim": f"Claim {position}"}]}


# ---------------------------------------------------------------------------
# Test: update_checkpoint helper
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_update_checkpoint(seeded):
    job_id, course_id, positions, session = seeded

    await update_checkpoint(job_id, CHECKPOINT_RESEARCHED, session)

    result = await session.execute(
        select(PipelineJob).where(PipelineJob.id == job_id)
    )
    job = result.scalar_one()
    assert job.checkpoint == CHECKPOINT_RESEARCHED


# ---------------------------------------------------------------------------
# Test: update_job_status helper
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_update_job_status_sets_completed_at(seeded):
    job_id, course_id, positions, session = seeded

    await update_job_status(job_id, "completed", session)

    result = await session.execute(
        select(PipelineJob).where(PipelineJob.id == job_id)
    )
    job = result.scalar_one()
    assert job.status == "completed"
    assert job.completed_at is not None


@pytest.mark.anyio
async def test_update_job_status_with_error(seeded):
    job_id, course_id, positions, session = seeded

    await update_job_status(job_id, "failed", session, error="Something broke")

    result = await session.execute(
        select(PipelineJob).where(PipelineJob.id == job_id)
    )
    job = result.scalar_one()
    assert job.status == "failed"
    assert job.error == "Something broke"
    assert job.completed_at is not None


# ---------------------------------------------------------------------------
# Test: Full pipeline happy path
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_full_pipeline_happy_path(setup_db, seeded):
    """Plan → research → verify+write → edit → completed for all sections."""
    job_id, course_id, positions, session = seeded

    plan_result = _make_plan_result(positions)

    with (
        patch("app.pipeline._discover_and_plan", new_callable=AsyncMock, return_value=plan_result),
        patch("app.pipeline._research_section", new_callable=AsyncMock, side_effect=lambda cid, pos, *a, **kw: _make_research_result(pos)),
        patch("app.pipeline._verify_section", new_callable=AsyncMock, return_value={}),
        patch("app.pipeline._write_section", new_callable=AsyncMock, return_value={}),
        patch("app.pipeline._edit_section", new_callable=AsyncMock, return_value={}),
        patch("app.agent_service.update_course_status", new_callable=AsyncMock),
        patch("app.pipeline.async_session", return_value=_FakeSessionCtx(session)),
    ):
        result = await run_pipeline(
            job_id, course_id, CHECKPOINT_QUEUED,
            PROVIDER, MODEL, CREDENTIALS,
        )

    assert result == "completed"

    # Verify job checkpoint advanced to DONE
    await session.execute(select(PipelineJob).where(PipelineJob.id == job_id))
    # Check job was updated
    job_result = await session.execute(select(PipelineJob).where(PipelineJob.id == job_id))
    job = job_result.scalar_one()
    assert job.checkpoint == CHECKPOINT_DONE
    assert job.status == "completed"


# ---------------------------------------------------------------------------
# Test: Planning failure
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_planning_failure(setup_db, seeded):
    """When planning fails, pipeline returns 'failed' and updates job."""
    job_id, course_id, positions, session = seeded

    with (
        patch("app.pipeline._discover_and_plan", new_callable=AsyncMock, side_effect=RuntimeError("LLM down")),
        patch("app.agent_service.update_course_status", new_callable=AsyncMock),
        patch("app.pipeline.async_session", return_value=_FakeSessionCtx(session)),
    ):
        result = await run_pipeline(
            job_id, course_id, CHECKPOINT_QUEUED,
            PROVIDER, MODEL, CREDENTIALS,
        )

    assert result == "failed"

    job_result = await session.execute(select(PipelineJob).where(PipelineJob.id == job_id))
    job = job_result.scalar_one()
    assert job.status == "failed"
    assert "Planning failed" in (job.error or "")


# ---------------------------------------------------------------------------
# Test: Checkpoint resumption skips planning
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resume_from_researched_skips_plan_and_research(setup_db, seeded):
    """Starting from CHECKPOINT_RESEARCHED skips planning and research phases."""
    job_id, course_id, positions, session = seeded

    plan_mock = AsyncMock()
    research_mock = AsyncMock()

    with (
        patch("app.pipeline._discover_and_plan", plan_mock),
        patch("app.pipeline._research_section", research_mock),
        patch("app.pipeline._verify_section", new_callable=AsyncMock, return_value={}),
        patch("app.pipeline._write_section", new_callable=AsyncMock, return_value={}),
        patch("app.pipeline._edit_section", new_callable=AsyncMock, return_value={}),
        patch("app.agent_service.update_course_status", new_callable=AsyncMock),
        patch("app.pipeline.async_session", return_value=_FakeSessionCtx(session)),
    ):
        result = await run_pipeline(
            job_id, course_id, CHECKPOINT_RESEARCHED,
            PROVIDER, MODEL, CREDENTIALS,
        )

    assert result == "completed"
    plan_mock.assert_not_called()
    research_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Test: Checkpoint resumption skips verify+write
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resume_from_writing_skips_vw(setup_db, seeded):
    """Starting from CHECKPOINT_WRITING skips plan, research, and verify+write."""
    job_id, course_id, positions, session = seeded

    verify_mock = AsyncMock()
    write_mock = AsyncMock()

    with (
        patch("app.pipeline._discover_and_plan", new_callable=AsyncMock),
        patch("app.pipeline._research_section", new_callable=AsyncMock),
        patch("app.pipeline._verify_section", verify_mock),
        patch("app.pipeline._write_section", write_mock),
        patch("app.pipeline._edit_section", new_callable=AsyncMock, return_value={}),
        patch("app.agent_service.update_course_status", new_callable=AsyncMock),
        patch("app.pipeline.async_session", return_value=_FakeSessionCtx(session)),
    ):
        result = await run_pipeline(
            job_id, course_id, CHECKPOINT_WRITING,
            PROVIDER, MODEL, CREDENTIALS,
        )

    assert result == "completed"
    verify_mock.assert_not_called()
    write_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Test: Shutdown event returns "pending"
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_shutdown_event_returns_pending(setup_db, seeded):
    """If shutdown_event is set between phases, pipeline returns 'pending'."""
    job_id, course_id, positions, session = seeded

    shutdown = asyncio.Event()

    plan_result = _make_plan_result(positions)

    async def _plan_then_shutdown(*args, **kwargs):
        shutdown.set()  # Set shutdown after planning completes
        return plan_result

    with (
        patch("app.pipeline._discover_and_plan", new_callable=AsyncMock, side_effect=_plan_then_shutdown),
        patch("app.pipeline._research_section", new_callable=AsyncMock),
        patch("app.pipeline._verify_section", new_callable=AsyncMock),
        patch("app.pipeline._write_section", new_callable=AsyncMock),
        patch("app.pipeline._edit_section", new_callable=AsyncMock),
        patch("app.agent_service.update_course_status", new_callable=AsyncMock),
        patch("app.pipeline.async_session", return_value=_FakeSessionCtx(session)),
    ):
        result = await run_pipeline(
            job_id, course_id, CHECKPOINT_QUEUED,
            PROVIDER, MODEL, CREDENTIALS,
            shutdown_event=shutdown,
        )

    assert result == "pending"


# ---------------------------------------------------------------------------
# Test: Partial failure — some sections fail research
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_partial_research_failure(setup_db, seeded):
    """When one section's research fails, pipeline returns 'completed_partial'."""
    job_id, course_id, positions, session = seeded

    plan_result = _make_plan_result(positions)

    async def _research(cid, pos, *a, **kw):
        if pos == 2:
            raise RuntimeError("Research failed for section 2")
        return _make_research_result(pos)

    with (
        patch("app.pipeline._discover_and_plan", new_callable=AsyncMock, return_value=plan_result),
        patch("app.pipeline._research_section", new_callable=AsyncMock, side_effect=_research),
        patch("app.pipeline._verify_section", new_callable=AsyncMock, return_value={}),
        patch("app.pipeline._write_section", new_callable=AsyncMock, return_value={}),
        patch("app.pipeline._edit_section", new_callable=AsyncMock, return_value={}),
        patch("app.agent_service.update_course_status", new_callable=AsyncMock),
        patch("app.pipeline.async_session", return_value=_FakeSessionCtx(session)),
    ):
        result = await run_pipeline(
            job_id, course_id, CHECKPOINT_QUEUED,
            PROVIDER, MODEL, CREDENTIALS,
        )

    assert result == "completed_partial"


# ---------------------------------------------------------------------------
# Test: All sections fail → "failed"
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_all_sections_fail(setup_db, seeded):
    """When all sections fail, pipeline returns 'failed'."""
    job_id, course_id, positions, session = seeded

    plan_result = _make_plan_result(positions)

    with (
        patch("app.pipeline._discover_and_plan", new_callable=AsyncMock, return_value=plan_result),
        patch("app.pipeline._research_section", new_callable=AsyncMock, side_effect=RuntimeError("boom")),
        patch("app.pipeline._verify_section", new_callable=AsyncMock, return_value={}),
        patch("app.pipeline._write_section", new_callable=AsyncMock, return_value={}),
        patch("app.pipeline._edit_section", new_callable=AsyncMock, return_value={}),
        patch("app.agent_service.update_course_status", new_callable=AsyncMock),
        patch("app.pipeline.async_session", return_value=_FakeSessionCtx(session)),
    ):
        result = await run_pipeline(
            job_id, course_id, CHECKPOINT_QUEUED,
            PROVIDER, MODEL, CREDENTIALS,
        )

    assert result == "failed"


# ---------------------------------------------------------------------------
# Test: Verify failure skips write and edit for that section
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_verify_failure_skips_write_and_edit(setup_db, seeded):
    """When verify fails for a section, write and edit are skipped for it."""
    job_id, course_id, positions, session = seeded

    plan_result = _make_plan_result(positions)
    write_positions = []
    edit_positions = []

    async def _verify(cid, pos, *a, **kw):
        if pos == 2:
            raise RuntimeError("Verify failed for section 2")
        return {}

    async def _write(cid, pos, *a, **kw):
        write_positions.append(pos)
        return {}

    async def _edit(cid, pos, *a, **kw):
        edit_positions.append(pos)
        return {}

    with (
        patch("app.pipeline._discover_and_plan", new_callable=AsyncMock, return_value=plan_result),
        patch("app.pipeline._research_section", new_callable=AsyncMock, side_effect=lambda cid, pos, *a, **kw: _make_research_result(pos)),
        patch("app.pipeline._verify_section", new_callable=AsyncMock, side_effect=_verify),
        patch("app.pipeline._write_section", new_callable=AsyncMock, side_effect=_write),
        patch("app.pipeline._edit_section", new_callable=AsyncMock, side_effect=_edit),
        patch("app.agent_service.update_course_status", new_callable=AsyncMock),
        patch("app.pipeline.async_session", return_value=_FakeSessionCtx(session)),
    ):
        result = await run_pipeline(
            job_id, course_id, CHECKPOINT_QUEUED,
            PROVIDER, MODEL, CREDENTIALS,
        )

    assert result == "completed_partial"
    # Section 2 should not appear in write or edit
    assert 2 not in write_positions
    assert 2 not in edit_positions
    # Sections 1 and 3 should have been written and edited
    assert 1 in write_positions and 3 in write_positions
    assert 1 in edit_positions and 3 in edit_positions


# ---------------------------------------------------------------------------
# Test: Edit failure marks section as failed but pipeline continues
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_edit_failure_marks_section_failed(setup_db, seeded):
    """When edit fails for one section, it's marked failed but others complete."""
    job_id, course_id, positions, session = seeded

    plan_result = _make_plan_result(positions)

    async def _edit(cid, pos, *a, **kw):
        if pos == 1:
            raise RuntimeError("Edit failed for section 1")
        return {}

    with (
        patch("app.pipeline._discover_and_plan", new_callable=AsyncMock, return_value=plan_result),
        patch("app.pipeline._research_section", new_callable=AsyncMock, side_effect=lambda cid, pos, *a, **kw: _make_research_result(pos)),
        patch("app.pipeline._verify_section", new_callable=AsyncMock, return_value={}),
        patch("app.pipeline._write_section", new_callable=AsyncMock, return_value={}),
        patch("app.pipeline._edit_section", new_callable=AsyncMock, side_effect=_edit),
        patch("app.agent_service.update_course_status", new_callable=AsyncMock),
        patch("app.pipeline.async_session", return_value=_FakeSessionCtx(session)),
    ):
        result = await run_pipeline(
            job_id, course_id, CHECKPOINT_QUEUED,
            PROVIDER, MODEL, CREDENTIALS,
        )

    assert result == "completed_partial"


# ---------------------------------------------------------------------------
# Test: Checkpoint constants are ordinal
# ---------------------------------------------------------------------------


def test_checkpoint_constants_are_ordinal():
    """Checkpoint constants increase monotonically."""
    assert CHECKPOINT_QUEUED < CHECKPOINT_PLANNING
    assert CHECKPOINT_PLANNING < CHECKPOINT_RESEARCHED
    assert CHECKPOINT_RESEARCHED < CHECKPOINT_WRITING
    assert CHECKPOINT_WRITING < CHECKPOINT_EDITING
    assert CHECKPOINT_EDITING < CHECKPOINT_DONE


# ---------------------------------------------------------------------------
# Test: No sections found after planning returns "failed"
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_no_sections_returns_failed(setup_db, pipeline_db):
    """When DB has no sections, pipeline returns 'failed'."""
    session = pipeline_db

    user = User(email="nouser@test.com", password_hash="hashed")
    session.add(user)
    await session.commit()

    course = Course(topic="Empty Course", status="outline_ready", user_id=user.id)
    session.add(course)
    await session.commit()

    job = PipelineJob(
        course_id=course.id,
        user_id=user.id,
        status="running",
        checkpoint=CHECKPOINT_QUEUED,
        config={},
    )
    session.add(job)
    await session.commit()

    with (
        patch("app.pipeline.async_session", return_value=_FakeSessionCtx(session)),
        patch("app.agent_service.update_course_status", new_callable=AsyncMock),
    ):
        # Start from CHECKPOINT_PLANNING (skip plan phase) — no sections in DB
        result = await run_pipeline(
            job.id, course.id, CHECKPOINT_PLANNING,
            PROVIDER, MODEL, CREDENTIALS,
        )

    assert result == "failed"


# ---------------------------------------------------------------------------
# Test: Verify+write uses parallel execution
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_verify_write_runs_in_parallel(setup_db, seeded):
    """Verify+write should use asyncio.gather (not be purely sequential)."""
    job_id, course_id, positions, session = seeded

    plan_result = _make_plan_result(positions)

    # Track concurrent execution to verify parallelism
    concurrent = 0
    max_concurrent = 0
    lock = asyncio.Lock()

    async def _verify(cid, pos, *a, **kw):
        nonlocal concurrent, max_concurrent
        async with lock:
            concurrent += 1
            max_concurrent = max(max_concurrent, concurrent)
        await asyncio.sleep(0.01)  # Simulate work
        async with lock:
            concurrent -= 1
        return {}

    with (
        patch("app.pipeline._discover_and_plan", new_callable=AsyncMock, return_value=plan_result),
        patch("app.pipeline._research_section", new_callable=AsyncMock, side_effect=lambda cid, pos, *a, **kw: _make_research_result(pos)),
        patch("app.pipeline._verify_section", new_callable=AsyncMock, side_effect=_verify),
        patch("app.pipeline._write_section", new_callable=AsyncMock, return_value={}),
        patch("app.pipeline._edit_section", new_callable=AsyncMock, return_value={}),
        patch("app.agent_service.update_course_status", new_callable=AsyncMock),
        patch("app.pipeline.async_session", return_value=_FakeSessionCtx(session)),
    ):
        result = await run_pipeline(
            job_id, course_id, CHECKPOINT_QUEUED,
            PROVIDER, MODEL, CREDENTIALS,
        )

    assert result == "completed"
    # With 3 sections and Semaphore(3), all should run concurrently
    assert max_concurrent >= 2, f"Expected parallel execution, got max_concurrent={max_concurrent}"


# ---------------------------------------------------------------------------
# Test: Edit runs sequentially (verify ordering)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_edit_runs_sequentially(setup_db, seeded):
    """Edit phase processes sections one at a time in order."""
    job_id, course_id, positions, session = seeded

    edit_order = []

    async def _edit(cid, pos, *a, **kw):
        edit_order.append(pos)
        return {}

    with (
        patch("app.pipeline._discover_and_plan", new_callable=AsyncMock, return_value=_make_plan_result(positions)),
        patch("app.pipeline._research_section", new_callable=AsyncMock, side_effect=lambda cid, pos, *a, **kw: _make_research_result(pos)),
        patch("app.pipeline._verify_section", new_callable=AsyncMock, return_value={}),
        patch("app.pipeline._write_section", new_callable=AsyncMock, return_value={}),
        patch("app.pipeline._edit_section", new_callable=AsyncMock, side_effect=_edit),
        patch("app.agent_service.update_course_status", new_callable=AsyncMock),
        patch("app.pipeline.async_session", return_value=_FakeSessionCtx(session)),
    ):
        result = await run_pipeline(
            job_id, course_id, CHECKPOINT_QUEUED,
            PROVIDER, MODEL, CREDENTIALS,
        )

    assert result == "completed"
    assert edit_order == [1, 2, 3]


# ---------------------------------------------------------------------------
# Helper: fake async context manager for session patching
# ---------------------------------------------------------------------------


class _FakeSessionCtx:
    """Wraps a real session to act as ``async with async_session() as s``."""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False

    def __call__(self):
        """Support ``async_session()`` returning this context manager."""
        return self
