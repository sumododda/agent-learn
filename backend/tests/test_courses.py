import pytest
from unittest.mock import patch, AsyncMock


@pytest.mark.anyio
async def test_create_course(client, mock_outline):
    with patch("app.routers.courses.generate_outline", new_callable=AsyncMock, return_value=mock_outline):
        response = await client.post("/api/courses", json={"topic": "Python basics"})

    assert response.status_code == 200
    data = response.json()
    assert data["topic"] == "Python basics"
    assert data["status"] == "outline_ready"
    assert len(data["sections"]) == 3
    assert data["sections"][0]["title"] == "Introduction"


@pytest.mark.anyio
async def test_get_course_not_found(client):
    response = await client.get("/api/courses/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_create_and_get_course(client, mock_outline):
    with patch("app.routers.courses.generate_outline", new_callable=AsyncMock, return_value=mock_outline):
        create_response = await client.post("/api/courses", json={"topic": "Testing"})

    course_id = create_response.json()["id"]
    get_response = await client.get(f"/api/courses/{course_id}")
    assert get_response.status_code == 200
    assert get_response.json()["topic"] == "Testing"


@pytest.mark.anyio
async def test_generate_course(client, mock_outline, mock_content):
    with patch("app.routers.courses.generate_outline", new_callable=AsyncMock, return_value=mock_outline):
        create_response = await client.post("/api/courses", json={"topic": "Testing"})

    course_id = create_response.json()["id"]

    with patch("app.routers.courses.generate_lessons", new_callable=AsyncMock, return_value=mock_content):
        gen_response = await client.post(f"/api/courses/{course_id}/generate")

    assert gen_response.status_code == 200
    data = gen_response.json()
    assert data["status"] == "completed"
    assert data["sections"][0]["content"] is not None
