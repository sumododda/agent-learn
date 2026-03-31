"""Checkpoint-aware pipeline orchestrator for course generation.

Orchestration flow (resumable from any checkpoint):
1. Planning     — discover_and_plan (3 retries)
2. Research     — parallel research per section (3 retries each)
3. Verify+Write — parallel verify→write per section, bounded by semaphore(3)
4. Edit         — sequential per section (blackboard safety)
5. Done         — determine final status, persist to DB

State is persisted to the ``pipeline_jobs`` table via ``update_checkpoint``
after each phase, so pipelines survive server restarts.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text, update
from tenacity import retry, stop_after_attempt, wait_exponential

from app.database import async_session
from app.models import PipelineJob, Section

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Checkpoint constants (integers for ordinal-safe comparison)
# ---------------------------------------------------------------------------

CHECKPOINT_QUEUED = 0
CHECKPOINT_PLANNING = 1
CHECKPOINT_RESEARCHED = 2
CHECKPOINT_WRITING = 3
CHECKPOINT_EDITING = 4
CHECKPOINT_DONE = 5


# ---------------------------------------------------------------------------
# DB helper functions
# ---------------------------------------------------------------------------


async def update_checkpoint(
    job_id: uuid.UUID,
    checkpoint: int,
    session,
) -> None:
    """UPDATE pipeline_jobs SET checkpoint = :checkpoint WHERE id = :job_id."""
    await session.execute(
        update(PipelineJob)
        .where(PipelineJob.id == job_id)
        .values(checkpoint=checkpoint)
    )
    await session.commit()


async def update_job_status(
    job_id: uuid.UUID,
    status: str,
    session,
    error: str | None = None,
) -> None:
    """UPDATE pipeline_jobs SET status, error, completed_at WHERE id = :job_id."""
    values: dict = {"status": status, "error": error}
    if status in ("completed", "completed_partial", "failed"):
        values["completed_at"] = datetime.now(timezone.utc)
    await session.execute(
        update(PipelineJob)
        .where(PipelineJob.id == job_id)
        .values(**values)
    )
    await session.commit()


async def is_job_cancelled(job_id: uuid.UUID) -> bool:
    """Check if a pipeline job has been cancelled (e.g. course deleted)."""
    async with async_session() as session:
        result = await session.execute(
            select(PipelineJob.status).where(PipelineJob.id == job_id)
        )
        status = result.scalar_one_or_none()
        return status == "cancelled" or status is None


async def append_pipeline_event(job_id: uuid.UUID, event: str, data: dict) -> None:
    """Append an event to the pipeline_jobs.events JSONB array."""
    async with async_session() as session:
        await session.execute(
            text("UPDATE pipeline_jobs SET events = COALESCE(events, '[]'::jsonb) || CAST(:event AS jsonb) WHERE id = :job_id"),
            {"event": json.dumps([{"event": event, "data": data}]), "job_id": str(job_id)},
        )
        await session.commit()


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
    user_id: str = "",
    academic_options: dict | None = None,
) -> dict:
    """Run discover-and-plan with retries (3 attempts)."""
    from app.agent_service import run_discover_and_plan

    async with async_session() as session:
        return await run_discover_and_plan(course_id, session, provider, model, credentials, extra_fields, search_provider, search_credentials, skip_status_update=True, user_id=user_id, academic_options=academic_options)


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
    user_id: str = "",
    academic_options: dict | None = None,
) -> dict:
    """Run research for one section with retries (3 attempts)."""
    from app.agent_service import run_research_section

    async with async_session() as session:
        return await run_research_section(course_id, position, session, provider, model, credentials, extra_fields, search_provider, search_credentials, user_id=user_id, academic_options=academic_options)


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
    user_id: str = "",
) -> dict:
    """Run verification for one section with retries (2 attempts)."""
    from app.agent_service import run_verify_section

    async with async_session() as session:
        return await run_verify_section(course_id, position, session, provider, model, credentials, extra_fields, search_provider, search_credentials, user_id=user_id)


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
# Pipeline orchestrator — checkpoint-aware, no retry on the orchestrator
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
    user_id: str = "",
    academic_options: dict | None = None,
) -> str:
    """Run the course generation pipeline, resumable from *checkpoint*.

    Returns the final status string:
    ``"completed"`` | ``"completed_partial"`` | ``"failed"`` | ``"pending"``
    """
    tag = str(course_id)[:8]

    async def emit(event: str, data: dict) -> None:
        """Fire-and-forget pipeline event."""
        try:
            await append_pipeline_event(job_id, event, data)
        except Exception:
            logger.warning("[pipeline:%s] Failed to emit event %s", tag, event, exc_info=True)

    # Sets tracking per-section failures across phases
    research_failed: set[int] = set()
    vw_failed: set[int] = set()

    # ------------------------------------------------------------------
    # Phase 1: Planning
    # ------------------------------------------------------------------
    if checkpoint < CHECKPOINT_PLANNING:
        logger.info("[pipeline:%s] === PHASE 1: PLANNING === (model=%s, search=%s)", tag, model, search_provider or "none")
        try:
            plan_result = await _discover_and_plan(course_id, provider, model, credentials, extra_fields, search_provider, search_credentials, user_id=user_id, academic_options=academic_options)
        except Exception as e:
            logger.error("[pipeline:%s] PLANNING FAILED: %s", tag, e)
            async with async_session() as session:
                from app.agent_service import update_course_status
                await update_course_status(course_id, "failed", session)
            async with async_session() as session:
                await update_job_status(job_id, "failed", session, error="Planning failed")
            return "failed"

        sections_data = plan_result["sections"]
        total = len(sections_data)
        logger.info("[pipeline:%s] Planning complete: %d sections created", tag, total)
        for s in sections_data:
            logger.info("[pipeline:%s]   Section %d: %s", tag, s["position"], s["title"])

        async with async_session() as session:
            await update_checkpoint(job_id, CHECKPOINT_PLANNING, session)

    # Check shutdown / cancellation between phases
    if shutdown_event and shutdown_event.is_set():
        logger.info("[pipeline:%s] Shutdown requested after planning", tag)
        return "pending"
    if await is_job_cancelled(job_id):
        logger.info("[pipeline:%s] Job cancelled after planning", tag)
        return "cancelled"

    # ------------------------------------------------------------------
    # Load sections from DB (needed for both fresh runs and resumption)
    # ------------------------------------------------------------------
    async with async_session() as session:
        result = await session.execute(
            select(Section)
            .where(Section.course_id == course_id)
            .order_by(Section.position)
        )
        sections = list(result.scalars().all())

    positions = [s.position for s in sections]
    section_titles = {s.position: s.title for s in sections}
    total = len(positions)

    if total == 0:
        logger.error("[pipeline:%s] No sections found — aborting", tag)
        async with async_session() as session:
            await update_job_status(job_id, "failed", session, error="No sections found")
        return "failed"

    await emit("pipeline_start", {"total_sections": total})

    # ------------------------------------------------------------------
    # Phase 2: Research all sections in parallel
    # ------------------------------------------------------------------
    if checkpoint < CHECKPOINT_RESEARCHED:
        logger.info("[pipeline:%s] === PHASE 2: RESEARCHING %d sections (sem=7) ===", tag, total)
        async with async_session() as session:
            from app.agent_service import update_course_status
            await update_course_status(course_id, "researching", session)

        research_sem = asyncio.Semaphore(7)

        async def _research_with_events(pos: int) -> dict:
            async with research_sem:
                title = section_titles.get(pos, f"section-{pos}")
                await emit("research_start", {"section": pos, "title": title})
                result = await _research_section(course_id, pos, provider, model, credentials, extra_fields, search_provider, search_credentials, user_id=user_id, academic_options=academic_options)
                sources_found = len(result.get("evidence_cards", []))
                await emit("research_done", {"section": pos, "sources_found": sources_found})
                return result

        research_results = await asyncio.gather(
            *[_research_with_events(pos) for pos in positions],
            return_exceptions=True,
        )

        research_ok = 0
        for i, pos in enumerate(positions):
            res = research_results[i]
            if isinstance(res, BaseException):
                research_failed.add(pos)
                logger.error("[pipeline:%s] Research FAILED for section %d (%s): %s", tag, pos, section_titles.get(pos, ""), res)
            else:
                research_ok += 1
                card_count = len(res.get("evidence_cards", []))
                logger.info("[pipeline:%s] Research OK for section %d (%s): %d evidence cards", tag, pos, section_titles.get(pos, ""), card_count)

        logger.info("[pipeline:%s] Research complete: %d succeeded, %d failed", tag, research_ok, len(research_failed))

        async with async_session() as session:
            await update_checkpoint(job_id, CHECKPOINT_RESEARCHED, session)

    # Check shutdown / cancellation between phases
    if shutdown_event and shutdown_event.is_set():
        logger.info("[pipeline:%s] Shutdown requested after research", tag)
        return "pending"
    if await is_job_cancelled(job_id):
        logger.info("[pipeline:%s] Job cancelled after research", tag)
        return "cancelled"

    # ------------------------------------------------------------------
    # Phase 3: Verify + Write per section (parallel, bounded by semaphore)
    # ------------------------------------------------------------------
    if checkpoint < CHECKPOINT_WRITING:
        logger.info("[pipeline:%s] === PHASE 3: VERIFY+WRITE per section (parallel, sem=3) ===", tag)
        async with async_session() as session:
            from app.agent_service import update_course_status
            await update_course_status(course_id, "writing", session)

        sem = asyncio.Semaphore(3)

        async def _verify_then_write(pos: int) -> None:
            """Verify then write a single section, bounded by semaphore."""
            sec_name = section_titles.get(pos, f"section-{pos}")
            if pos in research_failed:
                logger.warning("[pipeline:%s] Skipping section %d (%s) — research failed", tag, pos, sec_name)
                vw_failed.add(pos)
                return

            async with sem:
                # Verify
                logger.info("[pipeline:%s] Verifying section %d (%s)...", tag, pos, sec_name)
                await emit("verify_start", {"section": pos, "title": sec_name})
                try:
                    await _verify_section(course_id, pos, provider, model, credentials, extra_fields, search_provider, search_credentials, user_id=user_id)
                    logger.info("[pipeline:%s] Verification OK for section %d", tag, pos)
                    await emit("verify_done", {"section": pos})
                except Exception as e:
                    logger.error("[pipeline:%s] Verification FAILED for section %d: %s", tag, pos, e)
                    vw_failed.add(pos)
                    return

                # Write
                logger.info("[pipeline:%s] Writing section %d (%s)...", tag, pos, sec_name)
                await emit("write_start", {"section": pos, "title": sec_name})
                try:
                    await _write_section(course_id, pos, provider, model, credentials, extra_fields)
                    logger.info("[pipeline:%s] Writing OK for section %d", tag, pos)
                    await emit("write_done", {"section": pos})
                except Exception as e:
                    logger.error("[pipeline:%s] Writing FAILED for section %d: %s", tag, pos, e)
                    vw_failed.add(pos)
                    return

        await asyncio.gather(*[_verify_then_write(pos) for pos in positions])

        async with async_session() as session:
            await update_checkpoint(job_id, CHECKPOINT_WRITING, session)

    # Check shutdown / cancellation between phases
    if shutdown_event and shutdown_event.is_set():
        logger.info("[pipeline:%s] Shutdown requested after verify+write", tag)
        return "pending"
    if await is_job_cancelled(job_id):
        logger.info("[pipeline:%s] Job cancelled after verify+write", tag)
        return "cancelled"

    # ------------------------------------------------------------------
    # Phase 4: Edit — sequential for blackboard safety
    # ------------------------------------------------------------------
    if checkpoint < CHECKPOINT_EDITING:
        logger.info("[pipeline:%s] === PHASE 4: EDITING sequentially ===", tag)

        all_failed = research_failed | vw_failed
        for idx, pos in enumerate(positions):
            sec_name = section_titles.get(pos, f"section-{pos}")
            if pos in all_failed:
                logger.warning("[pipeline:%s] Skipping edit for section %d (%s) — prior failure", tag, pos, sec_name)
                continue

            logger.info("[pipeline:%s] [%d/%d] Editing section %d (%s)...", tag, idx + 1, total, pos, sec_name)
            await emit("edit_start", {"section": pos, "title": sec_name})
            try:
                await _edit_section(course_id, pos, provider, model, credentials, extra_fields)
                logger.info("[pipeline:%s] [%d/%d] Editing OK for section %d", tag, idx + 1, total, pos)
                await emit("edit_done", {"section": pos})
            except Exception as e:
                logger.error("[pipeline:%s] [%d/%d] Editing FAILED for section %d: %s", tag, idx + 1, total, pos, e)
                vw_failed.add(pos)

        async with async_session() as session:
            await update_checkpoint(job_id, CHECKPOINT_EDITING, session)

    # ------------------------------------------------------------------
    # Phase 5: Determine final status
    # ------------------------------------------------------------------
    all_failed = research_failed | vw_failed
    if len(all_failed) == 0:
        final_status = "completed"
    elif len(all_failed) >= total:
        final_status = "failed"
    else:
        final_status = "completed_partial"

    # Persist final status to both course and job
    async with async_session() as session:
        from app.agent_service import update_course_status
        await update_course_status(course_id, final_status, session)

    async with async_session() as session:
        await update_job_status(job_id, final_status, session)

    async with async_session() as session:
        await update_checkpoint(job_id, CHECKPOINT_DONE, session)

    await emit("pipeline_complete", {
        "status": final_status,
        "completed_count": total - len(all_failed),
        "failed_count": len(all_failed),
    })

    logger.info(
        "[pipeline:%s] === PIPELINE FINISHED === status=%s, total=%d, failed=%s",
        tag, final_status, total,
        sorted(all_failed) if all_failed else "none",
    )

    return final_status


# ---------------------------------------------------------------------------
# Backward-compatible stubs (consumed by courses.py / main.py until Task 4)
# ---------------------------------------------------------------------------

_active_tasks: set[asyncio.Task] = set()


def get_pipeline_status(course_id: str):
    """Deprecated: returns None. Pipeline status is now in pipeline_jobs table."""
    return None


def start_pipeline(
    course_id: str,
    provider: str,
    model: str,
    credentials: dict,
    extra_fields: dict | None = None,
    search_provider: str = "",
    search_credentials: dict | None = None,
) -> None:
    """Deprecated: no-op. Use the worker process + PipelineJob row instead."""
    logger.warning("start_pipeline() is deprecated — pipeline jobs are now DB-driven")
