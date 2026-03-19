"""Tests for Phase 3: Section researcher + evidence cards.

Tests cover:
- research_section: Tavily mocking, agent invocation, EvidenceCardItem output
- research_all_sections: parallel execution, error isolation, DB persistence
- save_evidence_cards: bulk insert, retrieved_date, field mapping
- get_evidence_cards: query by course_id + section_position
"""

import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.agent import EvidenceCardItem, EvidenceCardSet
from app.models import EvidenceCard, ResearchBrief


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_evidence_cards():
    """A list of EvidenceCardItem instances for testing."""
    return [
        EvidenceCardItem(
            claim="Python was created by Guido van Rossum in 1991",
            source_url="https://docs.python.org/3/faq/general.html",
            source_title="Python FAQ",
            source_tier=1,
            passage="Python was conceived in the late 1980s by Guido van Rossum...",
            confidence=0.95,
            caveat=None,
            explanation="Foundational fact about Python's origin",
        ),
        EvidenceCardItem(
            claim="Python uses dynamic typing",
            source_url="https://realpython.com/python-type-checking/",
            source_title="Python Type Checking Guide",
            source_tier=2,
            passage="Python is a dynamically typed language...",
            confidence=0.9,
            caveat="Type hints were added in Python 3.5 but are not enforced at runtime",
            explanation="Key language characteristic for the intro section",
        ),
        EvidenceCardItem(
            claim="Python is the most popular language for data science",
            source_url="https://stackoverflow.com/questions/12345",
            source_title="SO: Python for Data Science",
            source_tier=3,
            passage="According to surveys, Python leads in data science adoption...",
            confidence=0.7,
            caveat="Popularity rankings vary by survey methodology",
            explanation="Motivational claim for learners",
        ),
    ]


@pytest.fixture
def mock_tavily_response():
    """A mock Tavily search response dict."""
    return {
        "results": [
            {
                "title": "Python FAQ",
                "url": "https://docs.python.org/3/faq/general.html",
                "content": "Python was conceived in the late 1980s by Guido van Rossum...",
                "score": 0.95,
            },
            {
                "title": "Python Type Checking Guide",
                "url": "https://realpython.com/python-type-checking/",
                "content": "Python is a dynamically typed language...",
                "score": 0.88,
            },
        ]
    }


@pytest.fixture
def mock_research_brief(setup_db):
    """Create a ResearchBrief ORM object (requires DB setup from conftest)."""
    return ResearchBrief(
        id=uuid.uuid4(),
        course_id=uuid.uuid4(),
        section_position=1,
        questions=[
            "What is Python?",
            "Who created Python?",
            "What are Python's key features?",
        ],
        source_policy={"preferred_tiers": [1, 2], "scope": "introductory"},
    )


# ---------------------------------------------------------------------------
# Tests: research_section
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_research_section_returns_cards(
    mock_research_brief, mock_tavily_response, sample_evidence_cards
):
    """research_section calls Tavily for each question and returns EvidenceCardItems."""
    mock_card_set = EvidenceCardSet(cards=sample_evidence_cards)

    with (
        patch(
            "tavily.AsyncTavilyClient"
        ) as mock_tavily_cls,
        patch(
            "app.agent_service.create_section_researcher"
        ) as mock_create,
        patch(
            "app.agent_service._invoke_agent", new_callable=AsyncMock
        ) as mock_invoke,
    ):
        # Set up Tavily mock
        mock_client = AsyncMock()
        mock_client.search = AsyncMock(return_value=mock_tavily_response)
        mock_tavily_cls.return_value = mock_client

        # Set up agent mock
        mock_agent = MagicMock()
        mock_create.return_value = mock_agent
        mock_invoke.return_value = mock_card_set

        from app.agent_service import research_section

        result = await research_section(mock_research_brief)

    # Should return a list of EvidenceCardItem
    assert isinstance(result, list)
    assert len(result) == 3
    assert all(isinstance(c, EvidenceCardItem) for c in result)

    # Tavily should be called once per question
    assert mock_client.search.call_count == 3

    # Agent should be invoked once with all aggregated results
    mock_invoke.assert_called_once()


@pytest.mark.anyio
async def test_research_section_handles_partial_tavily_failure(
    mock_research_brief, mock_tavily_response, sample_evidence_cards
):
    """research_section continues when some Tavily searches fail."""
    mock_card_set = EvidenceCardSet(cards=sample_evidence_cards)

    with (
        patch(
            "tavily.AsyncTavilyClient"
        ) as mock_tavily_cls,
        patch(
            "app.agent_service.create_section_researcher"
        ) as mock_create,
        patch(
            "app.agent_service._invoke_agent", new_callable=AsyncMock
        ) as mock_invoke,
    ):
        mock_client = AsyncMock()
        # First call succeeds, second fails, third succeeds
        mock_client.search = AsyncMock(
            side_effect=[
                mock_tavily_response,
                RuntimeError("Tavily timeout"),
                mock_tavily_response,
            ]
        )
        mock_tavily_cls.return_value = mock_client

        mock_agent = MagicMock()
        mock_create.return_value = mock_agent
        mock_invoke.return_value = mock_card_set

        from app.agent_service import research_section

        result = await research_section(mock_research_brief)

    # Should still succeed with partial results
    assert len(result) == 3
    assert mock_client.search.call_count == 3


