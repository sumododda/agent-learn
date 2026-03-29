"""Tests for Phase 8: Evidence and Blackboard API endpoints.

Tests cover:
- GET /api/courses/{id}/evidence — returns evidence cards, section filter
- GET /api/courses/{id}/blackboard — returns blackboard state
- Evidence card insertion and retrieval via API
- Blackboard creation and retrieval via API

Note: Pipeline-status endpoint tests were removed in Phase 4 (Milestone 3).
Real-time progress is now delivered via Trigger.dev metadata.
"""

import uuid
from datetime import date, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import event as sa_event

from app.main import app
from app.models import Base, Blackboard, Course, EvidenceCard, Section, User
from app.database import get_session
from app.auth import get_current_user

# Deterministic test user UUID
TEST_USER_UUID = uuid.UUID("00000000-0000-0000-0000-bbbbbbbbbbbb")
TEST_USER_ID = str(TEST_USER_UUID)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def evidence_db():
    """Create a fresh in-memory DB with the get_session dependency overridden."""
    engine = create_async_engine("sqlite+aiosqlite://")

    @sa_event.listens_for(engine.sync_engine, "connect")
    def _set_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # Create a User row so FK constraints are satisfied
    async with session_factory() as session:
        user = User(id=TEST_USER_UUID, email="evidence@test.com", password_hash="hashed")
        session.add(user)
        await session.commit()

    async def override_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_current_user] = lambda: TEST_USER_ID
    yield session_factory

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    app.dependency_overrides.clear()


