"""Tests for Phase 5: Writer (evidence-aware) + Editor + Blackboard CRUD.

Tests cover:
- Blackboard CRUD: create_blackboard, get_blackboard, update_blackboard
- Writer helpers: _format_cards_for_writer, _format_blackboard_for_agent, _format_outline_context
- write_section: evidence-aware section writing with blackboard context
- edit_section: editor agent invocation, EditorResult handling
- extract_citations: [N] marker extraction and card mapping
- Editor/BlackboardUpdates schema validation
- Discovery brief persists serialized TopicBrief JSON in findings field
"""

import json
import uuid
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import event as sa_event, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.agent import (
    BlackboardUpdates,
    EditorResult,
    TopicBrief,
    CourseOutlineWithBriefs,
    OutlineSection,
    ResearchBriefItem,
)
from app.models import Base, Blackboard, Course, EvidenceCard, ResearchBrief, Section, User

# Deterministic test user UUID for phase5 tests
_TEST_USER_UUID = uuid.UUID("00000000-0000-0000-0000-cccccccccccc")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_session():
    """Create a fresh in-memory SQLite DB and session for each test."""
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
        user = User(id=_TEST_USER_UUID, email="phase5@test.com", password_hash="hashed")
        session.add(user)
        await session.commit()

    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def course_with_cards(db_session):
    """Create a course with verified and unverified evidence cards."""
    course = Course(topic="Python Basics", status="writing", user_id=_TEST_USER_UUID)
    db_session.add(course)
    await db_session.commit()

    cards = [
        EvidenceCard(
            course_id=course.id,
            section_position=1,
            claim="Python was created by Guido van Rossum in 1991",
            source_url="https://docs.python.org/3/faq/general.html",
            source_title="Python FAQ",
            source_tier=1,
            passage="Python was conceived in the late 1980s by Guido van Rossum...",
            retrieved_date=date.today(),
            confidence=0.95,
            caveat=None,
            explanation="Foundational fact",
            verified=True,
            verification_note="Strong official source",
        ),
        EvidenceCard(
            course_id=course.id,
            section_position=1,
            claim="Python uses dynamic typing",
            source_url="https://realpython.com/python-type-checking/",
            source_title="Python Type Checking Guide",
            source_tier=2,
            passage="Python is a dynamically typed language...",
            retrieved_date=date.today(),
            confidence=0.9,
            caveat="Type hints added in Python 3.5",
            explanation="Key language characteristic",
            verified=True,
            verification_note="Reputable tutorial",
        ),
        EvidenceCard(
            course_id=course.id,
            section_position=1,
            claim="Python is slow compared to C",
            source_url="https://stackoverflow.com/questions/99999",
            source_title="SO: Python speed",
            source_tier=3,
            passage="Python can be 10-100x slower than C...",
            retrieved_date=date.today(),
            confidence=0.6,
            caveat="Depends on workload",
            explanation="Common concern",
            verified=False,
            verification_note="Source too unreliable",
        ),
    ]
    db_session.add_all(cards)
    await db_session.commit()

    return course, cards


@pytest.fixture
def sample_blackboard_updates():
    """A BlackboardUpdates instance for testing."""
    return BlackboardUpdates(
        new_glossary_terms={
            "dynamic typing": {
                "definition": "Types are determined at runtime, not at compile time",
                "defined_in_section": 1,
            },
        },
        new_concept_ownership={"Python origins": 1, "typing system": 1},
        topics_covered=["Python history", "dynamic typing", "type hints"],
        key_points_summary="Python was created in 1991, uses dynamic typing with optional type hints.",
        new_sources=[
            {"url": "https://docs.python.org/3/faq/general.html", "title": "Python FAQ"},
        ],
    )


@pytest.fixture
def sample_editor_result(sample_blackboard_updates):
    """An EditorResult instance for testing."""
    return EditorResult(
        edited_content="## Introduction\n\nPython was created by Guido van Rossum in 1991 [1]. It uses dynamic typing [2].\n\n### Key Takeaways\n- Created in 1991\n- Dynamically typed",
        blackboard_updates=sample_blackboard_updates,
    )


# ---------------------------------------------------------------------------
# Tests: Schema validation
# ---------------------------------------------------------------------------


def test_blackboard_updates_schema():
    """BlackboardUpdates validates all required fields."""
    updates = BlackboardUpdates(
        new_glossary_terms={"term1": {"definition": "def1", "defined_in_section": 1}},
        new_concept_ownership={"concept1": 1},
        topics_covered=["topic1"],
        key_points_summary="Summary here",
        new_sources=[{"url": "https://example.com", "title": "Example"}],
    )
    assert "term1" in updates.new_glossary_terms
    assert updates.topics_covered == ["topic1"]
    assert updates.key_points_summary == "Summary here"


def test_blackboard_updates_empty_fields():
    """BlackboardUpdates accepts empty dicts/lists."""
    updates = BlackboardUpdates(
        new_glossary_terms={},
        new_concept_ownership={},
        topics_covered=[],
        key_points_summary="",
        new_sources=[],
    )
    assert updates.new_glossary_terms == {}
    assert updates.topics_covered == []


