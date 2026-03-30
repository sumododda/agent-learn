import asyncio
import json as json_mod
import logging
import time
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import key_cache
from app.agent_service import (
    generate_outline,
    get_blackboard,
)
from app.auth import get_current_user, get_user_from_query_token
from app.database import SessionDep, async_session
from app.limiter import limiter
from app.models import Course, EvidenceCard, LearnerProgress, PipelineJob, ProviderConfig, Section, ResearchBrief
from app.pdf_export import generate_course_pdf, sanitize_pdf_filename
from app.schemas import (
    BlackboardResponse,
    CourseCreate,
    CourseResponse,
    CourseWithProgressResponse,
    EvidenceCardResponse,
    GenerateResponse,
    PipelineStatusResponse,
    ProgressResponse,
    ProgressUpdateRequest,
    RegenerateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# SSE discovery feed buffers (in-process, append-only)
# ---------------------------------------------------------------------------
_feed_events: dict[str, list[dict]] = {}
_feed_queues: dict[str, asyncio.Queue] = {}
_MAX_FEED_ENTRIES = 200  # max concurrent course feeds
_DISCOVER_REPLAY_POLL_SECONDS = 0.2
_DISCOVER_REPLAY_TIMEOUT_SECONDS = 300
_DISCOVER_HEARTBEAT_SECONDS = 10.0


def _format_outline_snapshot(sections: list[Section]) -> str:
    lines = []
    for section in sorted(sections, key=lambda s: s.position):
        lines.append(f"{section.position}. {section.title}: {section.summary}")
    return "\n".join(lines)


def _build_regeneration_instructions(
    original_instructions: str | None,
    current_sections: list[Section],
    body: RegenerateRequest,
) -> str | None:
    parts: list[str] = []

    if original_instructions:
        parts.append("<original_instructions>")
        parts.append(original_instructions)
        parts.append("</original_instructions>")

    parts.append("Revise the existing outline instead of creating a brand-new one.")
    parts.append("Keep the current section count, order, and untouched sections stable unless the overall feedback explicitly asks for broader restructuring.")
    parts.append("Apply per-section comments only to the referenced sections.")
    parts.append("If a section comment asks to remove or replace a topic, treat that as a targeted rewrite of that section rather than a rewrite of the full outline.")

    if body.overall_comment:
        parts.append("<overall_feedback>")
        parts.append(body.overall_comment)
        parts.append("</overall_feedback>")

    if body.section_comments:
        parts.append("<section_feedback>")
        for sc in sorted(body.section_comments, key=lambda s: s.position):
            parts.append(f"Section {sc.position}: {sc.comment}")
        parts.append("</section_feedback>")

    if current_sections:
        parts.append("<current_outline>")
        parts.append(_format_outline_snapshot(current_sections))
        parts.append("</current_outline>")

    return "\n".join(parts) if parts else None


async def _ensure_cache(user_id: str, session) -> None:
    """Lazy-load provider credentials into cache if not already present."""
    if key_cache.get_default(user_id) is not None:
        return
    from app.routers.auth_routes import _load_provider_keys
    uid = uuid.UUID(user_id)
    await _load_provider_keys(user_id, uid, session)


async def _get_user_provider(user_id: str, session) -> tuple[str, str, dict, dict]:
    """Get (provider, model, api_key, extra_fields) for the user's OpenRouter config."""
    from app.provider_service import DEFAULT_MODEL
    await _ensure_cache(user_id, session)
    default = key_cache.get_default(user_id)
    if default is None:
        raise HTTPException(400, detail="no_provider_configured")
    provider, creds = default
    result = await session.execute(
        select(ProviderConfig).where(
            ProviderConfig.user_id == uuid.UUID(user_id),
            ProviderConfig.provider == provider,
        )
    )
    pc = result.scalar_one_or_none()
    extra_fields = pc.extra_fields if pc else {}
    model = (extra_fields or {}).get("model") or DEFAULT_MODEL
    return provider, model, creds, extra_fields or {}


async def _get_user_search_provider(user_id: str, session) -> tuple[str, dict]:
    """Get (search_provider, search_credentials) from cache. Returns ("", {}) if none configured."""
    await _ensure_cache(user_id, session)
    result = key_cache.get_default_search(user_id)
    if result is None:
        return ("", {})
    return result


# ---------------------------------------------------------------------------
# Course CRUD endpoints
# ---------------------------------------------------------------------------


@router.post("/courses")
@limiter.limit("5/minute")
async def create_course(
    request: Request,
    body: CourseCreate,
    session: SessionDep,
    user_id: str = Depends(get_current_user),
    stream: bool = Query(False),
):
    # Create course row first with "researching" status
    academic_search_dict = body.academic_search.model_dump() if body.academic_search and body.academic_search.enabled else None
    course = Course(
        topic=body.topic,
        instructions=body.instructions,
        status="researching",
        user_id=uuid.UUID(user_id),
        academic_search=academic_search_dict,
    )
    session.add(course)
    await session.commit()  # commit (not flush) so background task's own session can see it

    # ---- SSE streaming path (also used as background task for non-stream) ----
    course_id = course.id
    course_id_str = str(course_id)

    if len(_feed_events) >= _MAX_FEED_ENTRIES:
        stale = [k for k in _feed_events if k not in _feed_queues]
        for k in stale:
            _feed_events.pop(k, None)

    queue: asyncio.Queue = asyncio.Queue()
    _feed_queues[course_id_str] = queue
    _feed_events[course_id_str] = []

    async def emit(event_type: str, data: dict) -> None:
        payload = {"event": event_type, "data": data}
        buf = _feed_events.get(course_id_str)
        if buf is not None:
            buf.append(payload)
        await queue.put(payload)

    async def run_discovery() -> None:
        # Emit created event immediately so the frontend can extract course_id
        await emit("created", {"course_id": course_id_str})

        try:
            async with async_session() as sess:
                # Fetch provider credentials using a fresh session
                provider, model, creds, extra_fields = await _get_user_provider(user_id, sess)
                search_provider, search_creds = await _get_user_search_provider(user_id, sess)

                outline_with_briefs, ungrounded, _ = await generate_outline(
                    body.topic, body.instructions, provider, model, creds, extra_fields,
                    search_provider, search_creds,
                    on_event=emit, user_id=user_id,
                    academic_options=academic_search_dict,
                )

                # Reload course in this session
                result = await sess.execute(
                    select(Course).where(Course.id == course_id)
                )
                db_course = result.scalar_one_or_none()
                if db_course is None:
                    logger.info("Course %s deleted during generation, aborting", course_id)
                    return
                db_course.ungrounded = ungrounded

                for section_data in outline_with_briefs.sections:
                    section = Section(
                        course_id=course_id,
                        position=section_data.position,
                        title=section_data.title,
                        summary=section_data.summary,
                    )
                    sess.add(section)

                if not ungrounded:
                    discovery_brief = ResearchBrief(
                        course_id=course_id,
                        section_position=None,
                        questions=[],
                        source_policy={},
                        findings="Discovery research completed successfully",
                    )
                    sess.add(discovery_brief)

                for brief_item in outline_with_briefs.research_briefs:
                    research_brief = ResearchBrief(
                        course_id=course_id,
                        section_position=brief_item.section_position,
                        questions=brief_item.questions,
                        source_policy=brief_item.source_policy,
                    )
                    sess.add(research_brief)

                db_course.status = "outline_ready"
                await sess.commit()

                # Emit section events for each created section
                for section_data in outline_with_briefs.sections:
                    await emit("section", {
                        "position": section_data.position,
                        "title": section_data.title,
                        "summary": section_data.summary,
                    })

                await emit("complete", {"course_id": course_id_str, "status": "outline_ready"})

        except Exception as e:
            logger.error("SSE discovery failed for course %s: %s", course_id, e)
            try:
                async with async_session() as sess:
                    result = await sess.execute(
                        select(Course).where(Course.id == course_id)
                    )
                    db_course = result.scalar_one_or_none()
                    if db_course is not None:
                        db_course.status = "failed"
                        await sess.commit()
                    else:
                        logger.info("Course %s already deleted, skipping status update", course_id)
            except Exception:
                logger.exception("Failed to mark course %s as failed", course_id)
            await emit("error", {"message": "Course creation failed. Please try again."})

        finally:
            await queue.put(None)  # sentinel
            _feed_queues.pop(course_id_str, None)

            def _cleanup_buffer():
                _feed_events.pop(course_id_str, None)

            try:
                loop = asyncio.get_event_loop()
                loop.call_later(300, _cleanup_buffer)
            except RuntimeError:
                _cleanup_buffer()

    asyncio.create_task(run_discovery())

    if not stream:
        # Return course immediately — discover page picks up SSE events
        return CourseResponse(
            id=course.id,
            topic=course.topic,
            instructions=course.instructions,
            status=course.status,
            ungrounded=False,
            sections=[],
            pipeline_status=None,
        )

    async def event_generator():
        while True:
            payload = await queue.get()
            if payload is None:
                return
            yield f"event: {payload['event']}\ndata: {json_mod.dumps(payload['data'])}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Referrer-Policy": "no-referrer",
        },
    )