@pytest.fixture
async def api_client():
    """Create an async HTTP test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def course_with_evidence(evidence_db):
    """Create a course with sections and evidence cards in the DB."""
    async with evidence_db() as session:
        # Create course
        course = Course(topic="Python Testing", status="completed", user_id=TEST_USER_UUID)
        session.add(course)
        await session.commit()

        # Create sections
        for i in range(1, 4):
            section = Section(
                course_id=course.id,
                position=i,
                title=f"Section {i}",
                summary=f"Summary {i}",
                content=f"## Section {i}\n\nContent for section {i}.",
            )
            session.add(section)
        await session.commit()

        # Create evidence cards for sections 1 and 2
        for sec in [1, 2]:
            for j in range(1, 4):
                card = EvidenceCard(
                    course_id=course.id,
                    section_position=sec,
                    claim=f"Claim {sec}-{j}",
                    source_url=f"https://example.com/s{sec}/{j}",
                    source_title=f"Source {sec}-{j}",
                    source_tier=j,  # 1, 2, 3
                    passage=f"Passage for claim {sec}-{j}",
                    retrieved_date=date.today(),
                    confidence=0.9 - (j * 0.1),
                    caveat=f"Caveat {sec}-{j}" if j == 2 else None,
                    explanation=f"Explanation {sec}-{j}",
                    verified=j <= 2,  # first 2 verified, third not
                    verification_note=f"Note {sec}-{j}" if j <= 2 else None,
                )
                session.add(card)
        await session.commit()

        return course


@pytest.fixture
async def course_with_blackboard(evidence_db):
    """Create a course with a populated blackboard."""
    async with evidence_db() as session:
        course = Course(topic="Python Blackboard", status="completed", user_id=TEST_USER_UUID)
        session.add(course)
        await session.commit()

        # Add a section
        section = Section(
            course_id=course.id,
            position=1,
            title="Intro",
            summary="Introduction",
        )
        session.add(section)
        await session.commit()

        # Create populated blackboard
        bb = Blackboard(
            course_id=course.id,
            glossary={
                "variable": {
                    "definition": "A named storage location",
                    "defined_in_section": 1,
                }
            },
            concept_ownership={"variables": 1, "loops": 2},
            coverage_map={"all_topics": ["variables", "assignment"]},
            key_points={"0": "Variables store data in memory."},
            source_log=[
                {"url": "https://docs.python.org", "title": "Python Docs"}
            ],
            open_questions=["How does garbage collection work?"],
        )
        session.add(bb)
        await session.commit()

        return course, bb


# ---------------------------------------------------------------------------
# Tests: GET /api/courses/{id}/evidence
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_evidence_returns_all_cards(
    evidence_db, api_client, course_with_evidence
):
    """GET /evidence returns all evidence cards for a course."""
    course = course_with_evidence
    response = await api_client.get(f"/api/courses/{course.id}/evidence")

    assert response.status_code == 200
    data = response.json()

    # 3 cards per section * 2 sections = 6 total
    assert len(data) == 6

    # Verify card structure
    card = data[0]
    assert "id" in card
    assert "claim" in card
    assert "source_url" in card
    assert "source_title" in card
    assert "source_tier" in card
    assert "passage" in card
    assert "confidence" in card
    assert "verified" in card
    assert "section_position" in card


@pytest.mark.anyio
async def test_get_evidence_section_filter(
    evidence_db, api_client, course_with_evidence
):
    """GET /evidence?section=1 returns only section 1 cards."""
    course = course_with_evidence
    response = await api_client.get(
        f"/api/courses/{course.id}/evidence?section=1"
    )

    assert response.status_code == 200
    data = response.json()

    # Only 3 cards for section 1
    assert len(data) == 3
    assert all(card["section_position"] == 1 for card in data)


@pytest.mark.anyio
async def test_get_evidence_section_filter_no_cards(
    evidence_db, api_client, course_with_evidence
):
    """GET /evidence?section=3 returns empty when section has no cards."""
    course = course_with_evidence
    response = await api_client.get(
        f"/api/courses/{course.id}/evidence?section=3"
    )

    assert response.status_code == 200
    data = response.json()
    assert data == []


@pytest.mark.anyio
async def test_get_evidence_nonexistent_course(evidence_db, api_client):
    """GET /evidence returns 404 for nonexistent course."""
    fake_id = "00000000-0000-0000-0000-000000000000"
    response = await api_client.get(f"/api/courses/{fake_id}/evidence")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_get_evidence_card_fields(
    evidence_db, api_client, course_with_evidence
):
    """Evidence cards include all expected fields with correct types."""
    course = course_with_evidence
    response = await api_client.get(
        f"/api/courses/{course.id}/evidence?section=1"
    )

    data = response.json()
    assert len(data) >= 1

    # Find the verified card with caveat
    card_with_caveat = next(
        (c for c in data if c["caveat"] is not None), None
    )
    assert card_with_caveat is not None
    assert isinstance(card_with_caveat["caveat"], str)

    # Find verified card
    verified_card = next(
        (c for c in data if c["verified"] is True), None
    )
    assert verified_card is not None
    assert verified_card["verification_note"] is not None

    # Check tier values
    tiers = {c["source_tier"] for c in data}
    assert tiers == {1, 2, 3}


@pytest.mark.anyio
async def test_get_evidence_includes_academic_metadata(evidence_db, api_client):
    """Academic evidence card metadata is returned by the API."""
    async with evidence_db() as session:
        course = Course(topic="Academic Evidence", status="completed", user_id=TEST_USER_UUID)
        session.add(course)
        await session.commit()

        section = Section(
            course_id=course.id,
            position=1,
            title="Research",
            summary="Research summary",
        )
        session.add(section)
        await session.commit()

        card = EvidenceCard(
            course_id=course.id,
            section_position=1,
            claim="Transformers improved sequence modeling performance",
            source_url="https://arxiv.org/abs/1706.03762",
            source_title="Attention Is All You Need",
            source_tier=1,
            passage="The Transformer achieves better results than recurrent models.",
            retrieved_date=date.today(),
            confidence=0.98,
            explanation="Foundational paper",
            is_academic=True,
            academic_authors="Vaswani, A., et al.",
            academic_year=2017,
            academic_venue="NeurIPS",
            academic_doi="10.48550/arXiv.1706.03762",
        )
        session.add(card)
        await session.commit()

    response = await api_client.get(f"/api/courses/{course.id}/evidence")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["is_academic"] is True
    assert data[0]["academic_authors"] == "Vaswani, A., et al."
    assert data[0]["academic_year"] == 2017
    assert data[0]["academic_venue"] == "NeurIPS"
    assert data[0]["academic_doi"] == "10.48550/arXiv.1706.03762"


# ---------------------------------------------------------------------------
# Tests: GET /api/courses/{id}/blackboard
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_blackboard_populated(
    evidence_db, api_client, course_with_blackboard
):
    """GET /blackboard returns populated blackboard state."""
    course, bb = course_with_blackboard
    response = await api_client.get(
        f"/api/courses/{course.id}/blackboard"
    )

    assert response.status_code == 200
    data = response.json()

    assert data is not None
    assert "glossary" in data
    assert "variable" in data["glossary"]
    assert data["glossary"]["variable"]["definition"] == "A named storage location"

    assert "concept_ownership" in data
    assert data["concept_ownership"]["variables"] == 1

    assert "coverage_map" in data
    assert "variables" in data["coverage_map"]["all_topics"]

    assert "key_points" in data
    assert "source_log" in data
    assert len(data["source_log"]) == 1

    assert "open_questions" in data
    assert "How does garbage collection work?" in data["open_questions"]


@pytest.mark.anyio
async def test_get_blackboard_none_when_not_created(
    evidence_db, api_client, course_with_evidence
):
    """GET /blackboard returns null when no blackboard exists."""
    course = course_with_evidence
    response = await api_client.get(
        f"/api/courses/{course.id}/blackboard"
    )

    assert response.status_code == 200
    assert response.json() is None


@pytest.mark.anyio
async def test_get_blackboard_nonexistent_course(evidence_db, api_client):
    """GET /blackboard returns 404 for nonexistent course."""
    fake_id = "00000000-0000-0000-0000-000000000000"
    response = await api_client.get(
        f"/api/courses/{fake_id}/blackboard"
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Pipeline-status endpoint tests removed (Phase 4, Milestone 3)
# Real-time progress is now delivered via Trigger.dev metadata and the
# @trigger.dev/react-hooks useRealtimeRun hook.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Tests: Evidence card creation and retrieval round-trip
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_evidence_round_trip_via_service(evidence_db):
    """Create evidence cards via service, retrieve via API query."""
    from app.agent import EvidenceCardItem
    from app.agent_service import save_evidence_cards, get_evidence_cards

    async with evidence_db() as session:
        course = Course(topic="Round Trip", status="researching", user_id=TEST_USER_UUID)
        session.add(course)
        await session.commit()

        cards = [
            EvidenceCardItem(
                claim="Test claim 1",
                source_url="https://example.com/1",
                source_title="Source 1",
                source_tier=1,
                passage="Passage 1",
                confidence=0.95,
                explanation="Explanation 1",
            ),
            EvidenceCardItem(
                claim="Test claim 2",
                source_url="https://example.com/2",
                source_title="Source 2",
                source_tier=2,
                passage="Passage 2",
                confidence=0.8,
                caveat="Only for Python 3.10+",
                explanation="Explanation 2",
            ),
        ]

        await save_evidence_cards(course.id, 1, cards, session)

        # Retrieve via service function
        retrieved = await get_evidence_cards(course.id, 1, session)
        assert len(retrieved) == 2
        assert retrieved[0].claim == "Test claim 1"
        assert retrieved[1].caveat == "Only for Python 3.10+"
        assert all(c.retrieved_date == date.today() for c in retrieved)
        assert all(c.verified is False for c in retrieved)


# ---------------------------------------------------------------------------
# Tests: Blackboard creation and retrieval round-trip
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_blackboard_round_trip_via_service(evidence_db):
    """Create and update blackboard via service, retrieve via service."""
    from app.agent import BlackboardUpdates
    from app.agent_service import create_blackboard, get_blackboard, update_blackboard

    async with evidence_db() as session:
        course = Course(topic="BB Round Trip", status="writing", user_id=TEST_USER_UUID)
        session.add(course)
        await session.commit()

        bb = await create_blackboard(course.id, session)
        assert bb is not None

        # Update with terms
        updates = BlackboardUpdates(
            new_glossary_terms={"test_term": {"definition": "A test", "defined_in_section": 1}},
            new_concept_ownership={"testing": 1},
            topics_covered=["unit testing"],
            key_points_summary="Testing is important.",
            new_sources=[{"url": "https://pytest.org", "title": "pytest"}],
        )
        await update_blackboard(bb, updates, session)

        # Retrieve
        retrieved = await get_blackboard(course.id, session)
        assert retrieved is not None
        assert "test_term" in retrieved.glossary
        assert retrieved.concept_ownership["testing"] == 1
        assert "unit testing" in retrieved.coverage_map.get("all_topics", [])
        assert len(retrieved.source_log) == 1