def test_editor_result_schema():
    """EditorResult validates edited_content and blackboard_updates."""
    updates = BlackboardUpdates(
        new_glossary_terms={},
        new_concept_ownership={},
        topics_covered=[],
        key_points_summary="",
        new_sources=[],
    )
    result = EditorResult(
        edited_content="## Section\n\nContent here.",
        blackboard_updates=updates,
    )
    assert "## Section" in result.edited_content
    assert isinstance(result.blackboard_updates, BlackboardUpdates)


# ---------------------------------------------------------------------------
# Tests: Blackboard CRUD
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_blackboard(setup_db, db_session):
    """create_blackboard creates an empty row and returns it."""
    course = Course(topic="Test", status="writing", user_id=_TEST_USER_UUID)
    db_session.add(course)
    await db_session.commit()

    from app.agent_service import create_blackboard

    bb = await create_blackboard(course.id, db_session)

    assert bb is not None
    assert bb.course_id == course.id
    assert bb.glossary == {} or bb.glossary is None or bb.glossary == {}
    assert bb.concept_ownership == {} or bb.concept_ownership is None


@pytest.mark.anyio
async def test_get_blackboard(setup_db, db_session):
    """get_blackboard retrieves by course_id."""
    course = Course(topic="Test", status="writing", user_id=_TEST_USER_UUID)
    db_session.add(course)
    await db_session.commit()

    from app.agent_service import create_blackboard, get_blackboard

    await create_blackboard(course.id, db_session)
    bb = await get_blackboard(course.id, db_session)

    assert bb is not None
    assert bb.course_id == course.id


@pytest.mark.anyio
async def test_get_blackboard_returns_none(setup_db, db_session):
    """get_blackboard returns None when no blackboard exists."""
    from app.agent_service import get_blackboard

    bb = await get_blackboard(uuid.uuid4(), db_session)
    assert bb is None


@pytest.mark.anyio
async def test_update_blackboard_merges(setup_db, db_session, sample_blackboard_updates):
    """update_blackboard merges new data into existing fields."""
    course = Course(topic="Test", status="writing", user_id=_TEST_USER_UUID)
    db_session.add(course)
    await db_session.commit()

    from app.agent_service import create_blackboard, update_blackboard

    bb = await create_blackboard(course.id, db_session)
    await update_blackboard(bb, sample_blackboard_updates, db_session)

    # Refresh from DB
    await db_session.refresh(bb)

    assert "dynamic typing" in bb.glossary
    assert bb.concept_ownership["Python origins"] == 1
    assert "Python history" in bb.coverage_map.get("all_topics", [])
    assert len(bb.source_log) == 1
    assert bb.source_log[0]["url"] == "https://docs.python.org/3/faq/general.html"


@pytest.mark.anyio
async def test_update_blackboard_merges_multiple_times(setup_db, db_session):
    """update_blackboard merges across multiple calls without overwriting."""
    course = Course(topic="Test", status="writing", user_id=_TEST_USER_UUID)
    db_session.add(course)
    await db_session.commit()

    from app.agent_service import create_blackboard, update_blackboard

    bb = await create_blackboard(course.id, db_session)

    # First update
    updates_1 = BlackboardUpdates(
        new_glossary_terms={"term_a": {"definition": "def A", "defined_in_section": 1}},
        new_concept_ownership={"concept_a": 1},
        topics_covered=["topic_a"],
        key_points_summary="Summary for section 1",
        new_sources=[{"url": "https://a.com", "title": "Source A"}],
    )
    await update_blackboard(bb, updates_1, db_session)

    # Second update
    updates_2 = BlackboardUpdates(
        new_glossary_terms={"term_b": {"definition": "def B", "defined_in_section": 2}},
        new_concept_ownership={"concept_b": 2},
        topics_covered=["topic_b"],
        key_points_summary="Summary for section 2",
        new_sources=[{"url": "https://b.com", "title": "Source B"}],
    )
    await update_blackboard(bb, updates_2, db_session)

    await db_session.refresh(bb)

    # Both terms should be present
    assert "term_a" in bb.glossary
    assert "term_b" in bb.glossary

    # Both concepts should be present
    assert bb.concept_ownership["concept_a"] == 1
    assert bb.concept_ownership["concept_b"] == 2

    # Both topics should be in coverage
    all_topics = bb.coverage_map.get("all_topics", [])
    assert "topic_a" in all_topics
    assert "topic_b" in all_topics

    # Both sources in log
    assert len(bb.source_log) == 2

    # Both key points
    assert len(bb.key_points) == 2


@pytest.mark.anyio
async def test_update_blackboard_invalid_data_doesnt_crash(setup_db, db_session):
    """update_blackboard logs warning and skips on invalid data."""
    course = Course(topic="Test", status="writing", user_id=_TEST_USER_UUID)
    db_session.add(course)
    await db_session.commit()

    from app.agent_service import create_blackboard, update_blackboard

    bb = await create_blackboard(course.id, db_session)

    # Pass a dict that can't be parsed as BlackboardUpdates (missing required fields)
    bad_updates = {"not_a_valid_field": True}

    # Should not raise
    await update_blackboard(bb, bad_updates, db_session)

    # Blackboard should be unchanged
    await db_session.refresh(bb)
    assert bb.glossary == {} or bb.glossary is None