@router.post("/courses/{course_id}/generate")
@limiter.limit("3/hour")
async def generate_course(
    request: Request,
    course_id: uuid.UUID,
    session: SessionDep,
    user_id: str = Depends(get_current_user),
):
    result = await session.execute(
        select(Course)
        .options(selectinload(Course.sections))
        .where(Course.id == course_id)
    )
    course = result.scalar_one_or_none()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    if str(course.user_id) != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this course")

    # Status guard: only allow generation when outline is ready
    if course.status != "outline_ready":
        raise HTTPException(
            status_code=400,
            detail=f"Course status is '{course.status}'; generation requires 'outline_ready'",
        )

    # Check if user already has an active pipeline job
    active_result = await session.execute(
        select(PipelineJob).where(
            PipelineJob.user_id == uuid.UUID(user_id),
            PipelineJob.status.in_(["pending", "claimed", "running"]),
        )
    )
    if active_result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="A pipeline job is already active")

    # Transition to "generating" before creating the job
    course.status = "generating"
    await session.flush()

    # Get provider credentials from cache
    provider, model, _creds, extra_fields = await _get_user_provider(user_id, session)
    search_provider, _search_creds = await _get_user_search_provider(user_id, session)

    # Create a PipelineJob row for the worker to pick up
    config = {
        "provider": provider,
        "model": model,
        "extra_fields": extra_fields,
        "search_provider": search_provider,
    }
    if course.academic_search:
        config["academic_search"] = course.academic_search
    job = PipelineJob(
        course_id=course_id,
        user_id=uuid.UUID(user_id),
        config=config,
    )
    session.add(job)
    await session.commit()

    return {"job_id": str(job.id)}