@pytest.mark.anyio
async def test_research_section_raises_when_all_tavily_fail(mock_research_brief):
    """research_section raises RuntimeError when all Tavily searches fail."""
    with patch(
        "tavily.AsyncTavilyClient"
    ) as mock_tavily_cls:
        mock_client = AsyncMock()
        mock_client.search = AsyncMock(side_effect=RuntimeError("Tavily down"))
        mock_tavily_cls.return_value = mock_client

        from app.agent_service import research_section

        with pytest.raises(RuntimeError, match="All Tavily searches failed"):
            await research_section(mock_research_brief)


@pytest.mark.anyio
async def test_research_section_handles_dict_result(
    mock_research_brief, mock_tavily_response, sample_evidence_cards
):
    """research_section handles agent returning a dict (JSON fallback)."""
    card_set_dict = {
        "cards": [c.model_dump() for c in sample_evidence_cards]
    }

    with (
        patch(
            "tavily.AsyncTavilyClient"
        ) as mock_tavily_cls,
        patch(
            "app.agent_service.create_section_researcher"
        ) as mock_create,
        patch(
            "app.agent_service._invoke_agent", new_callable=AsyncMock
        ) as mock_invoke,
    ):
        mock_client = AsyncMock()
        mock_client.search = AsyncMock(return_value=mock_tavily_response)
        mock_tavily_cls.return_value = mock_client

        mock_create.return_value = MagicMock()
        mock_invoke.return_value = card_set_dict

        from app.agent_service import research_section

        result = await research_section(mock_research_brief)

    assert isinstance(result, list)
    assert len(result) == 3


# ---------------------------------------------------------------------------
# Tests: research_all_sections (parallel orchestration)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_research_all_sections_parallel(setup_db):
    """research_all_sections runs research in parallel via asyncio.gather."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy import event as sa_event

    from app.models import Base, Course

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
        # Create a course
        course = Course(topic="Python", status="researching")
        session.add(course)
        await session.commit()
        course_id = course.id

        # Create section-level briefs
        briefs = [
            ResearchBrief(
                course_id=course_id,
                section_position=i,
                questions=[f"Question {i}a", f"Question {i}b"],
                source_policy={},
            )
            for i in range(1, 4)
        ]
        # Add a discovery brief (should be filtered out)
        discovery_brief = ResearchBrief(
            course_id=course_id,
            section_position=None,
            questions=[],
            source_policy={},
        )
        all_briefs = [discovery_brief] + briefs

        cards_section_1 = [
            EvidenceCardItem(
                claim=f"Claim 1-{j}",
                source_url=f"https://example.com/{j}",
                source_title=f"Source {j}",
                source_tier=1,
                passage=f"Passage {j}",
                confidence=0.9,
                explanation=f"Explanation {j}",
            )
            for j in range(1, 4)
        ]
        cards_section_2 = [
            EvidenceCardItem(
                claim="Claim 2-1",
                source_url="https://example.com/2",
                source_title="Source 2",
                source_tier=2,
                passage="Passage 2",
                confidence=0.8,
                explanation="Explanation 2",
            )
        ]

        with patch(
            "app.agent_service.research_section",
            new_callable=AsyncMock,
            side_effect=[
                cards_section_1,
                cards_section_2,
                RuntimeError("Section 3 research failed"),
            ],
        ):
            from app.agent_service import research_all_sections

            await research_all_sections(course_id, all_briefs, session)

        # Section 1 and 2 should have saved cards, section 3 should have none
        result_1 = await session.execute(
            select(EvidenceCard).where(
                EvidenceCard.course_id == course_id,
                EvidenceCard.section_position == 1,
            )
        )
        saved_1 = result_1.scalars().all()
        assert len(saved_1) == 3

        result_2 = await session.execute(
            select(EvidenceCard).where(
                EvidenceCard.course_id == course_id,
                EvidenceCard.section_position == 2,
            )
        )
        saved_2 = result_2.scalars().all()
        assert len(saved_2) == 1

        result_3 = await session.execute(
            select(EvidenceCard).where(
                EvidenceCard.course_id == course_id,
                EvidenceCard.section_position == 3,
            )
        )
        saved_3 = result_3.scalars().all()
        assert len(saved_3) == 0

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.anyio
async def test_research_all_sections_no_section_briefs(setup_db):
    """research_all_sections is a no-op when only discovery briefs exist."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

    engine = create_async_engine("sqlite+aiosqlite://")

    from app.models import Base, Course

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        course = Course(topic="Test", status="researching")
        session.add(course)
        await session.commit()

        briefs = [
            ResearchBrief(
                course_id=course.id,
                section_position=None,
                questions=[],
                source_policy={},
            )
        ]

        with patch(
            "app.agent_service.research_section", new_callable=AsyncMock
        ) as mock_research:
            from app.agent_service import research_all_sections

            await research_all_sections(course.id, briefs, session)

        # research_section should never be called
        mock_research.assert_not_called()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ---------------------------------------------------------------------------