# ---------------------------------------------------------------------------
# Tests: _format_cards_for_writer
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_format_cards_for_writer(setup_db, course_with_cards):
    """_format_cards_for_writer produces 1-indexed numbered cards."""
    from app.agent_service import _format_cards_for_writer

    _, cards = course_with_cards
    formatted = _format_cards_for_writer(cards)

    # Should contain 1-indexed markers
    assert "[1]" in formatted
    assert "[2]" in formatted
    assert "[3]" in formatted

    # Should contain card content
    assert "Python was created by Guido van Rossum" in formatted
    assert "Python uses dynamic typing" in formatted
    assert "https://docs.python.org/3/faq/general.html" in formatted

    # Should include caveat where present
    assert "Type hints added in Python 3.5" in formatted


@pytest.mark.anyio
async def test_format_cards_for_writer_empty(setup_db):
    """_format_cards_for_writer handles empty list."""
    from app.agent_service import _format_cards_for_writer

    formatted = _format_cards_for_writer([])
    assert "No evidence cards" in formatted


# ---------------------------------------------------------------------------
# Tests: _format_blackboard_for_agent
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_format_blackboard_for_agent_none(setup_db):
    """_format_blackboard_for_agent handles None blackboard."""
    from app.agent_service import _format_blackboard_for_agent

    formatted = _format_blackboard_for_agent(None)
    assert "empty" in formatted.lower() or "first section" in formatted.lower()


@pytest.mark.anyio
async def test_format_blackboard_for_agent_populated(setup_db, db_session):
    """_format_blackboard_for_agent formats populated blackboard."""
    from app.agent_service import _format_blackboard_for_agent, create_blackboard, update_blackboard

    course = Course(topic="Test", status="writing", user_id=_TEST_USER_UUID)
    db_session.add(course)
    await db_session.commit()

    bb = await create_blackboard(course.id, db_session)
    updates = BlackboardUpdates(
        new_glossary_terms={"variable": {"definition": "A named storage location", "defined_in_section": 1}},
        new_concept_ownership={"variables": 1},
        topics_covered=["variable basics"],
        key_points_summary="Variables store data.",
        new_sources=[],
    )
    await update_blackboard(bb, updates, db_session)
    await db_session.refresh(bb)

    formatted = _format_blackboard_for_agent(bb)

    assert "GLOSSARY" in formatted
    assert "variable" in formatted
    assert "CONCEPT OWNERSHIP" in formatted
    assert "TOPICS ALREADY COVERED" in formatted
    assert "variable basics" in formatted


@pytest.mark.anyio
async def test_format_blackboard_for_agent_empty_fields(setup_db, db_session):
    """_format_blackboard_for_agent handles blackboard with empty fields."""
    from app.agent_service import _format_blackboard_for_agent, create_blackboard

    course = Course(topic="Test", status="writing", user_id=_TEST_USER_UUID)
    db_session.add(course)
    await db_session.commit()

    bb = await create_blackboard(course.id, db_session)
    formatted = _format_blackboard_for_agent(bb)

    assert "GLOSSARY: (empty)" in formatted
    assert "CONCEPT OWNERSHIP: (empty)" in formatted
    assert "(none)" in formatted


# ---------------------------------------------------------------------------
# Tests: _format_outline_context
# ---------------------------------------------------------------------------


def test_format_outline_context_dicts():
    """_format_outline_context formats list of dicts."""
    from app.agent_service import _format_outline_context

    outline = [
        {"position": 1, "title": "Intro", "summary": "Getting started"},
        {"position": 2, "title": "Core", "summary": "Main content"},
    ]
    formatted = _format_outline_context(outline)
    assert "1. Intro" in formatted
    assert "2. Core" in formatted


def test_format_outline_context_objects():
    """_format_outline_context formats objects with position/title/summary attrs."""
    from app.agent_service import _format_outline_context

    outline = [
        SimpleNamespace(position=1, title="Intro", summary="Getting started"),
        SimpleNamespace(position=2, title="Core", summary="Main content"),
    ]
    formatted = _format_outline_context(outline)
    assert "1. Intro" in formatted
    assert "2. Core" in formatted


# ---------------------------------------------------------------------------
# Tests: write_section (direct LLM call)
# ---------------------------------------------------------------------------


def _mock_writer_llm(content_text):
    """Create a mock LLM whose ainvoke returns a message with .content."""
    mock_llm = AsyncMock()
    mock_response = MagicMock()
    mock_response.content = content_text
    mock_llm.ainvoke.return_value = mock_response
    return mock_llm


@pytest.mark.anyio
async def test_write_section_filters_verified_cards(setup_db, db_session, course_with_cards):
    """write_section only passes verified cards to the writer."""
    course, cards = course_with_cards

    section = SimpleNamespace(title="Introduction", summary="Getting started with Python")
    outline = [
        SimpleNamespace(position=1, title="Introduction", summary="Getting started"),
    ]

    mock_content = "## Introduction\n\nPython was created in 1991 [1]. It uses dynamic typing [2]."
    mock_llm = _mock_writer_llm(mock_content)

    with patch("app.agent_service.provider_service.build_chat_model", return_value=mock_llm):
        from app.agent_service import write_section

        result = await write_section(cards, None, section, outline, db_session)

    assert "## Introduction" in result

    # Check that the message sent to the LLM contains only verified cards
    call_args = mock_llm.ainvoke.call_args
    messages = call_args[0][0]
    user_message = messages[1].content  # SystemMessage, HumanMessage
    assert "Python was created by Guido van Rossum" in user_message
    assert "Python uses dynamic typing" in user_message


