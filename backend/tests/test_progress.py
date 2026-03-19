"""Tests for Phase 7 (Milestone 3): Learner progress endpoints.

Tests cover:
- Creating progress for a course
- Updating progress (current_section, completed_section)
- GET progress returns correct data
- GET /api/me/courses returns courses with progress
- Progress is per-user (user A can't see user B's progress)
- Resume flow (current_section is returned correctly)
"""

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import event as sa_event, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.main import app
from app.database import get_session
from app.auth import get_current_user
from app.models import Base, Course, LearnerProgress, Section


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def progress_db():
    """Create a fresh in-memory DB with auth override (test-user-id)."""
    engine = create_async_engine("sqlite+aiosqlite://")

    @sa_event.listens_for(engine.sync_engine, "connect")
    def _set_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_current_user] = lambda: "test-user-id"
    yield session_factory

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    app.dependency_overrides.clear()


@pytest.fixture
async def progress_client():
    """HTTP test client for progress endpoints."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def course_with_sections(progress_db):
    """Create a completed course with 3 sections for progress testing."""
    async with progress_db() as session:
        course = Course(
            topic="Progress Test Course",
            status="completed",
            user_id="test-user-id",
        )
        session.add(course)
        await session.flush()

        for i in range(1, 4):
            section = Section(
                course_id=course.id,
                position=i,
                title=f"Section {i}",
                summary=f"Summary for section {i}",
                content=f"## Section {i}\n\nContent here.",
            )
            session.add(section)
        await session.commit()

        return course


# ---------------------------------------------------------------------------
# Tests: Creating progress
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_progress(progress_db, progress_client, course_with_sections):
    """POST /api/courses/{id}/progress creates progress record."""
    course = course_with_sections

    response = await progress_client.post(
        f"/api/courses/{course.id}/progress",
        json={"current_section": 1},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["current_section"] == 1
    assert data["completed_sections"] == []
    assert "last_accessed_at" in data


@pytest.mark.anyio
async def test_create_progress_with_completed_section(
    progress_db, progress_client, course_with_sections
):
    """POST /api/courses/{id}/progress with completed_section creates progress."""
    course = course_with_sections

    response = await progress_client.post(
        f"/api/courses/{course.id}/progress",
        json={"current_section": 2, "completed_section": 1},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["current_section"] == 2
    assert 1 in data["completed_sections"]


@pytest.mark.anyio
async def test_create_progress_default_section(
    progress_db, progress_client, course_with_sections
):
    """POST /api/courses/{id}/progress without current_section defaults to 0."""
    course = course_with_sections

    response = await progress_client.post(
        f"/api/courses/{course.id}/progress",
        json={"completed_section": 1},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["current_section"] == 0
    assert 1 in data["completed_sections"]


# ---------------------------------------------------------------------------
# Tests: Updating progress
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_update_current_section(progress_db, progress_client, course_with_sections):
    """Updating current_section moves the reading position."""
    course = course_with_sections

    # Create initial progress
    await progress_client.post(
        f"/api/courses/{course.id}/progress",
        json={"current_section": 1},
    )

    # Update to section 2
    response = await progress_client.post(
        f"/api/courses/{course.id}/progress",
        json={"current_section": 2},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["current_section"] == 2


@pytest.mark.anyio
async def test_update_completed_section_accumulates(
    progress_db, progress_client, course_with_sections
):
    """Each completed_section is appended to the list, not replaced."""
    course = course_with_sections

    # Create initial progress with section 1 completed
    await progress_client.post(
        f"/api/courses/{course.id}/progress",
        json={"current_section": 1, "completed_section": 1},
    )

    # Complete section 2
    response = await progress_client.post(
        f"/api/courses/{course.id}/progress",
        json={"current_section": 3, "completed_section": 2},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["current_section"] == 3
    assert 1 in data["completed_sections"]
    assert 2 in data["completed_sections"]


@pytest.mark.anyio
async def test_update_completed_section_no_duplicates(
    progress_db, progress_client, course_with_sections
):
    """Completing the same section twice does not create duplicates."""
    course = course_with_sections

    # Complete section 1
    await progress_client.post(
        f"/api/courses/{course.id}/progress",
        json={"current_section": 1, "completed_section": 1},
    )

    # Complete section 1 again
    response = await progress_client.post(
        f"/api/courses/{course.id}/progress",
        json={"completed_section": 1},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["completed_sections"].count(1) == 1


@pytest.mark.anyio
async def test_update_progress_updates_last_accessed(
    progress_db, progress_client, course_with_sections
):
    """Updating progress always updates last_accessed_at."""
    course = course_with_sections

    # Create initial progress
    resp1 = await progress_client.post(
        f"/api/courses/{course.id}/progress",
        json={"current_section": 1},
    )
    first_access = resp1.json()["last_accessed_at"]

    # Update progress
    resp2 = await progress_client.post(
        f"/api/courses/{course.id}/progress",
        json={"current_section": 2},
    )
    second_access = resp2.json()["last_accessed_at"]

    # last_accessed_at should be updated (or at least not earlier)
    assert second_access >= first_access


# ---------------------------------------------------------------------------
# Tests: GET progress
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_progress_returns_null_when_no_progress(
    progress_db, progress_client, course_with_sections
):
    """GET /api/courses/{id}/progress returns null when no progress exists."""
    course = course_with_sections

    response = await progress_client.get(
        f"/api/courses/{course.id}/progress"
    )

    assert response.status_code == 200
    assert response.json() is None


@pytest.mark.anyio
async def test_get_progress_returns_correct_data(
    progress_db, progress_client, course_with_sections
):
    """GET /api/courses/{id}/progress returns saved progress."""
    course = course_with_sections

    # Create progress
    await progress_client.post(
        f"/api/courses/{course.id}/progress",
        json={"current_section": 2, "completed_section": 1},
    )

    # Retrieve progress
    response = await progress_client.get(
        f"/api/courses/{course.id}/progress"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["current_section"] == 2
    assert 1 in data["completed_sections"]
    assert "last_accessed_at" in data


# ---------------------------------------------------------------------------
# Tests: GET /api/me/courses returns courses with progress
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_my_courses_includes_progress(
    progress_db, progress_client, course_with_sections
):
    """GET /api/me/courses returns courses with progress data."""
    course = course_with_sections

    # Create progress
    await progress_client.post(
        f"/api/courses/{course.id}/progress",
        json={"current_section": 2, "completed_section": 1},
    )

    response = await progress_client.get("/api/me/courses")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1

    course_data = data[0]
    assert course_data["topic"] == "Progress Test Course"
    assert course_data["progress"] is not None
    assert course_data["progress"]["current_section"] == 2
    assert 1 in course_data["progress"]["completed_sections"]


@pytest.mark.anyio
async def test_my_courses_without_progress(
    progress_db, progress_client, course_with_sections
):
    """GET /api/me/courses returns course with progress=null if no progress."""
    response = await progress_client.get("/api/me/courses")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["progress"] is None


@pytest.mark.anyio
async def test_my_courses_multiple_courses_sorted(progress_db, progress_client):
    """GET /api/me/courses sorts by last_accessed_at descending."""
    async with progress_db() as session:
        # Create two courses
        course_a = Course(
            topic="Course A",
            status="completed",
            user_id="test-user-id",
        )
        course_b = Course(
            topic="Course B",
            status="completed",
            user_id="test-user-id",
        )
        session.add_all([course_a, course_b])
        await session.flush()

        for c in [course_a, course_b]:
            section = Section(
                course_id=c.id,
                position=1,
                title="Sec 1",
                summary="Summary",
                content="Content",
            )
            session.add(section)
        await session.commit()
        a_id = course_a.id
        b_id = course_b.id

    # Access course A first
    await progress_client.post(
        f"/api/courses/{a_id}/progress",
        json={"current_section": 1},
    )
    # Access course B second (more recent)
    await progress_client.post(
        f"/api/courses/{b_id}/progress",
        json={"current_section": 1},
    )

    response = await progress_client.get("/api/me/courses")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    # Course B should be first (most recently accessed)
    assert data[0]["topic"] == "Course B"
    assert data[1]["topic"] == "Course A"


# ---------------------------------------------------------------------------
# Tests: Progress is per-user
# ---------------------------------------------------------------------------


@pytest.fixture
async def progress_db_user_b():
    """DB with auth override for a different user (user-b)."""
    engine = create_async_engine("sqlite+aiosqlite://")

    @sa_event.listens_for(engine.sync_engine, "connect")
    def _set_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_current_user] = lambda: "user-b"
    yield session_factory

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_progress_per_user_isolation(progress_db, progress_client):
    """User A's progress is not visible to user B (and vice versa)."""
    # Create a course owned by test-user-id
    async with progress_db() as session:
        course = Course(
            topic="Shared Course",
            status="completed",
            user_id="test-user-id",
        )
        session.add(course)
        await session.flush()
        section = Section(
            course_id=course.id,
            position=1,
            title="Sec 1",
            summary="Summary",
            content="Content",
        )
        session.add(section)
        await session.commit()
        course_id = course.id

    # Create progress for test-user-id (user A)
    await progress_client.post(
        f"/api/courses/{course_id}/progress",
        json={"current_section": 2, "completed_section": 1},
    )

    # Verify user A sees progress
    resp_a = await progress_client.get(f"/api/courses/{course_id}/progress")
    assert resp_a.status_code == 200
    assert resp_a.json() is not None
    assert resp_a.json()["current_section"] == 2

    # Now switch to user B
    app.dependency_overrides[get_current_user] = lambda: "user-b"

    # user B should not see user A's progress
    resp_b = await progress_client.get(f"/api/courses/{course_id}/progress")
    assert resp_b.status_code == 200
    assert resp_b.json() is None  # No progress for user B

    # Restore override
    app.dependency_overrides[get_current_user] = lambda: "test-user-id"


