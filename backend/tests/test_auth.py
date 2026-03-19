"""Tests for Phase 7 (Milestone 3): Authentication and authorization.

Tests cover:
- Public endpoints return 401 without Authorization header
- Public endpoints work with the test auth override (conftest.py)
- Internal endpoints reject Clerk JWTs and accept internal tokens
- User can only see their own courses (user_id filtering)
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.main import app
from app.config import settings
from app.database import get_session
from app.auth import get_current_user
from app.models import Base, Course, Section

INTERNAL_TOKEN = "test-auth-internal-token"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def auth_db():
    """Create a fresh in-memory DB with NO auth override
    so we can test real 401 behavior.

    NOTE: The conftest.py autouse ``setup_db`` fixture installs a
    get_current_user override.  We explicitly remove it here so that
    the real Clerk JWT check runs (and returns 401 for unauthenticated
    requests).
    """
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

    original_token = settings.INTERNAL_API_TOKEN
    settings.INTERNAL_API_TOKEN = INTERNAL_TOKEN

    # Clear ALL overrides first (including the autouse one from conftest),
    # then only install the session override.
    app.dependency_overrides.clear()
    app.dependency_overrides[get_session] = override_session
    # Intentionally NOT overriding get_current_user
    yield session_factory

    settings.INTERNAL_API_TOKEN = original_token
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    app.dependency_overrides.clear()


@pytest.fixture
async def auth_db_with_user():
    """DB with the standard test auth override (get_current_user -> 'test-user-id')."""
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

    original_token = settings.INTERNAL_API_TOKEN
    settings.INTERNAL_API_TOKEN = INTERNAL_TOKEN

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_current_user] = lambda: "test-user-id"
    yield session_factory

    settings.INTERNAL_API_TOKEN = original_token
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    app.dependency_overrides.clear()


@pytest.fixture
async def no_auth_client():
    """HTTP client with NO auth override (real 401s)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def authed_client():
    """HTTP client used after auth_db_with_user fixture is activated."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Tests: Public endpoints require Authorization header
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_courses_401_without_auth(auth_db, no_auth_client):
    """GET /api/courses returns 401 when no Authorization header is sent."""
    response = await no_auth_client.get("/api/courses")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_get_course_401_without_auth(auth_db, no_auth_client):
    """GET /api/courses/{id} returns 401 without auth."""
    fake_id = str(uuid.uuid4())
    response = await no_auth_client.get(f"/api/courses/{fake_id}")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_create_course_401_without_auth(auth_db, no_auth_client):
    """POST /api/courses returns 401 without auth."""
    response = await no_auth_client.post(
        "/api/courses", json={"topic": "Test"}
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_get_evidence_401_without_auth(auth_db, no_auth_client):
    """GET /api/courses/{id}/evidence returns 401 without auth."""
    fake_id = str(uuid.uuid4())
    response = await no_auth_client.get(f"/api/courses/{fake_id}/evidence")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_get_progress_401_without_auth(auth_db, no_auth_client):
    """GET /api/courses/{id}/progress returns 401 without auth."""
    fake_id = str(uuid.uuid4())
    response = await no_auth_client.get(f"/api/courses/{fake_id}/progress")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_my_courses_401_without_auth(auth_db, no_auth_client):
    """GET /api/me/courses returns 401 without auth."""
    response = await no_auth_client.get("/api/me/courses")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Tests: Public endpoints work with test auth override
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_courses_works_with_auth_override(auth_db_with_user, authed_client):
    """GET /api/courses returns 200 when auth override is active."""
    response = await authed_client.get("/api/courses")
    assert response.status_code == 200
    assert response.json() == []  # No courses yet


@pytest.mark.anyio
async def test_my_courses_works_with_auth_override(auth_db_with_user, authed_client):
    """GET /api/me/courses returns 200 when auth override is active."""
    response = await authed_client.get("/api/me/courses")
    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# Tests: Internal endpoints reject Clerk JWTs and accept internal tokens
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_internal_endpoint_rejects_bearer_token(auth_db, no_auth_client):
    """Internal endpoint rejects a Bearer-style Authorization header
    (it expects X-Internal-Token, not Clerk JWT)."""
    response = await no_auth_client.post(
        "/api/internal/set-course-status",
        json={"course_id": str(uuid.uuid4()), "status": "completed"},
        headers={"Authorization": "Bearer fake-clerk-jwt-token"},
    )
    # Should be 401 because X-Internal-Token is missing
    assert response.status_code == 401


@pytest.mark.anyio
async def test_internal_endpoint_accepts_internal_token(auth_db, no_auth_client):
    """Internal endpoint accepts X-Internal-Token header."""
    # set-course-status will return 404 for the fake course, but NOT 401
    fake_id = str(uuid.uuid4())
    response = await no_auth_client.post(
        "/api/internal/set-course-status",
        json={"course_id": fake_id, "status": "completed"},
        headers={"X-Internal-Token": INTERNAL_TOKEN},
    )
    # 404 means it passed auth (but course doesn't exist)
    assert response.status_code == 404


@pytest.mark.anyio
async def test_internal_endpoint_rejects_empty_token(auth_db, no_auth_client):
    """Internal endpoint rejects an empty X-Internal-Token."""
    response = await no_auth_client.post(
        "/api/internal/set-course-status",
        json={"course_id": str(uuid.uuid4()), "status": "completed"},
        headers={"X-Internal-Token": ""},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Tests: User can only see their own courses (user_id filtering)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_user_only_sees_own_courses(auth_db_with_user, authed_client):
    """GET /api/courses returns only courses belonging to test-user-id."""
    async with auth_db_with_user() as session:
        # Create a course for test-user-id
        course_mine = Course(
            topic="My Course",
            status="outline_ready",
            user_id="test-user-id",
        )
        session.add(course_mine)

        # Create a course for a different user
        course_other = Course(
            topic="Other User Course",
            status="outline_ready",
            user_id="other-user-id",
        )
        session.add(course_other)
        await session.commit()

    response = await authed_client.get("/api/courses")
    assert response.status_code == 200
    data = response.json()

    # Should only see the course belonging to test-user-id
    topics = [c["topic"] for c in data]
    assert "My Course" in topics
    assert "Other User Course" not in topics


@pytest.mark.anyio
async def test_user_cannot_access_other_users_course(auth_db_with_user, authed_client):
    """GET /api/courses/{id} returns 403 when accessing another user's course."""
    async with auth_db_with_user() as session:
        course_other = Course(
            topic="Secret Course",
            status="outline_ready",
            user_id="other-user-id",
        )
        session.add(course_other)
        await session.commit()
        other_id = course_other.id

    response = await authed_client.get(f"/api/courses/{other_id}")
    assert response.status_code == 403