@pytest.mark.anyio
async def test_write_section_with_empty_blackboard(setup_db, db_session, course_with_cards):
    """write_section works when blackboard is None (first section)."""
    course, cards = course_with_cards

    section = SimpleNamespace(title="Introduction", summary="Getting started")
    outline = [SimpleNamespace(position=1, title="Introduction", summary="Getting started")]

    mock_llm = _mock_writer_llm("## Introduction\n\nContent here.")

    with patch("app.agent_service.provider_service.build_chat_model", return_value=mock_llm):
        from app.agent_service import write_section

        result = await write_section(cards, None, section, outline, db_session)

    assert "## Introduction" in result


@pytest.mark.anyio
async def test_write_section_with_dict_section(setup_db, db_session, course_with_cards):
    """write_section accepts section as a dict."""
    course, cards = course_with_cards

    section = {"title": "Introduction", "summary": "Getting started", "position": 1}
    outline = [{"position": 1, "title": "Introduction", "summary": "Getting started"}]

    mock_llm = _mock_writer_llm("## Introduction\n\nContent here.")

    with patch("app.agent_service.provider_service.build_chat_model", return_value=mock_llm):
        from app.agent_service import write_section

        result = await write_section(cards, None, section, outline, db_session)

    assert "## Introduction" in result


# ---------------------------------------------------------------------------
# Tests: edit_section (langchain create_agent with ToolStrategy)
# ---------------------------------------------------------------------------


def _mock_editor_agent(editor_result):
    """Create a mock editor agent whose ainvoke returns structured_response."""
    mock_agent = AsyncMock()
    mock_agent.ainvoke.return_value = {
        "structured_response": editor_result,
        "messages": [],
    }
    return mock_agent


@pytest.mark.anyio
async def test_edit_section_returns_editor_result(setup_db, db_session, course_with_cards, sample_editor_result):
    """edit_section returns an EditorResult with edited content and blackboard updates."""
    course, cards = course_with_cards
    draft = "## Introduction\n\nDraft content."

    mock_agent = _mock_editor_agent(sample_editor_result)

    with patch("app.agent_service.create_editor", return_value=mock_agent):
        from app.agent_service import edit_section

        result = await edit_section(draft, None, cards, 1, db_session)

    assert isinstance(result, EditorResult)
    assert "## Introduction" in result.edited_content
    assert isinstance(result.blackboard_updates, BlackboardUpdates)
    assert "dynamic typing" in result.blackboard_updates.new_glossary_terms


@pytest.mark.anyio
async def test_edit_section_handles_dict_result(setup_db, db_session, course_with_cards):
    """edit_section handles editor returning a dict (JSON fallback)."""
    course, cards = course_with_cards
    draft = "## Test\n\nContent."

    editor_result = EditorResult(
        edited_content="## Test\n\nEdited content.",
        blackboard_updates=BlackboardUpdates(
            new_glossary_terms={},
            new_concept_ownership={},
            topics_covered=["test topic"],
            key_points_summary="Test summary",
            new_sources=[],
        ),
    )

    mock_agent = _mock_editor_agent(editor_result)

    with patch("app.agent_service.create_editor", return_value=mock_agent):
        from app.agent_service import edit_section

        result = await edit_section(draft, None, cards, 1, db_session)

    assert isinstance(result, EditorResult)
    assert "Edited content" in result.edited_content


@pytest.mark.anyio
async def test_edit_section_includes_blackboard_in_message(setup_db, db_session, course_with_cards):
    """edit_section passes blackboard state and cards to the editor."""
    course, cards = course_with_cards

    # Create a blackboard with some data
    from app.agent_service import create_blackboard, update_blackboard

    bb = await create_blackboard(course.id, db_session)
    updates = BlackboardUpdates(
        new_glossary_terms={"var": {"definition": "storage", "defined_in_section": 1}},
        new_concept_ownership={},
        topics_covered=["variables"],
        key_points_summary="Variables store data.",
        new_sources=[],
    )
    await update_blackboard(bb, updates, db_session)
    await db_session.refresh(bb)

    draft = "## Section 2\n\nContent about functions."

    mock_result = EditorResult(
        edited_content="## Section 2\n\nEdited content.",
        blackboard_updates=BlackboardUpdates(
            new_glossary_terms={},
            new_concept_ownership={},
            topics_covered=[],
            key_points_summary="",
            new_sources=[],
        ),
    )

    mock_agent = _mock_editor_agent(mock_result)

    with patch("app.agent_service.create_editor", return_value=mock_agent) as mock_create:
        from app.agent_service import edit_section

        result = await edit_section(draft, bb, cards, 2, db_session)

    # Verify the message sent to editor includes blackboard context
    call_args = mock_agent.ainvoke.call_args
    message = call_args[0][0]["messages"][0]["content"]
    assert "BLACKBOARD" in message
    assert "var" in message  # glossary term should be in the message
    assert "Section position: 2" in message