# Tests: save_evidence_cards
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_save_evidence_cards(setup_db, sample_evidence_cards):
    """save_evidence_cards bulk inserts cards with correct fields."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy import event as sa_event

    from app.models import Base, Course

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
        course = Course(topic="Python", status="researching")
        session.add(course)
        await session.commit()

        from app.agent_service import save_evidence_cards

        await save_evidence_cards(course.id, 1, sample_evidence_cards, session)

        # Verify saved cards
        result = await session.execute(
            select(EvidenceCard).where(EvidenceCard.course_id == course.id)
        )
        saved = result.scalars().all()

        assert len(saved) == 3

        # Check field mapping for the first card
        card = next(c for c in saved if c.source_tier == 1)
        assert card.claim == "Python was created by Guido van Rossum in 1991"
        assert card.source_url == "https://docs.python.org/3/faq/general.html"
        assert card.source_title == "Python FAQ"
        assert card.section_position == 1
        assert card.confidence == 0.95
        assert card.caveat is None
        assert card.retrieved_date == date.today()
        assert card.verified is False

        # Check card with caveat
        card_with_caveat = next(c for c in saved if c.source_tier == 2)
        assert card_with_caveat.caveat is not None
        assert "Type hints" in card_with_caveat.caveat

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ---------------------------------------------------------------------------
# Tests: get_evidence_cards
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_evidence_cards(setup_db, sample_evidence_cards):
    """get_evidence_cards returns cards filtered by course_id and section_position."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy import event as sa_event

    from app.models import Base, Course

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
        course = Course(topic="Python", status="researching")
        session.add(course)
        await session.commit()

        from app.agent_service import save_evidence_cards, get_evidence_cards

        # Save cards for section 1 and section 2
        await save_evidence_cards(course.id, 1, sample_evidence_cards, session)
        await save_evidence_cards(
            course.id,
            2,
            [sample_evidence_cards[0]],  # just one card for section 2
            session,
        )

        # Query section 1
        section_1_cards = await get_evidence_cards(course.id, 1, session)
        assert len(section_1_cards) == 3

        # Query section 2
        section_2_cards = await get_evidence_cards(course.id, 2, session)
        assert len(section_2_cards) == 1

        # Query non-existent section
        section_99_cards = await get_evidence_cards(course.id, 99, session)
        assert len(section_99_cards) == 0

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ---------------------------------------------------------------------------
# Tests: EvidenceCardItem / EvidenceCardSet schema validation
# ---------------------------------------------------------------------------


def test_evidence_card_item_schema():
    """EvidenceCardItem validates all required fields."""
    card = EvidenceCardItem(
        claim="Test claim",
        source_url="https://example.com",
        source_title="Example",
        source_tier=1,
        passage="Test passage",
        confidence=0.85,
        explanation="Test explanation",
    )
    assert card.claim == "Test claim"
    assert card.caveat is None  # optional, defaults to None
    assert card.confidence == 0.85


def test_evidence_card_item_with_caveat():
    """EvidenceCardItem accepts optional caveat."""
    card = EvidenceCardItem(
        claim="Test claim",
        source_url="https://example.com",
        source_title="Example",
        source_tier=2,
        passage="Test passage",
        confidence=0.7,
        caveat="Only applies to version 3+",
        explanation="Test explanation",
    )
    assert card.caveat == "Only applies to version 3+"


def test_evidence_card_set_schema():
    """EvidenceCardSet wraps a list of EvidenceCardItem."""
    cards = [
        EvidenceCardItem(
            claim=f"Claim {i}",
            source_url=f"https://example.com/{i}",
            source_title=f"Source {i}",
            source_tier=1,
            passage=f"Passage {i}",
            confidence=0.9,
            explanation=f"Explanation {i}",
        )
        for i in range(5)
    ]
    card_set = EvidenceCardSet(cards=cards)
    assert len(card_set.cards) == 5


def test_evidence_card_set_empty():
    """EvidenceCardSet can be empty."""
    card_set = EvidenceCardSet(cards=[])
    assert len(card_set.cards) == 0
