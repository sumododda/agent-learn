import logging
import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.agent_service import generate_outline
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

    # Stub: fill sections with placeholder content
    course.status = "generating"
    await session.flush()

    for section in course.sections:
        section.content = (
            f"# {section.title}\n\n"
            f"This is placeholder content for the {section.title} section."
        )

    course.status = "completed"
    await session.commit()

    # Reload
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