@router.post("/courses/{course_id}/resume")
async def resume_course(
    course_id: uuid.UUID,
    session: SessionDep,
    user_id: str = Depends(get_current_user),
):
    # Verify course exists and user owns it
    result = await session.execute(
        select(Course).where(Course.id == course_id)
    )
    course = result.scalar_one_or_none()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    if str(course.user_id) != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this course")

    # Only allow resume when course is stale
    if course.status != "stale":
        raise HTTPException(
            status_code=400,
            detail=f"Course status is '{course.status}'; resume requires 'stale'",
        )

    # Find the most recent stale PipelineJob for this course
    stale_result = await session.execute(
        select(PipelineJob)
        .where(
            PipelineJob.course_id == course_id,
            PipelineJob.status == "stale",
        )
        .order_by(PipelineJob.created_at.desc())
        .limit(1)
    )
    stale_job = stale_result.scalar_one_or_none()

    checkpoint = stale_job.checkpoint if stale_job else 0

    # Mark old job as failed
    if stale_job:
        stale_job.status = "failed"
        stale_job.error = "Superseded by resume"
        await session.flush()

    # Validate user still has provider config
    provider, model, _creds, extra_fields = await _get_user_provider(user_id, session)
    search_provider, _search_creds = await _get_user_search_provider(user_id, session)

    # Create new PipelineJob with checkpoint copied from stale job
    resume_config = {
        "provider": provider,
        "model": model,
        "extra_fields": extra_fields,
        "search_provider": search_provider,
    }
    if course.academic_search:
        resume_config["academic_search"] = course.academic_search
    new_job = PipelineJob(
        course_id=course_id,
        user_id=uuid.UUID(user_id),
        checkpoint=checkpoint,
        config=resume_config,
    )
    session.add(new_job)

    # Transition course back to generating
    course.status = "generating"
    await session.commit()

    return {"job_id": str(new_job.id), "checkpoint": new_job.checkpoint}


