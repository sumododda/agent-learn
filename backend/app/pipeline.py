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
    search_provider: str = "",
    search_credentials: dict | None = None,
) -> dict:
    """Run discover-and-plan with retries (3 attempts)."""
    from app.agent_service import run_discover_and_plan

    async with async_session() as session:
        return await run_discover_and_plan(course_id, session, provider, model, credentials, extra_fields, search_provider, search_credentials, skip_status_update=True)


@_retry_3
async def _research_section(
    course_id: uuid.UUID,
    position: int,
    provider: str,
    model: str,
    credentials: dict,
    extra_fields: dict | None = None,
    search_provider: str = "",
    search_credentials: dict | None = None,
) -> dict:
    """Run research for one section with retries (3 attempts)."""
    from app.agent_service import run_research_section

    async with async_session() as session:
        return await run_research_section(course_id, position, session, provider, model, credentials, extra_fields, search_provider, search_credentials)


@_retry_2
async def _verify_section(
    course_id: uuid.UUID,
    position: int,
    provider: str,
    model: str,
    credentials: dict,
    extra_fields: dict | None = None,
    search_provider: str = "",
    search_credentials: dict | None = None,
) -> dict:
    """Run verification for one section with retries (2 attempts)."""
    from app.agent_service import run_verify_section

    async with async_session() as session:
        return await run_verify_section(course_id, position, session, provider, model, credentials, extra_fields, search_provider, search_credentials)


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
    search_provider: str = "",
    search_credentials: dict | None = None,
) -> None:
    """Run the full course generation pipeline.

    1. Plan -> get sections list
    2. Parallel research all sections (gather)
    3. For each section sequentially: verify -> write -> edit
    4. Track failed sections, determine final status
    """
    cid = uuid.UUID(course_id)
    tag = course_id[:8]

    # ------------------------------------------------------------------
    # Step 1: Discover and plan (sequential)
    # ------------------------------------------------------------------
    logger.info("[pipeline:%s] === STEP 1: PLANNING === (model=%s, search=%s)", tag, model, search_provider or "none")
    _update_status(course_id, stage="planning")
    try:
        plan_result = await _discover_and_plan(cid, provider, model, credentials, extra_fields, search_provider, search_credentials)
    except Exception as e:
        logger.error("[pipeline:%s] PLANNING FAILED: %s", tag, e)
        _update_status(course_id, stage="failed", error="Planning failed")
        async with async_session() as session:
            from app.agent_service import update_course_status
            await update_course_status(cid, "failed", session)
        return

    sections = plan_result["sections"]
    positions = [s["position"] for s in sections]
    total = len(positions)
    section_titles = {s["position"]: s["title"] for s in sections}
    logger.info("[pipeline:%s] Planning complete: %d sections created", tag, total)
    for s in sections:
        logger.info("[pipeline:%s]   Section %d: %s", tag, s["position"], s["title"])

    # Track per-section status: position -> {stage, error?}
    section_statuses: dict[int, dict] = {
        pos: {"stage": "pending"} for pos in positions
    }

    # ------------------------------------------------------------------
    # Step 2: Research all sections in parallel
    # ------------------------------------------------------------------
    logger.info("[pipeline:%s] === STEP 2: RESEARCHING %d sections in parallel ===", tag, total)
    _update_status(course_id, stage="researching", total=total)
    async with async_session() as session:
        from app.agent_service import update_course_status
        await update_course_status(cid, "researching", session)
    for pos in positions:
        section_statuses[pos] = {"stage": "researching"}

    research_results = await asyncio.gather(
        *[_research_section(cid, pos, provider, model, credentials, extra_fields, search_provider, search_credentials) for pos in positions],
        return_exceptions=True,
    )

    # Mark research results
    research_ok = 0
    research_fail = 0
    for i, pos in enumerate(positions):
        result = research_results[i]
        if isinstance(result, BaseException):
            section_statuses[pos] = {"stage": "failed", "error": "Research failed"}
            research_fail += 1
            logger.error("[pipeline:%s] Research FAILED for section %d (%s): %s", tag, pos, section_titles.get(pos, ""), result)
        else:
            section_statuses[pos] = {"stage": "researched"}
            research_ok += 1
            card_count = len(result.get("evidence_cards", []))
            logger.info("[pipeline:%s] Research OK for section %d (%s): %d evidence cards", tag, pos, section_titles.get(pos, ""), card_count)

    logger.info("[pipeline:%s] Research complete: %d succeeded, %d failed", tag, research_ok, research_fail)

    # ------------------------------------------------------------------
    # Step 3: Sequential verify -> write -> edit per section
    # ------------------------------------------------------------------
    logger.info("[pipeline:%s] === STEP 3: VERIFY/WRITE/EDIT per section ===", tag)
    async with async_session() as session:
        from app.agent_service import update_course_status
        await update_course_status(cid, "writing", session)

    for idx, pos in enumerate(positions):
        sec_name = section_titles.get(pos, f"section-{pos}")
        if section_statuses[pos]["stage"] == "failed":
            logger.warning("[pipeline:%s] Skipping section %d (%s) — research failed", tag, pos, sec_name)
            continue

        # Verify
        logger.info("[pipeline:%s] [%d/%d] Verifying section %d (%s)...", tag, idx + 1, total, pos, sec_name)
        section_statuses[pos] = {"stage": "verifying"}
        _update_status(course_id, stage="writing", section=idx + 1, total=total)
        try:
            await _verify_section(cid, pos, provider, model, credentials, extra_fields, search_provider, search_credentials)
            logger.info("[pipeline:%s] [%d/%d] Verification OK for section %d", tag, idx + 1, total, pos)
        except Exception as e:
            section_statuses[pos] = {"stage": "failed", "error": "Verification failed"}
            logger.error("[pipeline:%s] [%d/%d] Verification FAILED for section %d: %s", tag, idx + 1, total, pos, e)
            continue

        # Write
        logger.info("[pipeline:%s] [%d/%d] Writing section %d (%s)...", tag, idx + 1, total, pos, sec_name)
        section_statuses[pos] = {"stage": "writing"}
        _update_status(course_id, stage="writing", section=idx + 1, total=total)
        try:
            await _write_section(cid, pos, provider, model, credentials, extra_fields)
            logger.info("[pipeline:%s] [%d/%d] Writing OK for section %d", tag, idx + 1, total, pos)
        except Exception as e:
            section_statuses[pos] = {"stage": "failed", "error": "Writing failed"}
            logger.error("[pipeline:%s] [%d/%d] Writing FAILED for section %d: %s", tag, idx + 1, total, pos, e)
            continue

        # Edit
        logger.info("[pipeline:%s] [%d/%d] Editing section %d (%s)...", tag, idx + 1, total, pos, sec_name)
        section_statuses[pos] = {"stage": "editing"}
        _update_status(course_id, stage="writing", section=idx + 1, total=total)
        try:
            await _edit_section(cid, pos, provider, model, credentials, extra_fields)
            logger.info("[pipeline:%s] [%d/%d] Editing OK for section %d", tag, idx + 1, total, pos)
        except Exception as e:
            section_statuses[pos] = {"stage": "failed", "error": "Editing failed"}
            logger.error("[pipeline:%s] [%d/%d] Editing FAILED for section %d: %s", tag, idx + 1, total, pos, e)
            continue

        section_statuses[pos] = {"stage": "completed"}
        logger.info("[pipeline:%s] [%d/%d] Section %d COMPLETE", tag, idx + 1, total, pos)

    # ------------------------------------------------------------------
    # Step 4: Determine final status
    # ------------------------------------------------------------------
    failed_sections = [
        pos for pos, s in section_statuses.items() if s["stage"] == "failed"
    ]
    completed_sections = [
        pos for pos, s in section_statuses.items() if s["stage"] == "completed"
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
        "[pipeline:%s] === PIPELINE FINISHED === status=%s, completed=%d/%d, failed=%s",
        tag, final_status, len(completed_sections), total,
        failed_sections if failed_sections else "none",
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def _safe_run_pipeline(
    course_id: str,
    provider: str,
    model: str,
    credentials: dict,
    extra_fields: dict | None = None,
    search_provider: str = "",
    search_credentials: dict | None = None,
) -> None:
    """Wrapper that catches ALL exceptions so the task never silently dies."""
    import traceback
    tag = course_id[:8]
    logger.info("[pipeline:%s] STARTED (model=%s, search=%s)", tag, model, search_provider or "none")
    try:
        await run_pipeline(course_id, provider, model, credentials, extra_fields, search_provider, search_credentials)
    except asyncio.CancelledError:
        logger.warning("[pipeline:%s] CANCELLED (server reload or shutdown)", tag)
        _update_status(course_id, stage="failed", error="Task cancelled")
        try:
            async with async_session() as session:
                from app.agent_service import update_course_status
                await update_course_status(uuid.UUID(course_id), "failed", session)
        except Exception:
            pass
        return
    except Exception as e:
        tb = traceback.format_exc()
        logger.error("[pipeline:%s] CRASHED: %s\n%s", tag, e, tb)
        _update_status(course_id, stage="failed", error=str(e)[:500])
        try:
            async with async_session() as session:
                from app.agent_service import update_course_status
                await update_course_status(uuid.UUID(course_id), "failed", session)
        except Exception:
            logger.error("[pipeline:%s] Failed to persist 'failed' status to DB", tag)


def start_pipeline(
    course_id: str,
    provider: str,
    model: str,
    credentials: dict,
    extra_fields: dict | None = None,
    search_provider: str = "",
    search_credentials: dict | None = None,
) -> None:
    """Start the pipeline as a background asyncio task.

    Stores a reference in _active_tasks to prevent GC from silently
    cancelling the task.
    """
    _jobs[course_id] = PipelineStatus()
    task = asyncio.create_task(
        _safe_run_pipeline(course_id, provider, model, credentials, extra_fields, search_provider, search_credentials),
        name=f"pipeline-{course_id}",
    )
    _active_tasks.add(task)
    task.add_done_callback(_active_tasks.discard)