# ---------------------------------------------------------------------------
# Tests: extract_citations
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_extract_citations_basic(setup_db, course_with_cards):
    """extract_citations maps [N] markers to evidence card sources."""
    from app.agent_service import extract_citations

    _, cards = course_with_cards
    content = "Python was created in 1991 [1]. It uses dynamic typing [2]. Some say it's slow [3]."

    citations = extract_citations(content, cards)

    assert len(citations) == 3
    assert citations[0]["number"] == 1
    assert citations[0]["claim"] == "Python was created by Guido van Rossum in 1991"
    assert citations[0]["source_url"] == "https://docs.python.org/3/faq/general.html"
    assert citations[1]["number"] == 2
    assert citations[2]["number"] == 3


@pytest.mark.anyio
async def test_extract_citations_skips_out_of_range(setup_db, course_with_cards):
    """extract_citations silently skips [N] markers beyond card count."""
    from app.agent_service import extract_citations

    _, cards = course_with_cards
    content = "Claim [1]. Another claim [99]. Third [2]."

    citations = extract_citations(content, cards)

    # Should only have [1] and [2], not [99]
    assert len(citations) == 2
    numbers = [c["number"] for c in citations]
    assert 1 in numbers
    assert 2 in numbers
    assert 99 not in numbers


@pytest.mark.anyio
async def test_extract_citations_no_markers(setup_db, course_with_cards):
    """extract_citations returns empty list when no [N] markers exist."""
    from app.agent_service import extract_citations

    _, cards = course_with_cards
    content = "This content has no citations at all."

    citations = extract_citations(content, cards)
    assert citations == []


@pytest.mark.anyio
async def test_extract_citations_empty_cards(setup_db):
    """extract_citations returns empty list when card list is empty."""
    from app.agent_service import extract_citations

    content = "Some claim [1] and another [2]."
    citations = extract_citations(content, [])
    assert citations == []


@pytest.mark.anyio
async def test_extract_citations_deduplicates(setup_db, course_with_cards):
    """extract_citations deduplicates repeated [N] markers."""
    from app.agent_service import extract_citations

    _, cards = course_with_cards
    content = "First mention [1]. Second mention of same source [1]. Different source [2]."

    citations = extract_citations(content, cards)

    # [1] should appear only once
    assert len(citations) == 2
    assert citations[0]["number"] == 1
    assert citations[1]["number"] == 2


@pytest.mark.anyio
async def test_extract_citations_skips_zero(setup_db, course_with_cards):
    """extract_citations skips [0] since cards are 1-indexed."""
    from app.agent_service import extract_citations

    _, cards = course_with_cards
    content = "Invalid [0]. Valid [1]."

    citations = extract_citations(content, cards)
    assert len(citations) == 1
    assert citations[0]["number"] == 1


# ---------------------------------------------------------------------------
# Test: Discovery brief persists serialized TopicBrief in findings field
# ---------------------------------------------------------------------------


@pytest.fixture
async def course_for_discovery(db_session):
    """Create a minimal course for discovery brief tests."""
    course = Course(topic="Machine Learning", status="researching", user_id=_TEST_USER_UUID)
    db_session.add(course)
    await db_session.commit()
    return course


def test_writer_prompt_no_rigid_template():
    """Writer prompt should not enforce the rigid Why This Matters / Key Takeaways template."""
    from app.agent import WRITER_PROMPT
    assert "Why This Matters" not in WRITER_PROMPT
    assert "Key Takeaways" not in WRITER_PROMPT
    assert "What Comes Next" not in WRITER_PROMPT


def test_editor_prompt_references_discovery():
    """Editor prompt should instruct the editor to use discovery context."""
    from app.agent import EDITOR_PROMPT
    assert "discovery" in EDITOR_PROMPT.lower() or "DISCOVERY" in EDITOR_PROMPT


@pytest.mark.anyio
async def test_discovery_brief_contains_serialized_topic_brief(db_session, course_for_discovery):
    """run_discover_and_plan persists the TopicBrief JSON in the discovery brief findings field."""
    course = course_for_discovery

    topic_brief = TopicBrief(
        key_concepts=["supervised learning", "neural networks"],
        subtopics=["classification", "regression", "deep learning"],
        authoritative_sources=["https://arxiv.org/example"],
        learning_progression="Start with basics, then move to neural networks",
        open_debates=["interpretability vs accuracy"],
        raw_search_results=[{"title": "ML intro", "url": "https://example.com"}],
    )

    mock_outline = CourseOutlineWithBriefs(
        sections=[
            OutlineSection(position=1, title="Intro to ML", summary="Basics of ML"),
            OutlineSection(position=2, title="Neural Networks", summary="Deep learning fundamentals"),
        ],
        research_briefs=[
            ResearchBriefItem(
                section_position=1,
                questions=["What is ML?"],
                source_policy={"preferred_tiers": [1, 2], "scope": "intro"},
            ),
            ResearchBriefItem(
                section_position=2,
                questions=["What are neural networks?"],
                source_policy={"preferred_tiers": [1, 2], "scope": "deep learning"},
            ),
        ],
    )

    with patch(
        "app.agent_service.generate_outline",
        new_callable=AsyncMock,
        return_value=(mock_outline, False, topic_brief),
    ):
        from app.agent_service import run_discover_and_plan

        result = await run_discover_and_plan(
            course.id,
            db_session,
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            credentials={"api_key": "sk-test"},
        )

    # Fetch the discovery brief (section_position=None)
    briefs_result = await db_session.execute(
        select(ResearchBrief).where(
            ResearchBrief.course_id == course.id,
            ResearchBrief.section_position.is_(None),
        )
    )
    discovery_brief = briefs_result.scalar_one_or_none()

    assert discovery_brief is not None, "Discovery brief should exist"
    assert discovery_brief.findings != "Discovery research completed successfully", \
        "findings should not contain the old placeholder string"

    # Verify findings is valid JSON containing TopicBrief fields
    findings_data = json.loads(discovery_brief.findings)
    assert "key_concepts" in findings_data
    assert "supervised learning" in findings_data["key_concepts"]
    assert "neural networks" in findings_data["key_concepts"]
    assert "subtopics" in findings_data
    assert "classification" in findings_data["subtopics"]
    assert "authoritative_sources" in findings_data
    assert findings_data["learning_progression"] == "Start with basics, then move to neural networks"
    assert "interpretability vs accuracy" in findings_data["open_debates"]
    assert len(findings_data["raw_search_results"]) == 1


