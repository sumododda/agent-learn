import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.agent_service import (
    generate_outline,
    get_blackboard,
)
from app.auth import get_current_user
from app.database import SessionDep
from app.limiter import limiter
from app.models import Course, EvidenceCard, LearnerProgress, Section, ResearchBrief
from app.pipeline import get_pipeline_status, start_pipeline
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
# Course CRUD endpoints
# ---------------------------------------------------------------------------


@router.post("/courses", response_model=CourseResponse)
@limiter.limit("5/minute")
async def create_course(
    request: Request,
    body: CourseCreate,
    session: SessionDep,
    user_id: str = Depends(get_current_user),
):
    # Create course row first with "researching" status
    course = Course(topic=body.topic, instructions=body.instructions, status="researching", user_id=user_id)
    session.add(course)
    await session.flush()

    try:
        # Generate outline via discovery research + planner
        # generate_outline now returns (CourseOutlineWithBriefs, ungrounded_flag)
        outline_with_briefs, ungrounded = await generate_outline(
            body.topic, body.instructions
        )

        # Set ungrounded flag if discovery research failed
        course.ungrounded = ungrounded

        # Create section rows from the structured outline
        for section_data in outline_with_briefs.sections:
            section = Section(
                course_id=course.id,
                position=section_data.position,
                title=section_data.title,
                summary=section_data.summary,
            )
            session.add(section)

        # Save discovery brief (section_position=null) if discovery succeeded
        if not ungrounded:
            discovery_brief = ResearchBrief(
                course_id=course.id,
                section_position=None,  # null = discovery brief
                questions=[],  # discovery brief has no per-section questions
                source_policy={},
                findings="Discovery research completed successfully",
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
        logger.error("Failed to generate outline: %s", e)
        course.status = "failed"
        await session.commit()
        raise HTTPException(
            status_code=500, detail="Internal server error"
        )

    # Reload with sections
    result = await session.execute(
        select(Course)
        .options(selectinload(Course.sections))
        .where(Course.id == course.id)
    )
    course = result.scalar_one()
    return course


@router.post("/courses/{course_id}/generate", response_model=GenerateResponse)
async def generate_course(
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
    if course.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this course")

    # Status guard: only allow generation when outline is ready
    if course.status != "outline_ready":
        raise HTTPException(
            status_code=400,
            detail=f"Course status is '{course.status}'; generation requires 'outline_ready'",
        )

    # Transition to "generating" before triggering the pipeline
    course.status = "generating"
    await session.commit()

    # Start the asyncio background pipeline
    start_pipeline(str(course_id))

    # Reload course with sections and return
    result = await session.execute(
        select(Course)
        .options(selectinload(Course.sections))
        .where(Course.id == course_id)
    )
    course = result.scalar_one()
    return GenerateResponse(
        id=course.id,
        status=course.status,
        sections=course.sections,
    )


@router.post("/courses/{course_id}/regenerate", response_model=CourseResponse)
async def regenerate_course(
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
    if course.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this course")

    # Build enhanced instructions from comments
    feedback_parts = []
    if course.instructions:
        feedback_parts.append(course.instructions)
    if body.overall_comment:
        feedback_parts.append(f"Feedback on previous outline: {body.overall_comment}")
    for sc in sorted(body.section_comments, key=lambda s: s.position):
        feedback_parts.append(f"Feedback on section {sc.position}: {sc.comment}")

    enhanced_instructions = "\n".join(feedback_parts) if feedback_parts else None

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
        outline_with_briefs, ungrounded = await generate_outline(
            course.topic, enhanced_instructions
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
    result = await session.execute(
        select(Course)
        .options(selectinload(Course.sections))
        .where(Course.user_id == user_id)
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
    if course.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this course")

    # Include pipeline status if a pipeline is running/completed
    pipeline = get_pipeline_status(str(course_id))
    resp = CourseResponse.model_validate(course)
    if pipeline is not None:
        resp.pipeline_status = PipelineStatusResponse(
            stage=pipeline.stage,
            section=pipeline.section,
            total=pipeline.total,
            error=pipeline.error,
        )
    return resp


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
    if course.user_id != user_id:
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
    if course.user_id != user_id:
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
    result = await session.execute(
        select(LearnerProgress).where(
            LearnerProgress.user_id == user_id,
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
    if course.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this course")

    # Fetch existing progress or create new
    result = await session.execute(
        select(LearnerProgress).where(
            LearnerProgress.user_id == user_id,
            LearnerProgress.course_id == course_id,
        )
    )
    progress = result.scalar_one_or_none()

    if progress is None:
        progress = LearnerProgress(
            user_id=user_id,
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
    # Get all courses for the user
    courses_result = await session.execute(
        select(Course)
        .options(selectinload(Course.sections))
        .where(Course.user_id == user_id)
    )
    courses = courses_result.scalars().all()

    # Get all progress records for the user
    progress_result = await session.execute(
        select(LearnerProgress).where(LearnerProgress.user_id == user_id)
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


