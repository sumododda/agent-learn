import uuid
from contextlib import asynccontextmanager

import pytest
from unittest.mock import patch, AsyncMock

from tests.conftest import TEST_PROVIDER, TEST_MODEL, TEST_CREDENTIALS, TEST_EXTRA_FIELDS, TEST_USER_UUID


def _mock_get_user_provider():
    """Return a coroutine mock that resolves to test provider params."""
    return AsyncMock(return_value=(TEST_PROVIDER, TEST_MODEL, TEST_CREDENTIALS, TEST_EXTRA_FIELDS))


def _mock_get_user_search_provider():
    """Return a coroutine mock that resolves to empty search provider."""
    return AsyncMock(return_value=("", {}))


# ---------------------------------------------------------------------------
# Helper: create a course with sections directly in the DB
# ---------------------------------------------------------------------------


async def _create_course_with_sections(client, setup_db):
    """Insert a course + 3 sections directly into the test DB and return the course id."""
    from app.database import get_session
    from app.main import app
    from app.models import Course, Section

    # We need to get a session via the override
    session_gen = app.dependency_overrides[get_session]()
    session = await session_gen.__anext__()

    course = Course(
        topic="Testing",
        status="outline_ready",
        user_id=TEST_USER_UUID,
    )
    session.add(course)
    await session.flush()

    for i, (title, summary) in enumerate(
        [("Introduction", "Getting started"), ("Core Concepts", "Key ideas"), ("Practice", "Hands-on exercises")],
        start=1,
    ):
        section = Section(
            course_id=course.id,
            position=i,
            title=title,
            summary=summary,
        )
        session.add(section)
    await session.commit()

    try:
        await session_gen.__anext__()
    except StopAsyncIteration:
        pass

    return str(course.id)


# ---------------------------------------------------------------------------
# Tests: Course creation (now returns "researching" immediately)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_course(client, mock_outline_with_briefs):
    """POST /api/courses now returns status='researching' immediately."""
    mock_return = (mock_outline_with_briefs, False)
    with (
        patch("app.routers.courses._get_user_provider", new_callable=_mock_get_user_provider),
        patch("app.routers.courses._get_user_search_provider", new_callable=_mock_get_user_search_provider),
        patch("app.routers.courses.generate_outline", new_callable=AsyncMock, return_value=mock_return),
    ):
        response = await client.post("/api/courses", json={"topic": "Python basics"})

    assert response.status_code == 200
    data = response.json()
    assert data["topic"] == "Python basics"
    # Course creation now kicks off a background discovery task and returns immediately
    assert data["status"] == "researching"
    assert data["ungrounded"] is False
    assert data["sections"] == []


@pytest.mark.anyio
async def test_create_course_ungrounded(client, mock_outline_with_briefs):
    """POST /api/courses always returns ungrounded=False in the immediate response."""
    mock_return = (mock_outline_with_briefs, True)
    with (
        patch("app.routers.courses._get_user_provider", new_callable=_mock_get_user_provider),
        patch("app.routers.courses._get_user_search_provider", new_callable=_mock_get_user_search_provider),
        patch("app.routers.courses.generate_outline", new_callable=AsyncMock, return_value=mock_return),
    ):
        response = await client.post("/api/courses", json={"topic": "Python basics"})

    assert response.status_code == 200
    data = response.json()
    # The immediate response always says researching/ungrounded=False;
    # the background task updates these later.
    assert data["status"] == "researching"
    assert data["ungrounded"] is False


@pytest.mark.anyio
async def test_create_course_passes_academic_search_context(client, mock_outline_with_briefs):
    mock_return = (mock_outline_with_briefs, False)
    with (
        patch("app.routers.courses._get_user_provider", new_callable=_mock_get_user_provider),
        patch("app.routers.courses._get_user_search_provider", new_callable=_mock_get_user_search_provider),
        patch("app.routers.courses.generate_outline", new_callable=AsyncMock, return_value=mock_return) as mock_generate_outline,
    ):
        response = await client.post(
            "/api/courses",
            json={
                "topic": "Python basics",
                "academic_search": {
                    "enabled": True,
                    "year_range": "10y",
                    "min_citations": 25,
                    "open_access_only": True,
                },
            },
        )

    assert response.status_code == 200
    call_kwargs = mock_generate_outline.await_args.kwargs
    assert call_kwargs["academic_options"] == {
        "enabled": True,
        "year_range": "10y",
        "min_citations": 25,
        "open_access_only": True,
    }