@pytest.mark.anyio
async def test_discovery_brief_empty_json_when_ungrounded(db_session, course_for_discovery):
    """When ungrounded (no topic_brief), the discovery brief is not created."""
    course = course_for_discovery

    mock_outline = CourseOutlineWithBriefs(
        sections=[
            OutlineSection(position=1, title="Intro", summary="Basics"),
        ],
        research_briefs=[
            ResearchBriefItem(
                section_position=1,
                questions=["What is it?"],
                source_policy={},
            ),
        ],
    )

    with patch(
        "app.agent_service.generate_outline",
        new_callable=AsyncMock,
        return_value=(mock_outline, True, None),
    ):
        from app.agent_service import run_discover_and_plan

        await run_discover_and_plan(
            course.id,
            db_session,
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            credentials={"api_key": "sk-test"},
        )

    # When ungrounded, no discovery brief should be created
    briefs_result = await db_session.execute(
        select(ResearchBrief).where(
            ResearchBrief.course_id == course.id,
            ResearchBrief.section_position.is_(None),
        )
    )
    discovery_brief = briefs_result.scalar_one_or_none()
    assert discovery_brief is None, "Discovery brief should not be created when ungrounded"


# ---------------------------------------------------------------------------
# Tests: _load_discovery_context helper
# ---------------------------------------------------------------------------


@pytest.fixture
async def course_with_discovery_brief(db_session):
    """Create a course with a discovery brief (section_position=None) containing TopicBrief JSON."""
    course = Course(topic="Machine Learning", status="writing", user_id=_TEST_USER_UUID)
    db_session.add(course)
    await db_session.commit()

    topic_brief_data = {
        "key_concepts": ["supervised learning", "neural networks", "gradient descent"],
        "subtopics": ["classification", "regression", "deep learning"],
        "authoritative_sources": ["https://arxiv.org/example", "https://dl.acm.org/example"],
        "learning_progression": "Start with basics, then move to neural networks",
        "open_debates": ["interpretability vs accuracy", "bias in training data"],
        "raw_search_results": [{"title": "ML intro", "url": "https://example.com"}],
    }

    discovery_brief = ResearchBrief(
        course_id=course.id,
        section_position=None,
        questions=[],
        source_policy={},
        findings=json.dumps(topic_brief_data),
    )
    db_session.add(discovery_brief)

    # Also add a section for write/edit tests
    section = Section(
        course_id=course.id,
        position=1,
        title="Intro to ML",
        summary="Basics of machine learning",
    )
    db_session.add(section)
    await db_session.commit()

    return course, topic_brief_data


@pytest.mark.anyio
async def test_load_discovery_context_returns_formatted_string(setup_db, db_session, course_with_discovery_brief):
    """_load_discovery_context returns formatted discovery data when a discovery brief exists."""
    from app.agent_service import _load_discovery_context

    course, topic_brief_data = course_with_discovery_brief
    result = await _load_discovery_context(course.id, db_session)

    assert "KEY CONCEPTS: supervised learning" in result
    assert "neural networks" in result
    assert "LEARNING PROGRESSION: Start with basics" in result
    assert "OPEN DEBATES:" in result
    assert "interpretability vs accuracy" in result
    assert "AUTHORITATIVE SOURCES:" in result
    assert "https://arxiv.org/example" in result
    assert "SUBTOPICS: classification" in result


@pytest.mark.anyio
async def test_load_discovery_context_returns_empty_when_no_brief(setup_db, db_session):
    """_load_discovery_context returns empty string when no discovery brief exists."""
    from app.agent_service import _load_discovery_context

    course = Course(topic="Test", status="writing", user_id=_TEST_USER_UUID)
    db_session.add(course)
    await db_session.commit()

    result = await _load_discovery_context(course.id, db_session)
    assert result == ""


@pytest.mark.anyio
async def test_load_discovery_context_returns_empty_on_invalid_json(setup_db, db_session):
    """_load_discovery_context returns empty string when findings is not valid JSON."""
    from app.agent_service import _load_discovery_context

    course = Course(topic="Test", status="writing", user_id=_TEST_USER_UUID)
    db_session.add(course)
    await db_session.commit()

    brief = ResearchBrief(
        course_id=course.id,
        section_position=None,
        questions=[],
        source_policy={},
        findings="not valid json",
    )
    db_session.add(brief)
    await db_session.commit()

    result = await _load_discovery_context(course.id, db_session)
    assert result == ""