@router.post("/courses/{course_id}/regenerate", response_model=CourseResponse)
@limiter.limit("5/hour")
async def regenerate_course(
    request: Request,
    course_id: uuid.UUID,
    body: RegenerateRequest,
    session: SessionDep,
    user_id: str = Depends(get_current_user),
):
    result = await session.execute(
        select(Course)
        .options(selectinload(Course.sections))
        .where(Course.id == course_id)
    )
    course = result.scalar_one_or_none()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    if str(course.user_id) != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this course")

    current_sections = list(sorted(course.sections, key=lambda s: s.position))
    enhanced_instructions = _build_regeneration_instructions(
        course.instructions,
        current_sections,
        body,
    )

    # Delete old sections
    for section in course.sections:
        await session.delete(section)
    await session.flush()

    # Delete old research briefs
    old_briefs_result = await session.execute(
        select(ResearchBrief).where(ResearchBrief.course_id == course.id)
    )
    for brief in old_briefs_result.scalars().all():
        await session.delete(brief)
    await session.flush()

    # Set status to researching for the new outline generation
    course.status = "researching"
    await session.flush()

    try:
        # Get provider credentials from cache
        provider, model, creds, extra_fields = await _get_user_provider(user_id, session)
        search_provider, search_creds = await _get_user_search_provider(user_id, session)

        outline_with_briefs, ungrounded, _ = await generate_outline(
            course.topic, enhanced_instructions, provider, model, creds, extra_fields,
            search_provider, search_creds, user_id=user_id, current_outline=current_sections,
        )

        course.ungrounded = ungrounded

        for section_data in outline_with_briefs.sections:
            section = Section(
                course_id=course.id,
                position=section_data.position,
                title=section_data.title,
                summary=section_data.summary,
            )
            session.add(section)

        # Save discovery brief if research succeeded
        if not ungrounded:
            discovery_brief = ResearchBrief(
                course_id=course.id,
                section_position=None,
                questions=[],
                source_policy={},
                findings="Discovery research completed successfully (regenerate)",
            )
            session.add(discovery_brief)

        # Save per-section research briefs
        for brief_item in outline_with_briefs.research_briefs:
            research_brief = ResearchBrief(
                course_id=course.id,
                section_position=brief_item.section_position,
                questions=brief_item.questions,
                source_policy=brief_item.source_policy,
            )
            session.add(research_brief)

        course.status = "outline_ready"
        await session.commit()

    except Exception as e:
        logger.error("Failed to regenerate outline: %s", e)
        course.status = "failed"
        await session.commit()
        raise HTTPException(
            status_code=500, detail="Internal server error"
        )

    result = await session.execute(
        select(Course)
        .options(selectinload(Course.sections))
        .where(Course.id == course.id)
    )
    course = result.scalar_one()
    return course


@router.get("/courses", response_model=list[CourseResponse])
async def list_courses(
    session: SessionDep,
    user_id: str = Depends(get_current_user),
):
    user_uuid = uuid.UUID(user_id)
    result = await session.execute(
        select(Course)
        .options(selectinload(Course.sections))
        .where(Course.user_id == user_uuid)
        .order_by(Course.created_at.desc())
    )
    return result.scalars().all()