@pytest.mark.anyio
async def test_get_course_not_found(client):
    response = await client.get("/api/courses/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_create_and_get_course(client, mock_outline_with_briefs):
    mock_return = (mock_outline_with_briefs, False)
    with (
        patch("app.routers.courses._get_user_provider", new_callable=_mock_get_user_provider),
        patch("app.routers.courses._get_user_search_provider", new_callable=_mock_get_user_search_provider),
        patch("app.routers.courses.generate_outline", new_callable=AsyncMock, return_value=mock_return),
    ):
        create_response = await client.post("/api/courses", json={"topic": "Testing"})

    course_id = create_response.json()["id"]
    get_response = await client.get(f"/api/courses/{course_id}")
    assert get_response.status_code == 200
    assert get_response.json()["topic"] == "Testing"


@pytest.mark.anyio
async def test_discover_stream_does_not_fake_complete_while_researching(setup_db):
    from app.database import get_session
    from app.main import app
    from app.models import Course
    from app.routers.courses import discover_stream

    session_gen = app.dependency_overrides[get_session]()
    session = await session_gen.__anext__()

    course = Course(
        topic="Streaming",
        status="researching",
        user_id=TEST_USER_UUID,
    )
    session.add(course)
    await session.commit()

    try:
        await session_gen.__anext__()
    except StopAsyncIteration:
        pass

    @asynccontextmanager
    async def test_async_session():
        agen = app.dependency_overrides[get_session]()
        test_session = await agen.__anext__()
        try:
            yield test_session
        finally:
            try:
                await agen.__anext__()
            except StopAsyncIteration:
                pass

    with (
        patch("app.routers.courses.get_user_from_query_token", new_callable=AsyncMock, return_value=str(TEST_USER_UUID)),
        patch("app.routers.courses.async_session", test_async_session),
        patch("app.routers.courses._DISCOVER_REPLAY_POLL_SECONDS", 0.01),
        patch("app.routers.courses._DISCOVER_REPLAY_TIMEOUT_SECONDS", 0.03),
        patch("app.routers.courses._DISCOVER_HEARTBEAT_SECONDS", 0.01),
    ):
        response = await discover_stream(course.id, token="test-ticket")
        chunks: list[str] = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)

    body = "".join(chunks)
    assert "event: complete" not in body
    assert "event: error" in body


@pytest.mark.anyio
async def test_generate_course_creates_pipeline_job(setup_db, client, mock_outline_with_briefs):
    """POST /generate creates a PipelineJob row and returns its job_id."""
    # Create a course with outline_ready status directly in the DB
    course_id = await _create_course_with_sections(client, setup_db)

    with (
        patch("app.routers.courses._get_user_provider", new_callable=_mock_get_user_provider),
        patch("app.routers.courses._get_user_search_provider", new_callable=_mock_get_user_search_provider),
    ):
        gen_response = await client.post(f"/api/courses/{course_id}/generate")

    assert gen_response.status_code == 200
    data = gen_response.json()
    assert "job_id" in data
    # job_id should be a valid UUID string
    import uuid as _uuid
    _uuid.UUID(data["job_id"])


@pytest.mark.anyio
async def test_generate_course_requires_outline_ready(setup_db, client, mock_outline_with_briefs):
    """POST /generate rejects courses not in 'outline_ready' status."""
    course_id = await _create_course_with_sections(client, setup_db)

    # First generate call transitions to "generating"
    with (
        patch("app.routers.courses._get_user_provider", new_callable=_mock_get_user_provider),
        patch("app.routers.courses._get_user_search_provider", new_callable=_mock_get_user_search_provider),
    ):
        await client.post(f"/api/courses/{course_id}/generate")

    # Second call should fail because status is now "generating"
    gen_response = await client.post(f"/api/courses/{course_id}/generate")
    assert gen_response.status_code == 400


