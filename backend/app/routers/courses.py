import logging
import uuid
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.agent_service import (
    generate_outline,
    get_blackboard,
)
from app.auth import get_current_user
from app.config import settings
from app.database import SessionDep
from app.models import Course, EvidenceCard, Section, ResearchBrief
from app.schemas import (
    BlackboardResponse,
    CourseCreate,
    CourseResponse,
    EvidenceCardResponse,
    GenerateResponse,
    RegenerateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Course CRUD endpoints
# ---------------------------------------------------------------------------


@router.post("/courses", response_model=CourseResponse)
async def create_course(
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
            status_code=500, detail=f"Failed to generate outline: {str(e)}"
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
    if course.user_id and course.user_id != user_id:
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

    # Trigger the generate-course task via Trigger.dev REST API
    run_id: str | None = None
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{settings.TRIGGER_API_URL}/api/v1/tasks/generate-course/trigger",
                headers={
                    "Authorization": f"Bearer {settings.TRIGGER_SECRET_KEY}",
                    "Content-Type": "application/json",
                },
                json={"payload": {"courseId": str(course_id)}},
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
            run_id = data["id"]
    except Exception as e:
        logger.error("Failed to trigger Trigger.dev task: %s", e)
        # Revert status so the user can retry
        course.status = "outline_ready"
        await session.commit()
        raise HTTPException(
            status_code=502,
            detail=f"Failed to trigger generation pipeline: {str(e)}",
        )

    # Reload course with sections and return with run_id
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
        run_id=run_id,
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
    if course.user_id and course.user_id != user_id:
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
            status_code=500, detail=f"Failed to regenerate outline: {str(e)}"
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
    if course.user_id and course.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this course")
    return course


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
    if course.user_id and course.user_id != user_id:
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
    if course.user_id and course.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this course")

    bb = await get_blackboard(course_id, session)
    if bb is None:
        return None
    return bb