@router.get("/courses/{course_id}", response_model=CourseResponse)
async def get_course(
    course_id: uuid.UUID,
    session: SessionDep,
    user_id: str = Depends(get_current_user),
):
    result = await session.execute(
        select(Course)
        .options(selectinload(Course.sections))
        .where(Course.id == course_id)
    )
    course = result.scalar_one_or_none()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    if str(course.user_id) != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this course")

    # Include pipeline status from the latest PipelineJob in DB
    job_result = await session.execute(
        select(PipelineJob)
        .where(PipelineJob.course_id == course_id)
        .order_by(PipelineJob.created_at.desc())
        .limit(1)
    )
    job = job_result.scalar_one_or_none()

    resp = CourseResponse.model_validate(course)
    if job is not None:
        checkpoint_stage_map = {
            0: "planning",
            1: "researching",
            2: "writing",
            3: "editing",
            4: "complete",
        }
        stage = checkpoint_stage_map.get(job.checkpoint, "unknown")
        if job.status in ("failed", "stale"):
            stage = job.status
        elif job.status in ("completed", "completed_partial"):
            stage = "complete"
        resp.pipeline_status = PipelineStatusResponse(
            stage=stage,
            error=job.error,
        )
    return resp


@router.get("/courses/{course_id}/export/pdf")
async def export_course_pdf(
    course_id: uuid.UUID,
    session: SessionDep,
    user_id: str = Depends(get_current_user),
):
    result = await session.execute(
        select(Course)
        .options(selectinload(Course.sections))
        .where(Course.id == course_id)
    )
    course = result.scalar_one_or_none()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    if str(course.user_id) != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this course")
    if course.status != "completed":
        raise HTTPException(status_code=400, detail="Course must be completed before export")

    try:
        pdf_bytes = generate_course_pdf(CourseResponse.model_validate(course))
    except HTTPException:
        raise
    except Exception:
        logger.exception("PDF export failed for course %s", course_id)
        raise HTTPException(status_code=500, detail="Export failed")

    filename = f"{sanitize_pdf_filename(course.topic)}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/courses/{course_id}", status_code=204)
async def delete_course(
    course_id: uuid.UUID,
    session: SessionDep,
    user_id: str = Depends(get_current_user),
):
    result = await session.execute(
        select(Course).where(Course.id == course_id)
    )
    course = result.scalar_one_or_none()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    if str(course.user_id) != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Cancel any active pipeline jobs so the worker stops processing
    active_jobs = await session.execute(
        select(PipelineJob).where(
            PipelineJob.course_id == course_id,
            PipelineJob.status.in_(["pending", "claimed", "running"]),
        )
    )
    for job in active_jobs.scalars().all():
        job.status = "cancelled"
    await session.commit()  # commit cancellation first so worker sees it

    # Clean up in-memory SSE feed buffers
    cid_str = str(course_id)
    _feed_events.pop(cid_str, None)
    q = _feed_queues.pop(cid_str, None)
    if q:
        await q.put(None)  # signal stream to close

    # Re-fetch and delete (cascade removes all related data)
    result2 = await session.execute(
        select(Course).where(Course.id == course_id)
    )
    course = result2.scalar_one_or_none()
    if course:
        await session.delete(course)
        await session.commit()


# ---------------------------------------------------------------------------
# SSE pipeline stream endpoint
# ---------------------------------------------------------------------------

CHECKPOINT_STAGE_MAP = {
    0: "planning",
    1: "researching",
    2: "writing",
    3: "editing",
    4: "complete",
}