@pytest.mark.anyio
async def test_regenerate_course(setup_db, client, mock_outline_with_briefs):
    course_id = await _create_course_with_sections(client, setup_db)

    mock_return_2 = (mock_outline_with_briefs, False)
    with (
        patch("app.routers.courses._get_user_provider", new_callable=_mock_get_user_provider),
        patch("app.routers.courses._get_user_search_provider", new_callable=_mock_get_user_search_provider),
        patch("app.routers.courses.generate_outline", new_callable=AsyncMock, return_value=mock_return_2),
    ):
        regen_response = await client.post(
            f"/api/courses/{course_id}/regenerate",
            json={"overall_comment": "Add more detail", "section_comments": []},
        )

    assert regen_response.status_code == 200
    data = regen_response.json()
    assert data["status"] == "outline_ready"
    assert len(data["sections"]) == 3


@pytest.mark.anyio
async def test_regenerate_course_passes_current_outline_and_targeted_feedback(setup_db, client, mock_outline_with_briefs):
    course_id = await _create_course_with_sections(client, setup_db)

    # Add instructions to the course
    from app.database import get_session
    from app.main import app
    from app.models import Course

    session_gen = app.dependency_overrides[get_session]()
    session = await session_gen.__anext__()
    from sqlalchemy import select
    result = await session.execute(select(Course).where(Course.id == uuid.UUID(course_id)))
    course = result.scalar_one()
    course.instructions = "Beginner-friendly."
    await session.commit()
    try:
        await session_gen.__anext__()
    except StopAsyncIteration:
        pass

    mock_return = (mock_outline_with_briefs, False)
    with (
        patch("app.routers.courses._get_user_provider", new_callable=_mock_get_user_provider),
        patch("app.routers.courses._get_user_search_provider", new_callable=_mock_get_user_search_provider),
        patch("app.routers.courses.generate_outline", new_callable=AsyncMock, return_value=mock_return) as mock_generate,
    ):
        regen_response = await client.post(
            f"/api/courses/{course_id}/regenerate",
            json={
                "section_comments": [
                    {
                        "position": 3,
                        "comment": "Replace this section with security risks and adversarial examples.",
                    }
                ],
            },
        )

    assert regen_response.status_code == 200
    await_args = mock_generate.await_args
    assert await_args is not None
    assert await_args.kwargs["current_outline"][2].title == "Practice"
    assert "Revise the existing outline instead of creating a brand-new one." in await_args.args[1]
    assert "Section 3: Replace this section with security risks and adversarial examples." in await_args.args[1]
    assert "<current_outline>" in await_args.args[1]


@pytest.mark.anyio
async def test_get_evidence_empty(client, mock_outline_with_briefs):
    """GET /evidence returns empty list when no evidence cards exist."""
    mock_return = (mock_outline_with_briefs, False)
    with (
        patch("app.routers.courses._get_user_provider", new_callable=_mock_get_user_provider),
        patch("app.routers.courses._get_user_search_provider", new_callable=_mock_get_user_search_provider),
        patch("app.routers.courses.generate_outline", new_callable=AsyncMock, return_value=mock_return),
    ):
        create_response = await client.post("/api/courses", json={"topic": "Testing"})

    course_id = create_response.json()["id"]
    response = await client.get(f"/api/courses/{course_id}/evidence")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.anyio
async def test_get_evidence_not_found(client):
    """GET /evidence returns 404 for non-existent course."""
    response = await client.get(
        "/api/courses/00000000-0000-0000-0000-000000000000/evidence"
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_get_blackboard_none(client, mock_outline_with_briefs):
    """GET /blackboard returns null when no blackboard exists."""
    mock_return = (mock_outline_with_briefs, False)
    with (
        patch("app.routers.courses._get_user_provider", new_callable=_mock_get_user_provider),
        patch("app.routers.courses._get_user_search_provider", new_callable=_mock_get_user_search_provider),
        patch("app.routers.courses.generate_outline", new_callable=AsyncMock, return_value=mock_return),
    ):
        create_response = await client.post("/api/courses", json={"topic": "Testing"})

    course_id = create_response.json()["id"]
    response = await client.get(f"/api/courses/{course_id}/blackboard")
    assert response.status_code == 200
    assert response.json() is None


@pytest.mark.anyio
async def test_get_blackboard_not_found(client):
    """GET /blackboard returns 404 for non-existent course."""
    response = await client.get(
        "/api/courses/00000000-0000-0000-0000-000000000000/blackboard"
    )
    assert response.status_code == 404
