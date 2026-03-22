import pytest
from unittest.mock import patch, AsyncMock

from tests.conftest import TEST_PROVIDER, TEST_MODEL, TEST_CREDENTIALS, TEST_EXTRA_FIELDS


def _mock_get_user_provider():
    """Return a coroutine mock that resolves to test provider params."""
    return AsyncMock(return_value=(TEST_PROVIDER, TEST_MODEL, TEST_CREDENTIALS, TEST_EXTRA_FIELDS))


def _mock_get_user_search_provider():
    """Return a coroutine mock that resolves to empty search provider."""
    return AsyncMock(return_value=("", {}))



@pytest.mark.anyio
async def test_create_course(client, mock_outline_with_briefs):
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
    assert data["status"] == "outline_ready"
    assert data["ungrounded"] is False
    assert len(data["sections"]) == 3
    assert data["sections"][0]["title"] == "Introduction"


@pytest.mark.anyio
async def test_create_course_ungrounded(client, mock_outline_with_briefs):
    mock_return = (mock_outline_with_briefs, True)
    with (
        patch("app.routers.courses._get_user_provider", new_callable=_mock_get_user_provider),
        patch("app.routers.courses._get_user_search_provider", new_callable=_mock_get_user_search_provider),
        patch("app.routers.courses.generate_outline", new_callable=AsyncMock, return_value=mock_return),
    ):
        response = await client.post("/api/courses", json={"topic": "Python basics"})

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "outline_ready"
    assert data["ungrounded"] is True


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
async def test_generate_course_creates_pipeline_job(client, mock_outline_with_briefs):
    """POST /generate creates a PipelineJob row and returns its job_id."""
    mock_return = (mock_outline_with_briefs, False)
    with (
        patch("app.routers.courses._get_user_provider", new_callable=_mock_get_user_provider),
        patch("app.routers.courses._get_user_search_provider", new_callable=_mock_get_user_search_provider),
        patch("app.routers.courses.generate_outline", new_callable=AsyncMock, return_value=mock_return),
    ):
        create_response = await client.post("/api/courses", json={"topic": "Testing"})

    course_id = create_response.json()["id"]

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
async def test_generate_course_requires_outline_ready(client, mock_outline_with_briefs):
    """POST /generate rejects courses not in 'outline_ready' status."""
    mock_return = (mock_outline_with_briefs, False)
    with (
        patch("app.routers.courses._get_user_provider", new_callable=_mock_get_user_provider),
        patch("app.routers.courses._get_user_search_provider", new_callable=_mock_get_user_search_provider),
        patch("app.routers.courses.generate_outline", new_callable=AsyncMock, return_value=mock_return),
    ):
        create_response = await client.post("/api/courses", json={"topic": "Testing"})

    course_id = create_response.json()["id"]

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
async def test_regenerate_course(client, mock_outline_with_briefs):
    mock_return = (mock_outline_with_briefs, False)
    with (
        patch("app.routers.courses._get_user_provider", new_callable=_mock_get_user_provider),
        patch("app.routers.courses._get_user_search_provider", new_callable=_mock_get_user_search_provider),
        patch("app.routers.courses.generate_outline", new_callable=AsyncMock, return_value=mock_return),
    ):
        create_response = await client.post("/api/courses", json={"topic": "Testing"})

    course_id = create_response.json()["id"]

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