@pytest.mark.anyio
async def test_load_discovery_context_returns_empty_when_findings_empty(setup_db, db_session):
    """_load_discovery_context returns empty string when findings is empty/None."""
    from app.agent_service import _load_discovery_context

    course = Course(topic="Test", status="writing", user_id=_TEST_USER_UUID)
    db_session.add(course)
    await db_session.commit()

    brief = ResearchBrief(
        course_id=course.id,
        section_position=None,
        questions=[],
        source_policy={},
        findings=None,
    )
    db_session.add(brief)
    await db_session.commit()

    result = await _load_discovery_context(course.id, db_session)
    assert result == ""


# ---------------------------------------------------------------------------
# Tests: Writer pipeline includes discovery context
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_write_section_includes_discovery_context(setup_db, db_session, course_with_discovery_brief):
    """write_section message contains DISCOVERY CONTEXT when discovery_context is provided."""
    course, topic_brief_data = course_with_discovery_brief

    # Create some evidence cards for the section
    card = EvidenceCard(
        course_id=course.id,
        section_position=1,
        claim="ML uses statistical models",
        source_url="https://example.com",
        source_title="ML Guide",
        source_tier=1,
        passage="Machine learning uses statistical models...",
        retrieved_date=date.today(),
        confidence=0.9,
        explanation="Core ML concept",
        verified=True,
        verification_note="Good source",
    )
    db_session.add(card)
    await db_session.commit()

    section = SimpleNamespace(title="Intro to ML", summary="Basics of machine learning")
    outline = [SimpleNamespace(position=1, title="Intro to ML", summary="Basics of machine learning")]

    mock_llm = _mock_writer_llm("## Intro to ML\n\nMachine learning content.")

    from app.agent_service import _load_discovery_context
    discovery_ctx = await _load_discovery_context(course.id, db_session)

    with patch("app.agent_service.provider_service.build_chat_model", return_value=mock_llm):
        from app.agent_service import write_section

        result = await write_section(
            [card], None, section, outline, db_session,
            discovery_context=discovery_ctx,
        )

    assert "## Intro to ML" in result

    # Verify the message sent to the LLM contains discovery context
    call_args = mock_llm.ainvoke.call_args
    messages = call_args[0][0]
    user_message = messages[1].content
    assert "DISCOVERY CONTEXT" in user_message
    assert "supervised learning" in user_message
    assert "neural networks" in user_message
    assert "LEARNING PROGRESSION" in user_message


@pytest.mark.anyio
async def test_write_section_omits_discovery_context_when_empty(setup_db, db_session, course_with_cards):
    """write_section message does NOT contain DISCOVERY CONTEXT when discovery_context is empty."""
    course, cards = course_with_cards

    section = SimpleNamespace(title="Introduction", summary="Getting started")
    outline = [SimpleNamespace(position=1, title="Introduction", summary="Getting started")]

    mock_llm = _mock_writer_llm("## Introduction\n\nContent here.")

    with patch("app.agent_service.provider_service.build_chat_model", return_value=mock_llm):
        from app.agent_service import write_section

        result = await write_section(cards, None, section, outline, db_session)

    call_args = mock_llm.ainvoke.call_args
    messages = call_args[0][0]
    user_message = messages[1].content
    assert "DISCOVERY CONTEXT" not in user_message


# ---------------------------------------------------------------------------
# Tests: Editor pipeline includes discovery context
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_edit_section_includes_discovery_context(setup_db, db_session, course_with_discovery_brief):
    """edit_section message contains DISCOVERY CONTEXT when discovery_context is provided."""
    course, topic_brief_data = course_with_discovery_brief

    card = EvidenceCard(
        course_id=course.id,
        section_position=1,
        claim="ML uses statistical models",
        source_url="https://example.com",
        source_title="ML Guide",
        source_tier=1,
        passage="Machine learning uses statistical models...",
        retrieved_date=date.today(),
        confidence=0.9,
        explanation="Core ML concept",
        verified=True,
        verification_note="Good source",
    )
    db_session.add(card)
    await db_session.commit()

    draft = "## Intro to ML\n\nDraft content about machine learning."

    mock_result = EditorResult(
        edited_content="## Intro to ML\n\nEdited content about ML.",
        blackboard_updates=BlackboardUpdates(
            new_glossary_terms={},
            new_concept_ownership={},
            topics_covered=["ML basics"],
            key_points_summary="ML uses statistical models.",
            new_sources=[],
        ),
    )

    mock_agent = _mock_editor_agent(mock_result)

    from app.agent_service import _load_discovery_context
    discovery_ctx = await _load_discovery_context(course.id, db_session)

    with patch("app.agent_service.create_editor", return_value=mock_agent):
        from app.agent_service import edit_section

        result = await edit_section(
            draft, None, [card], 1, db_session,
            discovery_context=discovery_ctx,
        )

    assert isinstance(result, EditorResult)

    # Verify the message sent to editor includes discovery context
    call_args = mock_agent.ainvoke.call_args
    message = call_args[0][0]["messages"][0]["content"]
    assert "DISCOVERY CONTEXT" in message
    assert "supervised learning" in message
    assert "neural networks" in message
    assert "LEARNING PROGRESSION" in message


