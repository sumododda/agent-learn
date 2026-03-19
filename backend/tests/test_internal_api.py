"""Tests for Phase 7 (Milestone 3): Internal API endpoints.

Tests cover:
- All 6 internal endpoints return correct responses with valid token
- Endpoints return 401 without token
- Endpoints return 401 with wrong token
- Mocked LLM/Tavily calls (no real external calls)
"""

import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.main import app
from app.config import settings
from app.database import get_session
from app.models import (
    Base,
    Blackboard,
    Course,
    EvidenceCard,
    ResearchBrief,
    Section,
)

# Known token for tests -- we patch settings to match this
INTERNAL_TOKEN = "test-internal-token-secret"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def internal_db():
    """Create a fresh in-memory DB; do NOT override get_current_user
    (internal endpoints don't use it). Patches INTERNAL_API_TOKEN so the
    verify_internal_token dependency works."""
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
    # Do NOT override get_current_user -- internal endpoints don't use it
    yield session_factory

    settings.INTERNAL_API_TOKEN = original_token
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    app.dependency_overrides.clear()


@pytest.fixture
async def internal_client():
    """HTTP test client for internal endpoints."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def course_for_internal(internal_db):
    """Create a course with sections, research briefs, and evidence cards
    suitable for testing the internal endpoints."""
    async with internal_db() as session:
        course = Course(
            topic="Internal API Test",
            status="outline_ready",
            user_id="test-user-id",
        )
        session.add(course)
        await session.commit()

        # Create sections
        for i in range(1, 4):
            section = Section(
                course_id=course.id,
                position=i,
                title=f"Section {i}",
                summary=f"Summary for section {i}",
            )
            session.add(section)
        await session.commit()

        # Create research briefs per section
        for i in range(1, 4):
            brief = ResearchBrief(
                course_id=course.id,
                section_position=i,
                questions=[f"Question {i}-1", f"Question {i}-2"],
                source_policy={"preferred_tiers": [1, 2]},
            )
            session.add(brief)
        await session.commit()

        # Create evidence cards for section 1 (needed for verify, write, edit)
        for j in range(1, 4):
            card = EvidenceCard(
                course_id=course.id,
                section_position=1,
                claim=f"Claim {j}",
                source_url=f"https://example.com/{j}",
                source_title=f"Source {j}",
                source_tier=j,
                passage=f"Passage for claim {j}",
                retrieved_date=date.today(),
                confidence=0.9,
                caveat=None,
                explanation=f"Explanation {j}",
                verified=True,
                verification_note=f"Verified card {j}",
            )
            session.add(card)
        await session.commit()

        # Create blackboard (needed for write and edit)
        bb = Blackboard(course_id=course.id)
        session.add(bb)
        await session.commit()

        # Add content to section 1 so edit endpoint can work
        from sqlalchemy import select

        result = await session.execute(
            select(Section).where(
                Section.course_id == course.id,
                Section.position == 1,
            )
        )
        sec1 = result.scalar_one()
        sec1.content = "## Section 1\n\nDraft content for editing [1]."
        await session.commit()

        return course


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _auth_header():
    """Return the X-Internal-Token header dict."""
    return {"X-Internal-Token": INTERNAL_TOKEN}


# ---------------------------------------------------------------------------
# Tests: discover-and-plan
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_discover_and_plan_success(
    internal_db, internal_client, course_for_internal
):
    """POST /api/internal/discover-and-plan returns sections and briefs."""
    course = course_for_internal

    with patch(
        "app.routers.internal.run_discover_and_plan",
        new_callable=AsyncMock,
        return_value={
            "sections": [
                {"id": str(uuid.uuid4()), "position": 1, "title": "Intro", "summary": "Getting started"},
            ],
            "research_briefs": [
                {"id": str(uuid.uuid4()), "section_position": 1, "questions": ["Q1"], "source_policy": {}},
            ],
            "ungrounded": False,
        },
    ):
        response = await internal_client.post(
            "/api/internal/discover-and-plan",
            json={"course_id": str(course.id)},
            headers=_auth_header(),
        )

    assert response.status_code == 200
    data = response.json()
    assert "sections" in data
    assert "research_briefs" in data
    assert len(data["sections"]) == 1
    assert data["sections"][0]["title"] == "Intro"


@pytest.mark.anyio
async def test_discover_and_plan_no_token(internal_db, internal_client, course_for_internal):
    """POST /api/internal/discover-and-plan without token returns 401."""
    course = course_for_internal
    response = await internal_client.post(
        "/api/internal/discover-and-plan",
        json={"course_id": str(course.id)},
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_discover_and_plan_wrong_token(internal_db, internal_client, course_for_internal):
    """POST /api/internal/discover-and-plan with wrong token returns 401."""
    course = course_for_internal
    response = await internal_client.post(
        "/api/internal/discover-and-plan",
        json={"course_id": str(course.id)},
        headers={"X-Internal-Token": "wrong-token-value"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Tests: research-section
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_research_section_success(internal_db, internal_client, course_for_internal):
    """POST /api/internal/research-section returns evidence cards."""
    course = course_for_internal

    with patch(
        "app.routers.internal.run_research_section",
        new_callable=AsyncMock,
        return_value={
            "evidence_cards": [
                {
                    "id": str(uuid.uuid4()),
                    "section_position": 1,
                    "claim": "Test claim",
                    "source_url": "https://example.com",
                    "source_title": "Example",
                    "source_tier": 1,
                    "passage": "Test passage",
                    "retrieved_date": str(date.today()),
                    "confidence": 0.9,
                    "caveat": None,
                    "explanation": "Test explanation",
                    "verified": False,
                },
            ],
        },
    ):
        response = await internal_client.post(
            "/api/internal/research-section",
            json={"course_id": str(course.id), "section_position": 1},
            headers=_auth_header(),
        )

    assert response.status_code == 200
    data = response.json()
    assert "evidence_cards" in data
    assert len(data["evidence_cards"]) == 1
    assert data["evidence_cards"][0]["claim"] == "Test claim"


@pytest.mark.anyio
async def test_research_section_no_token(internal_db, internal_client, course_for_internal):
    """POST /api/internal/research-section without token returns 401."""
    course = course_for_internal
    response = await internal_client.post(
        "/api/internal/research-section",
        json={"course_id": str(course.id), "section_position": 1},
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_research_section_wrong_token(internal_db, internal_client, course_for_internal):
    """POST /api/internal/research-section with wrong token returns 401."""
    course = course_for_internal
    response = await internal_client.post(
        "/api/internal/research-section",
        json={"course_id": str(course.id), "section_position": 1},
        headers={"X-Internal-Token": "bad-token"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Tests: verify-section
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_verify_section_success(internal_db, internal_client, course_for_internal):
    """POST /api/internal/verify-section returns verification result."""
    course = course_for_internal

    with patch(
        "app.routers.internal.run_verify_section",
        new_callable=AsyncMock,
        return_value={
            "verification_result": {
                "cards_verified": 2,
                "cards_total": 3,
                "needs_more_research": False,
                "gaps": [],
            },
        },
    ):
        response = await internal_client.post(
            "/api/internal/verify-section",
            json={"course_id": str(course.id), "section_position": 1},
            headers=_auth_header(),
        )

    assert response.status_code == 200
    data = response.json()
    assert "verification_result" in data
    assert data["verification_result"]["cards_verified"] == 2
    assert data["verification_result"]["needs_more_research"] is False


@pytest.mark.anyio
async def test_verify_section_no_token(internal_db, internal_client, course_for_internal):
    """POST /api/internal/verify-section without token returns 401."""
    course = course_for_internal
    response = await internal_client.post(
        "/api/internal/verify-section",
        json={"course_id": str(course.id), "section_position": 1},
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_verify_section_wrong_token(internal_db, internal_client, course_for_internal):
    """POST /api/internal/verify-section with wrong token returns 401."""
    course = course_for_internal
    response = await internal_client.post(
        "/api/internal/verify-section",
        json={"course_id": str(course.id), "section_position": 1},
        headers={"X-Internal-Token": "nope"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Tests: write-section
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_write_section_success(internal_db, internal_client, course_for_internal):
    """POST /api/internal/write-section returns content and citations."""
    course = course_for_internal

    with patch(
        "app.routers.internal.run_write_section",
        new_callable=AsyncMock,
        return_value={
            "content": "## Section 1\n\nGenerated content [1].",
            "citations": [
                {"number": 1, "claim": "Claim 1", "source_url": "https://example.com/1", "source_title": "Source 1"},
            ],
        },
    ):
        response = await internal_client.post(
            "/api/internal/write-section",
            json={"course_id": str(course.id), "section_position": 1},
            headers=_auth_header(),
        )

    assert response.status_code == 200
    data = response.json()
    assert "content" in data
    assert "citations" in data
    assert "Generated content" in data["content"]


@pytest.mark.anyio
async def test_write_section_no_token(internal_db, internal_client, course_for_internal):
    """POST /api/internal/write-section without token returns 401."""
    course = course_for_internal
    response = await internal_client.post(
        "/api/internal/write-section",
        json={"course_id": str(course.id), "section_position": 1},
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_write_section_wrong_token(internal_db, internal_client, course_for_internal):
    """POST /api/internal/write-section with wrong token returns 401."""
    course = course_for_internal
    response = await internal_client.post(
        "/api/internal/write-section",
        json={"course_id": str(course.id), "section_position": 1},
        headers={"X-Internal-Token": "invalid"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Tests: edit-section
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_edit_section_success(internal_db, internal_client, course_for_internal):
    """POST /api/internal/edit-section returns edited content and blackboard updates."""
    course = course_for_internal

    with patch(
        "app.routers.internal.run_edit_section",
        new_callable=AsyncMock,
        return_value={
            "edited_content": "## Section 1\n\nEdited content [1].",
            "blackboard_updates": {
                "new_glossary_terms": {"test": {"definition": "A test", "defined_in_section": 1}},
                "new_concept_ownership": {"testing": 1},
                "topics_covered": ["testing"],
                "key_points_summary": "Testing is important.",
                "new_sources": [],
            },
        },
    ):
        response = await internal_client.post(
            "/api/internal/edit-section",
            json={"course_id": str(course.id), "section_position": 1},
            headers=_auth_header(),
        )

    assert response.status_code == 200
    data = response.json()
    assert "edited_content" in data
    assert "blackboard_updates" in data
    assert "Edited content" in data["edited_content"]
    assert "test" in data["blackboard_updates"]["new_glossary_terms"]


@pytest.mark.anyio
async def test_edit_section_no_token(internal_db, internal_client, course_for_internal):
    """POST /api/internal/edit-section without token returns 401."""
    course = course_for_internal
    response = await internal_client.post(
        "/api/internal/edit-section",
        json={"course_id": str(course.id), "section_position": 1},
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_edit_section_wrong_token(internal_db, internal_client, course_for_internal):
    """POST /api/internal/edit-section with wrong token returns 401."""
    course = course_for_internal
    response = await internal_client.post(
        "/api/internal/edit-section",
        json={"course_id": str(course.id), "section_position": 1},
        headers={"X-Internal-Token": "wrong"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Tests: set-course-status
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_set_course_status_success(internal_db, internal_client, course_for_internal):
    """POST /api/internal/set-course-status updates course status."""
    course = course_for_internal

    response = await internal_client.post(
        "/api/internal/set-course-status",
        json={"course_id": str(course.id), "status": "completed"},
        headers=_auth_header(),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["course_id"] == str(course.id)


@pytest.mark.anyio
async def test_set_course_status_invalid_status(internal_db, internal_client, course_for_internal):
    """POST /api/internal/set-course-status rejects invalid status values."""
    course = course_for_internal

    response = await internal_client.post(
        "/api/internal/set-course-status",
        json={"course_id": str(course.id), "status": "banana"},
        headers=_auth_header(),
    )

    assert response.status_code == 400


@pytest.mark.anyio
async def test_set_course_status_no_token(internal_db, internal_client, course_for_internal):
    """POST /api/internal/set-course-status without token returns 401."""
    course = course_for_internal
    response = await internal_client.post(
        "/api/internal/set-course-status",
        json={"course_id": str(course.id), "status": "completed"},
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_set_course_status_wrong_token(internal_db, internal_client, course_for_internal):
    """POST /api/internal/set-course-status with wrong token returns 401."""
    course = course_for_internal
    response = await internal_client.post(
        "/api/internal/set-course-status",
        json={"course_id": str(course.id), "status": "completed"},
        headers={"X-Internal-Token": "fake"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Tests: nonexistent course
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_discover_and_plan_course_not_found(internal_db, internal_client):
    """POST /api/internal/discover-and-plan with nonexistent course_id returns 404."""
    fake_id = str(uuid.uuid4())

    with patch(
        "app.routers.internal.run_discover_and_plan",
        new_callable=AsyncMock,
        side_effect=ValueError(f"Course {fake_id} not found"),
    ):
        response = await internal_client.post(
            "/api/internal/discover-and-plan",
            json={"course_id": fake_id},
            headers=_auth_header(),
        )

    assert response.status_code == 404


@pytest.mark.anyio
async def test_set_course_status_not_found(internal_db, internal_client):
    """POST /api/internal/set-course-status with nonexistent course_id returns 404."""
    fake_id = str(uuid.uuid4())

    response = await internal_client.post(
        "/api/internal/set-course-status",
        json={"course_id": fake_id, "status": "completed"},
        headers=_auth_header(),
    )

    assert response.status_code == 404