@router.get("/courses/{course_id}/pipeline/stream")
async def pipeline_stream(course_id: uuid.UUID, token: str = Query(...)):
    """Server-Sent Events stream for pipeline progress.

    Polls the pipeline_jobs table every 2 seconds and emits events:
    - status: stage/checkpoint updates
    - complete: pipeline finished successfully
    - stale: pipeline became stale (worker died)
    - error: pipeline failed

    Max duration: 30 minutes, then the stream closes.
    """
    user_id = await get_user_from_query_token(token)

    # Verify course ownership before starting the stream
    async with async_session() as session:
        result = await session.execute(
            select(Course).where(Course.id == course_id)
        )
        course = result.scalar_one_or_none()
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")
        if str(course.user_id) != user_id:
            raise HTTPException(status_code=403, detail="Not authorized")

    async def event_generator():
        max_iterations = 900  # 30 minutes at 2-second intervals
        last_checkpoint = -1
        last_status = ""
        last_event_count = 0

        for _ in range(max_iterations):
            try:
                async with async_session() as session:
                    result = await session.execute(
                        select(PipelineJob)
                        .where(PipelineJob.course_id == course_id)
                        .order_by(PipelineJob.created_at.desc())
                        .limit(1)
                    )
                    job = result.scalar_one_or_none()

                if job is None:
                    yield f"event: status\ndata: {json_mod.dumps({'stage': 'waiting', 'checkpoint': 0})}\n\n"
                    await asyncio.sleep(2)
                    continue

                # Stream granular pipeline events from the JSONB array
                events = job.events or []
                new_events = events[last_event_count:]
                last_event_count = len(events)
                for entry in new_events:
                    yield f"event: {entry['event']}\ndata: {json_mod.dumps(entry['data'])}\n\n"

                checkpoint = job.checkpoint
                status = job.status

                # Only emit stage-level events when something changed
                if checkpoint != last_checkpoint or status != last_status:
                    last_checkpoint = checkpoint
                    last_status = status

                    stage = CHECKPOINT_STAGE_MAP.get(checkpoint, "unknown")

                    if status in ("completed", "completed_partial"):
                        yield f"event: complete\ndata: {json_mod.dumps({'stage': 'complete', 'checkpoint': checkpoint, 'status': status})}\n\n"
                        return
                    elif status == "stale":
                        yield f"event: stale\ndata: {json_mod.dumps({'stage': 'stale', 'checkpoint': checkpoint})}\n\n"
                        return
                    elif status == "failed":
                        yield f"event: error\ndata: {json_mod.dumps({'stage': 'failed', 'checkpoint': checkpoint, 'error': job.error})}\n\n"
                        return
                    else:
                        yield f"event: status\ndata: {json_mod.dumps({'stage': stage, 'checkpoint': checkpoint})}\n\n"

            except Exception:
                logger.exception("SSE poll error for course %s", course_id)
                yield f"event: error\ndata: {json_mod.dumps({'stage': 'error', 'error': 'Internal polling error'})}\n\n"
                return

            await asyncio.sleep(2)

        # Max duration reached
        yield f"event: error\ndata: {json_mod.dumps({'stage': 'timeout', 'error': 'Stream timed out after 30 minutes'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Referrer-Policy": "no-referrer",
        },
    )


# ---------------------------------------------------------------------------
# SSE discovery replay endpoint
# ---------------------------------------------------------------------------


