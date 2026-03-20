"""Asyncio-based pipeline orchestrator for course generation.

Orchestration flow:
1. discover_and_plan (sequential) — 3 attempts
2. research all sections (parallel via asyncio.gather) — 3 attempts each
3. for each section sequentially: verify (2) -> write (3) -> edit (2), skip failed
4. determine final status: completed / failed / completed_partial
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass

from tenacity import retry, stop_after_attempt, wait_exponential

from app.database import async_session

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pipeline status tracking
# ---------------------------------------------------------------------------


@dataclass
class PipelineStatus:
    stage: str = "pending"
    section: int = 0
    total: int = 0
    error: str | None = None


_jobs: dict[str, PipelineStatus] = {}
_active_tasks: set[asyncio.Task] = set()


def get_pipeline_status(course_id: str) -> PipelineStatus | None:
    """Return current pipeline status for a course, or None."""
    return _jobs.get(course_id)


def _update_status(
    course_id: str,
    stage: str,
    section: int = 0,
    total: int = 0,
    error: str | None = None,
) -> None:
    """Update the in-memory pipeline status for a course."""
    status = _jobs.get(course_id)
    if status is None:
        _jobs[course_id] = PipelineStatus(
            stage=stage, section=section, total=total, error=error
        )
    else:
        status.stage = stage
        status.section = section
        status.total = total
        status.error = error


# ---------------------------------------------------------------------------
# Retry wrappers — match Trigger.dev retry counts exactly
# ---------------------------------------------------------------------------

_retry_3 = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=1, max=30),
    reraise=True,
)

_retry_2 = retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=2, min=1, max=30),
    reraise=True,
)


@_retry_3
async def _discover_and_plan(
    course_id: uuid.UUID,
    provider: str,
    model: str,
    credentials: dict,
    extra_fields: dict | None = None,
) -> dict:
    """Run discover-and-plan with retries (3 attempts)."""
    from app.agent_service import run_discover_and_plan

    async with async_session() as session:
        return await run_discover_and_plan(course_id, session, provider, model, credentials, extra_fields)


@_retry_3
async def _research_section(
    course_id: uuid.UUID,
    position: int,
    provider: str,
    model: str,
    credentials: dict,
    extra_fields: dict | None = None,
) -> dict:
    """Run research for one section with retries (3 attempts)."""
    from app.agent_service import run_research_section

    async with async_session() as session:
        return await run_research_section(course_id, position, session, provider, model, credentials, extra_fields)


@_retry_2
async def _verify_section(
    course_id: uuid.UUID,
    position: int,
    provider: str,
    model: str,
    credentials: dict,
    extra_fields: dict | None = None,
) -> dict:
    """Run verification for one section with retries (2 attempts)."""
    from app.agent_service import run_verify_section

    async with async_session() as session:
        return await run_verify_section(course_id, position, session, provider, model, credentials, extra_fields)


@_retry_3
async def _write_section(
    course_id: uuid.UUID,
    position: int,
    provider: str,
    model: str,
    credentials: dict,
    extra_fields: dict | None = None,
) -> dict:
    """Run writer for one section with retries (3 attempts)."""
    from app.agent_service import run_write_section

    async with async_session() as session:
        return await run_write_section(course_id, position, session, provider, model, credentials, extra_fields)


@_retry_2
async def _edit_section(
    course_id: uuid.UUID,
    position: int,
    provider: str,
    model: str,
    credentials: dict,
    extra_fields: dict | None = None,
) -> dict:
    """Run editor for one section with retries (2 attempts)."""
    from app.agent_service import run_edit_section

    async with async_session() as session:
        return await run_edit_section(course_id, position, session, provider, model, credentials, extra_fields)


# ---------------------------------------------------------------------------
# Pipeline orchestrator — no retry on the orchestrator itself
# ---------------------------------------------------------------------------


async def run_pipeline(
    course_id: str,
    provider: str,
    model: str,
    credentials: dict,
    extra_fields: dict | None = None,
) -> None:
    """Run the full course generation pipeline.

    Matches the exact flow from generate-course.ts:
    1. Plan -> get sections list
    2. Parallel research all sections (gather)
    3. For each section sequentially: verify -> write -> edit
    4. Track failed sections, determine final status
    """
    cid = uuid.UUID(course_id)

    # ------------------------------------------------------------------
    # Step 1: Discover and plan (sequential)
    # ------------------------------------------------------------------
    _update_status(course_id, stage="planning")
    try:
        plan_result = await _discover_and_plan(cid, provider, model, credentials, extra_fields)
    except Exception as e:
        logger.error("Pipeline planning failed for course %s: %s", course_id, e)
        _update_status(course_id, stage="failed", error="Planning failed")
        async with async_session() as session:
            from app.agent_service import update_course_status
            await update_course_status(cid, "failed", session)
        return

    sections = plan_result["sections"]
    positions = [s["position"] for s in sections]
    total = len(positions)

    # Track per-section status: position -> {stage, error?}
    section_statuses: dict[int, dict] = {
        pos: {"stage": "pending"} for pos in positions
    }

    # ------------------------------------------------------------------
    # Step 2: Research all sections in parallel
    # ------------------------------------------------------------------
    _update_status(course_id, stage="researching", total=total)
    for pos in positions:
        section_statuses[pos] = {"stage": "researching"}

    research_results = await asyncio.gather(
        *[_research_section(cid, pos, provider, model, credentials, extra_fields) for pos in positions],
        return_exceptions=True,
    )

    # Mark research results
    for i, pos in enumerate(positions):
        result = research_results[i]
        if isinstance(result, BaseException):
            section_statuses[pos] = {"stage": "failed", "error": "Research failed"}
            logger.error(
                "Research failed for course %s section %s: %s", course_id, pos, result
            )
        else:
            section_statuses[pos] = {"stage": "researched"}

    # ------------------------------------------------------------------
    # Step 3: Sequential verify -> write -> edit per section
    # ------------------------------------------------------------------
    for idx, pos in enumerate(positions):
        if section_statuses[pos]["stage"] == "failed":
            continue

        # Verify
        section_statuses[pos] = {"stage": "verifying"}
        _update_status(course_id, stage="writing", section=idx + 1, total=total)
        try:
            await _verify_section(cid, pos, provider, model, credentials, extra_fields)
        except Exception as e:
            section_statuses[pos] = {"stage": "failed", "error": "Verification failed"}
            logger.error(
                "Verify failed for course %s section %s: %s", course_id, pos, e
            )
            continue

        # Write
        section_statuses[pos] = {"stage": "writing"}
        _update_status(course_id, stage="writing", section=idx + 1, total=total)
        try:
            await _write_section(cid, pos, provider, model, credentials, extra_fields)
        except Exception as e:
            section_statuses[pos] = {"stage": "failed", "error": "Writing failed"}
            logger.error(
                "Write failed for course %s section %s: %s", course_id, pos, e
            )
            continue

        # Edit
        section_statuses[pos] = {"stage": "editing"}
        _update_status(course_id, stage="writing", section=idx + 1, total=total)
        try:
            await _edit_section(cid, pos, provider, model, credentials, extra_fields)
        except Exception as e:
            section_statuses[pos] = {"stage": "failed", "error": "Editing failed"}
            logger.error(
                "Edit failed for course %s section %s: %s", course_id, pos, e
            )
            continue

        section_statuses[pos] = {"stage": "completed"}

    # ------------------------------------------------------------------
    # Step 4: Determine final status
    # ------------------------------------------------------------------
    failed_sections = [
        pos for pos, s in section_statuses.items() if s["stage"] == "failed"
    ]
    if len(failed_sections) == 0:
        final_status = "completed"
    elif len(failed_sections) == total:
        final_status = "failed"
    else:
        final_status = "completed_partial"

    _update_status(course_id, stage=final_status, section=total, total=total)

    # Persist final status to database
    async with async_session() as session:
        from app.agent_service import update_course_status
        await update_course_status(cid, final_status, session)

    logger.info(
        "Pipeline finished for course %s: %s (failed sections: %s)",
        course_id,
        final_status,
        failed_sections,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def start_pipeline(
    course_id: str,
    provider: str,
    model: str,
    credentials: dict,
    extra_fields: dict | None = None,
) -> None:
    """Start the pipeline as a background asyncio task.

    Stores a reference in _active_tasks to prevent GC from silently
    cancelling the task.
    """
    _jobs[course_id] = PipelineStatus()
    task = asyncio.create_task(
        run_pipeline(course_id, provider, model, credentials, extra_fields),
        name=f"pipeline-{course_id}",
    )
    _active_tasks.add(task)
    task.add_done_callback(_active_tasks.discard)
