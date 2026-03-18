import logging
import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.agent_service import generate_outline, generate_lessons
from app.database import SessionDep
from app.models import Course, Section
from app.schemas import CourseCreate, CourseResponse, GenerateResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/courses", response_model=CourseResponse)
async def create_course(body: CourseCreate, session: SessionDep):
    # Create course row first
    course = Course(topic=body.topic, instructions=body.instructions)
    session.add(course)
    await session.flush()

    try:
        # Generate outline via Deep Agents planner
        outline = await generate_outline(body.topic, body.instructions)

        # Create section rows from the structured outline
        for section_data in outline.sections:
            section = Section(
                course_id=course.id,
                position=section_data.position,
                title=section_data.title,
                summary=section_data.summary,
            )
            session.add(section)

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
async def generate_course(course_id: uuid.UUID, session: SessionDep):
    result = await session.execute(
        select(Course)
        .options(selectinload(Course.sections))
        .where(Course.id == course_id)
    )
    course = result.scalar_one_or_none()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    # Status guard: only allow generation when outline is ready
    if course.status != "outline_ready":
        raise HTTPException(
            status_code=400,
            detail=f"Course status is '{course.status}'; generation requires 'outline_ready'",
        )

    # Transition to "generating" before invoking the agent
    course.status = "generating"
    await session.commit()

    try:
        # Build section dicts with the full outline for the writer
        section_dicts = [
            {
                "position": s.position,
                "title": s.title,
                "summary": s.summary,
            }
            for s in sorted(course.sections, key=lambda s: s.position)
        ]

        # Invoke the writer agent
        content_result = await generate_lessons(
            topic=course.topic,
            instructions=course.instructions,
            sections=section_dicts,
        )

        # Match generated content to sections by position
        content_by_position = {
            sc.position: sc.content for sc in content_result.sections
        }

        matched_count = 0
        for section in course.sections:
            if section.position in content_by_position:
                section.content = content_by_position[section.position]
                matched_count += 1

        # Fail if the writer returned fewer sections than expected
        if matched_count < len(course.sections):
            logger.error(
                "Writer returned %d sections, expected %d",
                matched_count,
                len(course.sections),
            )
            course.status = "failed"
            await session.commit()
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Writer generated {matched_count} sections but "
                    f"{len(course.sections)} were expected"
                ),
            )

        course.status = "completed"
        await session.commit()

    except HTTPException:
        # Re-raise HTTPExceptions (like the matched_count check above)
        raise
    except Exception as e:
        logger.error("Failed to generate lessons: %s", e)
        course.status = "failed"
        await session.commit()
        raise HTTPException(
            status_code=500, detail=f"Failed to generate lessons: {str(e)}"
        )

    # Reload with sections
    result = await session.execute(
        select(Course)
        .options(selectinload(Course.sections))
        .where(Course.id == course.id)
    )
    course = result.scalar_one()
    return course


@router.get("/courses/{course_id}", response_model=CourseResponse)
async def get_course(course_id: uuid.UUID, session: SessionDep):
    result = await session.execute(
        select(Course)
        .options(selectinload(Course.sections))
        .where(Course.id == course_id)
    )
    course = result.scalar_one_or_none()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    return course
