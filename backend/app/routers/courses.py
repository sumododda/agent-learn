import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import SessionDep
from app.models import Course, Section
from app.schemas import CourseCreate, CourseResponse, GenerateResponse

router = APIRouter()


@router.post("/courses", response_model=CourseResponse)
async def create_course(body: CourseCreate, session: SessionDep):
    # Create course
    course = Course(topic=body.topic, instructions=body.instructions)
    session.add(course)
    await session.flush()

    # Stub: create mock outline sections
    mock_sections = [
        {
            "position": 1,
            "title": "Introduction",
            "summary": f"Introduction to {body.topic}",
        },
        {
            "position": 2,
            "title": "Core Concepts",
            "summary": f"Key concepts in {body.topic}",
        },
        {
            "position": 3,
            "title": "Practical Applications",
            "summary": f"Applying {body.topic} in practice",
        },
    ]
    for s in mock_sections:
        section = Section(course_id=course.id, **s)
        session.add(section)

    await session.commit()

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