@router.get("/courses/{course_id}/discover/stream")
async def discover_stream(course_id: uuid.UUID, token: str = Query(...)):
    """Replay or follow the live discovery feed for a course.

    1. If a buffer exists, replay all buffered events.
    2. If discovery is still in progress, poll for new events until terminal.
    3. If neither buffer nor queue, fall back to DB state without faking completion.
    """
    user_id = await get_user_from_query_token(token)

    # Verify course ownership
    async with async_session() as session:
        result = await session.execute(
            select(Course)
            .options(selectinload(Course.sections))
            .where(Course.id == course_id)
        )
        course = result.scalar_one_or_none()
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")
        if str(course.user_id) != user_id:
            raise HTTPException(status_code=403, detail="Not authorized")

    course_id_str = str(course_id)

    async def event_generator():
        # Case 1 & 2: Buffer exists (may still be in progress)
        if course_id_str in _feed_events:
            read_index = 0
            max_polls = int(_DISCOVER_REPLAY_TIMEOUT_SECONDS / _DISCOVER_REPLAY_POLL_SECONDS)
            last_heartbeat = time.monotonic()

            for _ in range(max_polls):
                # Read any new events since last check
                events = _feed_events.get(course_id_str)
                if events is None:
                    # Buffer was cleaned up mid-read
                    return

                while read_index < len(events):
                    payload = events[read_index]
                    read_index += 1
                    yield f"event: {payload['event']}\ndata: {json_mod.dumps(payload['data'])}\n\n"
                    last_heartbeat = time.monotonic()

                    # Stop on terminal events
                    if payload["event"] in ("complete", "error"):
                        return

                # If no queue exists, discovery is done — all events have been replayed
                if course_id_str not in _feed_queues:
                    return

                if time.monotonic() - last_heartbeat >= _DISCOVER_HEARTBEAT_SECONDS:
                    yield ": keep-alive\n\n"
                    last_heartbeat = time.monotonic()

                await asyncio.sleep(_DISCOVER_REPLAY_POLL_SECONDS)

            # Timeout
            yield f"event: error\ndata: {json_mod.dumps({'message': 'Stream timed out'})}\n\n"
            return

        # Case 3: No buffer — fall back to DB state and wait for a terminal status
        max_polls = int(_DISCOVER_REPLAY_TIMEOUT_SECONDS / _DISCOVER_REPLAY_POLL_SECONDS)
        last_heartbeat = time.monotonic()
        emitted_section_positions: set[int] = set()

        for _ in range(max_polls):
            async with async_session() as session:
                result = await session.execute(
                    select(Course)
                    .options(selectinload(Course.sections))
                    .where(Course.id == course_id)
                )
                db_course = result.scalar_one_or_none()

            if db_course is None:
                yield f"event: error\ndata: {json_mod.dumps({'message': 'Course not found'})}\n\n"
                return

            for section in sorted(db_course.sections, key=lambda s: s.position):
                if section.position in emitted_section_positions:
                    continue
                emitted_section_positions.add(section.position)
                payload = {
                    "position": section.position,
                    "title": section.title,
                    "summary": section.summary,
                }
                yield f"event: section\ndata: {json_mod.dumps(payload)}\n\n"
                last_heartbeat = time.monotonic()

            if db_course.status == "outline_ready":
                yield f"event: complete\ndata: {json_mod.dumps({'course_id': course_id_str, 'status': db_course.status})}\n\n"
                return

            if db_course.status == "failed":
                yield f"event: error\ndata: {json_mod.dumps({'message': 'Course creation failed. Please try again.'})}\n\n"
                return

            if time.monotonic() - last_heartbeat >= _DISCOVER_HEARTBEAT_SECONDS:
                yield ": keep-alive\n\n"
                last_heartbeat = time.monotonic()

            await asyncio.sleep(_DISCOVER_REPLAY_POLL_SECONDS)

        yield f"event: error\ndata: {json_mod.dumps({'message': 'Stream timed out'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Referrer-Policy": "no-referrer",
        },
    )


# ---------------------------------------------------------------------------
# New M2 endpoints: evidence, blackboard
# ---------------------------------------------------------------------------


@router.get(
    "/courses/{course_id}/evidence",
    response_model=list[EvidenceCardResponse],
)
async def get_evidence(
    course_id: uuid.UUID,
    session: SessionDep,
    user_id: str = Depends(get_current_user),
    section: Optional[int] = Query(None, description="Filter by section position"),
):
    """Return evidence cards for a course, optionally filtered by section."""
    # Verify course exists and user owns it
    course_result = await session.execute(
        select(Course).where(Course.id == course_id)
    )
    course = course_result.scalar_one_or_none()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    if str(course.user_id) != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this course")

    query = select(EvidenceCard).where(EvidenceCard.course_id == course_id)
    if section is not None:
        query = query.where(EvidenceCard.section_position == section)
    query = query.order_by(
        EvidenceCard.section_position, EvidenceCard.created_at
    )

    result = await session.execute(query)
    return result.scalars().all()


@router.get(
    "/courses/{course_id}/blackboard",
    response_model=BlackboardResponse | None,
)
async def get_course_blackboard(
    course_id: uuid.UUID,
    session: SessionDep,
    user_id: str = Depends(get_current_user),
):
    """Return the current blackboard state for a course."""
    # Verify course exists and user owns it
    course_result = await session.execute(
        select(Course).where(Course.id == course_id)
    )
    course = course_result.scalar_one_or_none()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    if str(course.user_id) != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this course")

    bb = await get_blackboard(course_id, session)
    if bb is None:
        return None
    return bb


