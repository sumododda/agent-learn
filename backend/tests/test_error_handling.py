"""Tests for Phase 8: Error handling scenarios.

Tests cover:
- Tavily failure during discovery: course creates with ungrounded=true
- Verifier rejects all evidence: writer still runs with empty verified cards
- Editor returns bad blackboard update: pipeline continues, blackboard not corrupted
- Full pipeline with unhandled error: course status = "failed"
- extract_citations with edge cases
- update_blackboard with malformed data
"""

import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select, event as sa_event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.agent import (
    BlackboardUpdates,
    CardVerification,
    CourseOutlineWithBriefs,
    EditorResult,
    EvidenceCardItem,
    OutlineSection,
    ResearchBriefItem,
    VerificationResult,
)
from app.models import Base, Blackboard, Course, EvidenceCard, ResearchBrief, Section


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def error_session():
    """Create a fresh in-memory SQLite DB and session for error tests."""
    engine = create_async_engine("sqlite+aiosqlite://")

    @sa_event.listens_for(engine.sync_engine, "connect")
    def _set_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ---------------------------------------------------------------------------
# Test: Tavily failure during discovery -> ungrounded=True
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_tavily_failure_creates_ungrounded_course(setup_db, client):
    """When Tavily fails during discovery, course is created with ungrounded=True."""
    outline = CourseOutlineWithBriefs(
        sections=[
            OutlineSection(position=1, title="Intro", summary="Getting started"),
            OutlineSection(position=2, title="Core", summary="Key ideas"),
        ],
        research_briefs=[
            ResearchBriefItem(
                section_position=1,
                questions=["Q1?"],
                source_policy={"preferred_tiers": [1, 2], "scope": "intro"},
            ),
            ResearchBriefItem(
                section_position=2,
                questions=["Q2?"],
                source_policy={"preferred_tiers": [1, 2], "scope": "core"},
            ),
        ],
    )

    # generate_outline returns (outline, ungrounded=True)
    mock_return = (outline, True)
    with (
        patch(
            "app.routers.courses._get_user_provider",
            new_callable=AsyncMock,
            return_value=("anthropic", "claude-sonnet-4-20250514", {"api_key": "sk-test"}, {}),
        ),
        patch(
            "app.routers.courses.generate_outline",
            new_callable=AsyncMock,
            return_value=mock_return,
        ),
    ):
        response = await client.post(
            "/api/courses", json={"topic": "Machine Learning"}
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ungrounded"] is True
    assert data["status"] == "outline_ready"
    assert len(data["sections"]) == 2


# ---------------------------------------------------------------------------
# Test: Verifier rejects all cards -> writer still runs
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_all_cards_rejected_writer_still_runs(setup_db, error_session):
    """When verifier rejects all cards, writer still runs (with all cards passed)."""
    course = Course(topic="Error Test", status="outline_ready")
    error_session.add(course)
    await error_session.commit()

    sections = []
    for i in range(1, 3):
        section = Section(
            course_id=course.id,
            position=i,
            title=f"Section {i}",
            summary=f"Summary {i}",
        )
        error_session.add(section)
        sections.append(section)
    await error_session.commit()

    # Add research briefs
    for i in range(1, 3):
        brief = ResearchBrief(
            course_id=course.id,
            section_position=i,
            questions=[f"Q{i}?"],
            source_policy={},
        )
        error_session.add(brief)
    await error_session.commit()

    # Seed evidence cards
    for sec in [1, 2]:
        for j in range(2):
            card = EvidenceCard(
                course_id=course.id,
                section_position=sec,
                claim=f"Claim {sec}-{j}",
                source_url=f"https://example.com/{sec}/{j}",
                source_title=f"Source {sec}-{j}",
                source_tier=1,
                passage=f"Passage {sec}-{j}",
                retrieved_date=date.today(),
                confidence=0.9,
                explanation=f"Explanation {sec}-{j}",
            )
            error_session.add(card)
    await error_session.commit()

    # Verifier rejects all cards
    async def mock_verify(cards, brief, session, *args, **kwargs):
        for card in cards:
            card.verified = False
            card.verification_note = "Rejected: insufficient"
        await session.commit()
        return VerificationResult(
            card_verifications=[
                CardVerification(card_index=i, verified=False, note="Rejected")
                for i in range(len(cards))
            ],
            needs_more_research=False,
            gaps=[],
        )

    write_was_called = []

    async def mock_write(cards, blackboard, section, outline, session, *args, **kwargs):
        write_was_called.append(section.position)
        return f"## {section.title}\n\nContent without evidence."

    async def mock_edit(draft, blackboard, cards, section_position, session, *args, **kwargs):
        return EditorResult(
            edited_content=f"## Section {section_position}\n\nEdited.",
            blackboard_updates=BlackboardUpdates(
                new_glossary_terms={},
                new_concept_ownership={},
                topics_covered=[],
                key_points_summary="",
                new_sources=[],
            ),
        )

    async def mock_research_all(course_id, briefs, session, *args, **kwargs):
        pass  # Cards already seeded

    with (
        patch("app.agent_service.research_all_sections", new_callable=AsyncMock, side_effect=mock_research_all),
        patch("app.agent_service.verify_evidence", new_callable=AsyncMock, side_effect=mock_verify),
        patch("app.agent_service.write_section", new_callable=AsyncMock, side_effect=mock_write),
        patch("app.agent_service.edit_section", new_callable=AsyncMock, side_effect=mock_edit),
    ):
        from app.agent_service import generate_lessons

        await generate_lessons(course.id, error_session)

    # Writer was called for both sections despite all cards being rejected
    assert sorted(write_was_called) == [1, 2]

    # Pipeline completed
    await error_session.refresh(course)
    assert course.status == "completed"


# ---------------------------------------------------------------------------
# Test: Editor returns bad blackboard update -> blackboard not corrupted
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_bad_blackboard_update_doesnt_corrupt(setup_db):
    """update_blackboard with malformed data logs warning, doesn't corrupt state."""
    # Use a dedicated engine/session to avoid greenlet issues after rollback
    engine = create_async_engine("sqlite+aiosqlite://")

    @sa_event.listens_for(engine.sync_engine, "connect")
    def _pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        course = Course(topic="BB Error Test", status="writing")
        session.add(course)
        await session.commit()

        from app.agent_service import create_blackboard, update_blackboard

        bb = await create_blackboard(course.id, session)

        # First update: valid
        good_updates = BlackboardUpdates(
            new_glossary_terms={"existing_term": {"definition": "Good definition", "defined_in_section": 1}},
            new_concept_ownership={"existing_concept": 1},
            topics_covered=["good_topic"],
            key_points_summary="Good summary",
            new_sources=[{"url": "https://good.com", "title": "Good Source"}],
        )
        await update_blackboard(bb, good_updates, session)

        # Verify first update applied
        result = await session.execute(
            select(Blackboard).where(Blackboard.course_id == course.id)
        )
        bb_check = result.scalar_one()
        assert "existing_term" in bb_check.glossary
        assert len(bb_check.source_log) == 1

    # Use a fresh session for the bad update to avoid rollback contamination
    async with session_factory() as session2:
        result = await session2.execute(
            select(Blackboard).where(Blackboard.course_id == course.id)
        )
        bb2 = result.scalar_one()

        # Second update: malformed dict (missing required fields)
        bad_data = {"garbage_field": "bad_value"}
        await update_blackboard(bb2, bad_data, session2)

    # Use yet another fresh session to verify data is not corrupted
    async with session_factory() as session3:
        result = await session3.execute(
            select(Blackboard).where(Blackboard.course_id == course.id)
        )
        retrieved = result.scalar_one()
        assert "existing_term" in retrieved.glossary
        assert retrieved.concept_ownership.get("existing_concept") == 1
        assert len(retrieved.source_log) == 1

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ---------------------------------------------------------------------------
# Test: Full pipeline unhandled error -> course status "failed"
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_pipeline_unhandled_error_sets_failed(setup_db, error_session):
    """An unrecoverable error in the pipeline sets course.status='failed'."""
    course = Course(topic="Crash Test", status="outline_ready")
    error_session.add(course)
    await error_session.commit()

    section = Section(
        course_id=course.id,
        position=1,
        title="Section 1",
        summary="Summary",
    )
    error_session.add(section)

    brief = ResearchBrief(
        course_id=course.id,
        section_position=1,
        questions=["Q?"],
        source_policy={},
    )
    error_session.add(brief)
    await error_session.commit()

    # research_all_sections throws an unhandled exception
    with patch(
        "app.agent_service.research_all_sections",
        new_callable=AsyncMock,
        side_effect=RuntimeError("Database connection lost"),
    ):
        from app.agent_service import generate_lessons

        await generate_lessons(course.id, error_session)

    await error_session.refresh(course)
    assert course.status == "failed"


# ---------------------------------------------------------------------------
# Test: extract_citations edge cases
# ---------------------------------------------------------------------------


def test_extract_citations_with_no_content():
    """extract_citations handles empty content."""
    from app.agent_service import extract_citations

    citations = extract_citations("", [])
    assert citations == []


def test_extract_citations_handles_zero_indexed():
    """extract_citations correctly skips [0] (cards are 1-indexed)."""
    from app.agent_service import extract_citations

    card = MagicMock()
    card.claim = "Test"
    card.source_url = "https://test.com"
    card.source_title = "Test Source"

    citations = extract_citations("[0] is invalid. [1] is valid.", [card])
    assert len(citations) == 1
    assert citations[0]["number"] == 1


def test_extract_citations_large_numbers_ignored():
    """extract_citations ignores citation numbers larger than card count."""
    from app.agent_service import extract_citations

    card = MagicMock()
    card.claim = "Test"
    card.source_url = "https://test.com"
    card.source_title = "Test Source"

    citations = extract_citations("[1] valid. [100] invalid.", [card])
    assert len(citations) == 1
    assert citations[0]["number"] == 1


# ---------------------------------------------------------------------------
# Test: Pipeline with no research briefs (edge case)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_pipeline_no_section_briefs(setup_db, error_session):
    """Pipeline handles course with no section-level research briefs."""
    course = Course(topic="No Briefs", status="outline_ready")
    error_session.add(course)
    await error_session.commit()

    section = Section(
        course_id=course.id,
        position=1,
        title="Section 1",
        summary="Summary",
    )
    error_session.add(section)

    # Only discovery brief, no section-level briefs
    discovery = ResearchBrief(
        course_id=course.id,
        section_position=None,
        questions=[],
        source_policy={},
    )
    error_session.add(discovery)
    await error_session.commit()

    async def mock_research_all(course_id, briefs, session, *args, **kwargs):
        # No section briefs to research
        pass

    async def mock_write(cards, blackboard, section, outline, session, *args, **kwargs):
        return f"## {section.title}\n\nContent."

    async def mock_edit(draft, blackboard, cards, section_position, session, *args, **kwargs):
        return EditorResult(
            edited_content=f"## Section {section_position}\n\nEdited.",
            blackboard_updates=BlackboardUpdates(
                new_glossary_terms={},
                new_concept_ownership={},
                topics_covered=[],
                key_points_summary="",
                new_sources=[],
            ),
        )

    with (
        patch("app.agent_service.research_all_sections", new_callable=AsyncMock, side_effect=mock_research_all),
        patch("app.agent_service.verify_evidence", new_callable=AsyncMock) as mock_verify,
        patch("app.agent_service.write_section", new_callable=AsyncMock, side_effect=mock_write),
        patch("app.agent_service.edit_section", new_callable=AsyncMock, side_effect=mock_edit),
    ):
        from app.agent_service import generate_lessons

        await generate_lessons(course.id, error_session)

    # Pipeline completed (no verify called because no brief matched section 1
    # and there were no cards)
    await error_session.refresh(course)
    assert course.status == "completed"


# ---------------------------------------------------------------------------
# Test: generate_outline Tavily error fallback
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_generate_outline_tavily_error_returns_ungrounded(setup_db):
    """generate_outline returns ungrounded=True when discover_topic raises."""
    mock_outline = CourseOutlineWithBriefs(
        sections=[OutlineSection(position=1, title="Intro", summary="Start")],
        research_briefs=[
            ResearchBriefItem(
                section_position=1,
                questions=["Q1?"],
                source_policy={},
            )
        ],
    )

    def _mock_planner_agent(structured_response):
        mock_agent = AsyncMock()
        mock_agent.ainvoke.return_value = {
            "structured_response": structured_response,
            "messages": [],
        }
        return mock_agent

    with (
        patch(
            "app.agent_service.discover_topic",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Search API key invalid"),
        ),
        patch(
            "app.agent_service.create_planner",
            return_value=_mock_planner_agent(mock_outline),
        ),
    ):
        from app.agent_service import generate_outline

        result, ungrounded = await generate_outline(
            "Test Topic",
            search_provider="tavily", search_credentials={"api_key": "fake"},
        )

    assert ungrounded is True
    assert isinstance(result, CourseOutlineWithBriefs)


# ---------------------------------------------------------------------------
# Test: update_pipeline_status function correctness
# ---------------------------------------------------------------------------


def test_update_status_creates_entry():
    """_update_status creates a new PipelineStatus entry."""
    from app.pipeline import _update_status, _jobs

    test_id = "test-pipeline-status-create"
    _update_status(test_id, stage="researching", section=1, total=3)

    assert test_id in _jobs
    assert _jobs[test_id].stage == "researching"
    assert _jobs[test_id].section == 1
    assert _jobs[test_id].total == 3

    _jobs.pop(test_id, None)


def test_update_status_updates_existing():
    """_update_status updates an existing PipelineStatus entry."""
    from app.pipeline import _update_status, _jobs

    test_id = "test-pipeline-status-update"
    _update_status(test_id, stage="researching", section=1, total=3)
    _update_status(test_id, stage="writing", section=2, total=3)

    assert _jobs[test_id].stage == "writing"
    assert _jobs[test_id].section == 2
    assert _jobs[test_id].total == 3

    _jobs.pop(test_id, None)


def test_get_pipeline_status_returns_none_for_unknown():
    """get_pipeline_status returns None for unknown course_id."""
    from app.pipeline import get_pipeline_status

    assert get_pipeline_status("nonexistent-course-id") is None