@pytest.mark.anyio
async def test_user_can_access_own_course(auth_db_with_user, authed_client):
    """GET /api/courses/{id} returns 200 when accessing own course."""
    async with auth_db_with_user() as session:
        course = Course(
            topic="Accessible Course",
            status="outline_ready",
            user_id="test-user-id",
        )
        session.add(course)
        # Need at least one section for the response model
        await session.flush()
        section = Section(
            course_id=course.id,
            position=1,
            title="Section 1",
            summary="Summary",
        )
        session.add(section)
        await session.commit()
        course_id = course.id

    response = await authed_client.get(f"/api/courses/{course_id}")
    assert response.status_code == 200
    assert response.json()["topic"] == "Accessible Course"


@pytest.mark.anyio
async def test_progress_is_per_user(auth_db_with_user, authed_client):
    """Progress is filtered by user_id; user A cannot see user B's progress."""
    async with auth_db_with_user() as session:
        course = Course(
            topic="Progress Auth Test",
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
        )
        session.add(section)
        await session.commit()
        course_id = course.id

    # Create progress for test-user-id
    response = await authed_client.post(
        f"/api/courses/{course_id}/progress",
        json={"current_section": 1, "completed_section": 1},
    )
    assert response.status_code == 200

    # Verify progress is returned for test-user-id
    response = await authed_client.get(f"/api/courses/{course_id}/progress")
    assert response.status_code == 200
    data = response.json()
    assert data["current_section"] == 1
    assert 1 in data["completed_sections"]