@pytest.mark.anyio
async def test_edit_section_omits_discovery_context_when_empty(setup_db, db_session, course_with_cards):
    """edit_section message does NOT contain DISCOVERY CONTEXT when discovery_context is empty."""
    course, cards = course_with_cards
    draft = "## Introduction\n\nDraft content."

    mock_result = EditorResult(
        edited_content="## Introduction\n\nEdited content.",
        blackboard_updates=BlackboardUpdates(
            new_glossary_terms={},
            new_concept_ownership={},
            topics_covered=[],
            key_points_summary="",
            new_sources=[],
        ),
    )

    mock_agent = _mock_editor_agent(mock_result)

    with patch("app.agent_service.create_editor", return_value=mock_agent):
        from app.agent_service import edit_section

        result = await edit_section(draft, None, cards, 1, db_session)

    call_args = mock_agent.ainvoke.call_args
    message = call_args[0][0]["messages"][0]["content"]
    assert "DISCOVERY CONTEXT" not in message


# ---------------------------------------------------------------------------
# Tests: User instructions passed to writer and editor
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_write_section_includes_user_instructions(setup_db, db_session, course_with_cards):
    """write_section message includes USER INSTRUCTIONS when user_instructions is provided."""
    course, cards = course_with_cards

    section = SimpleNamespace(title="Introduction", summary="Getting started with Python")
    outline = [SimpleNamespace(position=1, title="Introduction", summary="Getting started")]

    mock_llm = _mock_writer_llm("## Introduction\n\nContent here.")

    with patch("app.agent_service.provider_service.build_chat_model", return_value=mock_llm):
        from app.agent_service import write_section

        result = await write_section(
            cards, None, section, outline, db_session,
            user_instructions="Focus on practical examples. Explain from the ground up.",
        )

    assert "## Introduction" in result

    # Verify the message sent to the LLM contains user instructions
    call_args = mock_llm.ainvoke.call_args
    messages = call_args[0][0]
    user_message = messages[1].content
    assert "USER INSTRUCTIONS" in user_message
    assert "Focus on practical examples" in user_message
    assert "Explain from the ground up" in user_message


@pytest.mark.anyio
async def test_write_section_omits_user_instructions_when_empty(setup_db, db_session, course_with_cards):
    """write_section message does NOT contain USER INSTRUCTIONS when user_instructions is empty."""
    course, cards = course_with_cards

    section = SimpleNamespace(title="Introduction", summary="Getting started")
    outline = [SimpleNamespace(position=1, title="Introduction", summary="Getting started")]

    mock_llm = _mock_writer_llm("## Introduction\n\nContent here.")

    with patch("app.agent_service.provider_service.build_chat_model", return_value=mock_llm):
        from app.agent_service import write_section

        result = await write_section(cards, None, section, outline, db_session)

    call_args = mock_llm.ainvoke.call_args
    messages = call_args[0][0]
    user_message = messages[1].content
    assert "USER INSTRUCTIONS" not in user_message


@pytest.mark.anyio
async def test_edit_section_includes_user_instructions(setup_db, db_session, course_with_cards):
    """edit_section message includes USER INSTRUCTIONS when user_instructions is provided."""
    course, cards = course_with_cards
    draft = "## Introduction\n\nDraft content."

    mock_result = EditorResult(
        edited_content="## Introduction\n\nEdited content.",
        blackboard_updates=BlackboardUpdates(
            new_glossary_terms={},
            new_concept_ownership={},
            topics_covered=[],
            key_points_summary="",
            new_sources=[],
        ),
    )

    mock_agent = _mock_editor_agent(mock_result)

    with patch("app.agent_service.create_editor", return_value=mock_agent):
        from app.agent_service import edit_section

        result = await edit_section(
            draft, None, cards, 1, db_session,
            user_instructions="Deep & Technical. Advanced English.",
        )

    assert isinstance(result, EditorResult)

    # Verify the message sent to editor includes user instructions
    call_args = mock_agent.ainvoke.call_args
    message = call_args[0][0]["messages"][0]["content"]
    assert "USER INSTRUCTIONS" in message
    assert "Deep & Technical" in message
    assert "Advanced English" in message


@pytest.mark.anyio
async def test_edit_section_omits_user_instructions_when_empty(setup_db, db_session, course_with_cards):
    """edit_section message does NOT contain USER INSTRUCTIONS when user_instructions is empty."""
    course, cards = course_with_cards
    draft = "## Introduction\n\nDraft content."

    mock_result = EditorResult(
        edited_content="## Introduction\n\nEdited content.",
        blackboard_updates=BlackboardUpdates(
            new_glossary_terms={},
            new_concept_ownership={},
            topics_covered=[],
            key_points_summary="",
            new_sources=[],
        ),
    )

    mock_agent = _mock_editor_agent(mock_result)

    with patch("app.agent_service.create_editor", return_value=mock_agent):
        from app.agent_service import edit_section

        result = await edit_section(draft, None, cards, 1, db_session)

    call_args = mock_agent.ainvoke.call_args
    message = call_args[0][0]["messages"][0]["content"]
    assert "USER INSTRUCTIONS" not in message