# ---------------------------------------------------------------------------
# Learner progress endpoints
# ---------------------------------------------------------------------------


@router.get("/courses/{course_id}/progress", response_model=ProgressResponse | None)
async def get_progress(
    course_id: uuid.UUID,
    session: SessionDep,
    user_id: str = Depends(get_current_user),
):
    """Get the current user's progress for a course. Returns null if no progress exists."""
    course_result = await session.execute(
        select(Course).where(Course.id == course_id)
    )
    course = course_result.scalar_one_or_none()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    if str(course.user_id) != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this course")

    user_uuid = uuid.UUID(user_id)
    result = await session.execute(
        select(LearnerProgress).where(
            LearnerProgress.user_id == user_uuid,
            LearnerProgress.course_id == course_id,
        )
    )
    progress = result.scalar_one_or_none()
    if progress is None:
        return None
    return progress


@router.post("/courses/{course_id}/progress", response_model=ProgressResponse)
async def update_progress(
    course_id: uuid.UUID,
    body: ProgressUpdateRequest,
    session: SessionDep,
    user_id: str = Depends(get_current_user),
):
    """Upsert learner progress for a course.

    - current_section: set the current reading position
    - completed_section: add a section position to the completed list
    Always updates last_accessed_at.
    """
    # Verify course exists and user owns it
    course_result = await session.execute(
        select(Course).where(Course.id == course_id)
    )
    course = course_result.scalar_one_or_none()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    if str(course.user_id) != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this course")

    # Fetch existing progress or create new
    user_uuid = uuid.UUID(user_id)
    result = await session.execute(
        select(LearnerProgress).where(
            LearnerProgress.user_id == user_uuid,
            LearnerProgress.course_id == course_id,
        )
    )
    progress = result.scalar_one_or_none()

    if progress is None:
        progress = LearnerProgress(
            user_id=uuid.UUID(user_id),
            course_id=course_id,
            current_section=body.current_section if body.current_section is not None else 0,
            completed_sections=[],
        )
        session.add(progress)
    else:
        if body.current_section is not None:
            progress.current_section = body.current_section

    # Append completed_section if provided and not already present
    if body.completed_section is not None:
        existing = list(progress.completed_sections or [])
        if body.completed_section not in existing:
            existing.append(body.completed_section)
            progress.completed_sections = existing

    # Force last_accessed_at update
    progress.last_accessed_at = datetime.now()

    await session.commit()
    await session.refresh(progress)
    return progress


@router.get("/me/courses", response_model=list[CourseWithProgressResponse])
async def list_my_courses_with_progress(
    session: SessionDep,
    user_id: str = Depends(get_current_user),
):
    """List the current user's courses with progress data, sorted by last accessed."""
    user_uuid = uuid.UUID(user_id)

    # Get all courses for the user
    courses_result = await session.execute(
        select(Course)
        .options(selectinload(Course.sections))
        .where(Course.user_id == user_uuid)
    )
    courses = courses_result.scalars().all()

    # Get all progress records for the user
    progress_result = await session.execute(
        select(LearnerProgress).where(LearnerProgress.user_id == user_uuid)
    )
    progress_map = {
        p.course_id: p for p in progress_result.scalars().all()
    }

    # Build response with progress info, paired with sort key
    items: list[tuple[datetime, CourseWithProgressResponse]] = []
    for course in courses:
        progress = progress_map.get(course.id)
        progress_resp = ProgressResponse(
            current_section=progress.current_section,
            completed_sections=progress.completed_sections or [],
            last_accessed_at=progress.last_accessed_at,
        ) if progress else None

        course_data = CourseWithProgressResponse(
            id=course.id,
            topic=course.topic,
            instructions=course.instructions,
            status=course.status,
            ungrounded=course.ungrounded,
            sections=course.sections,
            progress=progress_resp,
        )
        # Sort key: last_accessed_at from progress, falling back to course created_at
        sort_key = progress.last_accessed_at if progress else course.created_at
        items.append((sort_key, course_data))

    items.sort(key=lambda pair: pair[0], reverse=True)
    return [item[1] for item in items]