# ---------------------------------------------------------------------------
# Tests: Resume flow
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resume_flow(progress_db, progress_client, course_with_sections):
    """Full resume flow: read section 1, move to section 2, resume returns section 2."""
    course = course_with_sections

    # Start reading section 1
    await progress_client.post(
        f"/api/courses/{course.id}/progress",
        json={"current_section": 1},
    )

    # Complete section 1, move to section 2
    await progress_client.post(
        f"/api/courses/{course.id}/progress",
        json={"current_section": 2, "completed_section": 1},
    )

    # "Close" the app, then "resume" by fetching progress
    response = await progress_client.get(
        f"/api/courses/{course.id}/progress"
    )
    assert response.status_code == 200
    data = response.json()

    # Should resume at section 2
    assert data["current_section"] == 2
    assert 1 in data["completed_sections"]
    assert 2 not in data["completed_sections"]


@pytest.mark.anyio
async def test_resume_via_my_courses(progress_db, progress_client, course_with_sections):
    """GET /api/me/courses shows current_section for resume."""
    course = course_with_sections

    # Progress: reading section 3, completed 1 and 2
    await progress_client.post(
        f"/api/courses/{course.id}/progress",
        json={"current_section": 2, "completed_section": 1},
    )
    await progress_client.post(
        f"/api/courses/{course.id}/progress",
        json={"current_section": 3, "completed_section": 2},
    )

    response = await progress_client.get("/api/me/courses")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1

    progress = data[0]["progress"]
    assert progress["current_section"] == 3
    assert set(progress["completed_sections"]) == {1, 2}


# ---------------------------------------------------------------------------
# Tests: Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_progress_for_nonexistent_course(progress_db, progress_client):
    """POST /api/courses/{id}/progress returns 404 for nonexistent course."""
    fake_id = str(uuid.uuid4())
    response = await progress_client.post(
        f"/api/courses/{fake_id}/progress",
        json={"current_section": 1},
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_progress_empty_body(progress_db, progress_client, course_with_sections):
    """POST /api/courses/{id}/progress with empty body creates progress at section 0."""
    course = course_with_sections

    response = await progress_client.post(
        f"/api/courses/{course.id}/progress",
        json={},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["current_section"] == 0
    assert data["completed_sections"] == []
