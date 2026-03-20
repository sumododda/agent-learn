"""Tests for Phase 4: Verifier agent + re-research.

Tests cover:
- CardVerification / VerificationResult schema validation
- _format_cards_for_verifier: numbered card formatting
- verify_evidence: create_verifier invocation, DB updates, result handling
- _update_card_verification: DB persistence of verified/note fields
- research_section_targeted: Tavily advanced search, evidence extraction
"""

import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import event as sa_event

from app.agent import (
    CardVerification,
    VerificationResult,
    EvidenceCardItem,
    EvidenceCardSet,
)
from app.models import Base, Course, EvidenceCard, ResearchBrief


def _mock_structured_agent(structured_response):
    """Create a mock agent whose ainvoke returns a structured_response."""
    mock_agent = AsyncMock()
    mock_agent.ainvoke.return_value = {
        "structured_response": structured_response,
        "messages": [],
    }
    return mock_agent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_db_cards():
    """EvidenceCard ORM instances (not yet persisted)."""
    course_id = uuid.uuid4()
    return course_id, [
        EvidenceCard(
            id=uuid.uuid4(),
            course_id=course_id,
            section_position=1,
            claim="Python was created by Guido van Rossum in 1991",
            source_url="https://docs.python.org/3/faq/general.html",
            source_title="Python FAQ",
            source_tier=1,
            passage="Python was conceived in the late 1980s by Guido van Rossum...",
            retrieved_date=date.today(),
            confidence=0.95,
            caveat=None,
            explanation="Foundational fact about Python's origin",
            verified=False,
            verification_note=None,
        ),
        EvidenceCard(
            id=uuid.uuid4(),
            course_id=course_id,
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
            verified=False,
            verification_note=None,
        ),
        EvidenceCard(
            id=uuid.uuid4(),
            course_id=course_id,
            section_position=1,
            claim="Python is popular for data science",
            source_url="https://stackoverflow.com/questions/12345",
            source_title="SO: Python for Data Science",
            source_tier=3,
            passage="According to surveys, Python leads in data science...",
            retrieved_date=date.today(),
            confidence=0.7,
            caveat="Popularity rankings vary by survey methodology",
            explanation="Motivational claim for learners",
            verified=False,
            verification_note=None,
        ),
    ]


@pytest.fixture
def sample_research_brief():
    """A ResearchBrief ORM object for testing."""
    return ResearchBrief(
        id=uuid.uuid4(),
        course_id=uuid.uuid4(),
        section_position=1,
        questions=[
            "What is Python?",
            "Who created Python?",
            "What are Python's key features?",
            "What is Python used for?",
        ],
        source_policy={"preferred_tiers": [1, 2], "scope": "introductory"},
    )


@pytest.fixture
def good_verification_result():
    """A VerificationResult where all cards pass and coverage is sufficient."""
    return VerificationResult(
        card_verifications=[
            CardVerification(card_index=0, verified=True, note="Strong official source"),
            CardVerification(card_index=1, verified=True, note="Reputable tutorial"),
            CardVerification(card_index=2, verified=True, note="Acceptable forum source"),
        ],
        needs_more_research=False,
        gaps=[],
    )


@pytest.fixture
def bad_verification_result():
    """A VerificationResult where coverage is insufficient."""
    return VerificationResult(
        card_verifications=[
            CardVerification(card_index=0, verified=True, note="Good"),
            CardVerification(card_index=1, verified=False, note="Passage doesn't support claim"),
            CardVerification(card_index=2, verified=False, note="Source too unreliable"),
        ],
        needs_more_research=True,
        gaps=["What are Python's key features?", "What is Python used for?"],
    )


@pytest.fixture
def mock_tavily_advanced_response():
    """Mock Tavily response for advanced search."""
    return {
        "results": [
            {
                "title": "Python Features - Official Docs",
                "url": "https://docs.python.org/3/tutorial/",
                "content": "Python's key features include simplicity, readability...",
                "score": 0.92,
            },
            {
                "title": "Python Use Cases",
                "url": "https://realpython.com/what-can-i-do-with-python/",
                "content": "Python is used in web development, data science, AI...",
                "score": 0.88,
            },
        ]
    }


# ---------------------------------------------------------------------------
# Tests: Schema validation
# ---------------------------------------------------------------------------


def test_card_verification_schema():
    """CardVerification validates required fields."""
    v = CardVerification(card_index=0, verified=True, note="Looks good")
    assert v.card_index == 0
    assert v.verified is True
    assert v.note == "Looks good"


def test_card_verification_note_optional():
    """CardVerification note defaults to None."""
    v = CardVerification(card_index=1, verified=False)
    assert v.note is None


def test_verification_result_schema():
    """VerificationResult validates all fields."""
    result = VerificationResult(
        card_verifications=[
            CardVerification(card_index=0, verified=True),
            CardVerification(card_index=1, verified=False, note="Bad source"),
        ],
        needs_more_research=True,
        gaps=["What is X?"],
    )
    assert len(result.card_verifications) == 2
    assert result.needs_more_research is True
    assert result.gaps == ["What is X?"]


def test_verification_result_empty_gaps():
    """VerificationResult with empty gaps list is valid."""
    result = VerificationResult(
        card_verifications=[],
        needs_more_research=False,
        gaps=[],
    )
    assert result.gaps == []
    assert result.needs_more_research is False


# ---------------------------------------------------------------------------
# Tests: _format_cards_for_verifier
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_format_cards_for_verifier(setup_db, sample_db_cards):
    """_format_cards_for_verifier produces a numbered card list."""
    from app.agent_service import _format_cards_for_verifier

    _, cards = sample_db_cards
    formatted = _format_cards_for_verifier(cards)

    # Should contain all three cards
    assert "[Card 0]" in formatted
    assert "[Card 1]" in formatted
    assert "[Card 2]" in formatted

    # Should contain card fields
    assert "Python was created by Guido van Rossum" in formatted
    assert "https://docs.python.org/3/faq/general.html" in formatted
    assert "Confidence: 0.95" in formatted
    assert "Source Tier: 1" in formatted

    # Should include caveat text and "None" for missing caveats
    assert "Type hints added in Python 3.5" in formatted
    assert "Caveat: None" in formatted


@pytest.mark.anyio
async def test_format_cards_for_verifier_empty(setup_db):
    """_format_cards_for_verifier handles empty list."""
    from app.agent_service import _format_cards_for_verifier

    formatted = _format_cards_for_verifier([])
    assert formatted == ""


# ---------------------------------------------------------------------------
# Tests: verify_evidence
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_verify_evidence_good_set(setup_db, sample_research_brief, good_verification_result):
    """verify_evidence marks all cards as verified when verifier approves them."""
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
        # Create course and cards in DB
        course = Course(topic="Python", status="researching")
        session.add(course)
        await session.commit()

        db_cards = [
            EvidenceCard(
                course_id=course.id,
                section_position=1,
                claim=f"Claim {i}",
                source_url=f"https://example.com/{i}",
                source_title=f"Source {i}",
                source_tier=1,
                passage=f"Passage {i}",
                retrieved_date=date.today(),
                confidence=0.9,
                explanation=f"Explanation {i}",
            )
            for i in range(3)
        ]
        session.add_all(db_cards)
        await session.commit()

        brief = ResearchBrief(
            course_id=course.id,
            section_position=1,
            questions=["Q1?", "Q2?"],
            source_policy={},
        )

        with patch(
            "app.agent_service.create_verifier",
            return_value=_mock_structured_agent(good_verification_result),
        ):
            from app.agent_service import verify_evidence

            result = await verify_evidence(db_cards, brief, session)

        assert isinstance(result, VerificationResult)
        assert result.needs_more_research is False
        assert len(result.gaps) == 0

        # Cards should be marked verified in memory
        assert all(c.verified for c in db_cards)

        # Cards should be updated in DB
        db_result = await session.execute(
            select(EvidenceCard).where(EvidenceCard.course_id == course.id)
        )
        saved_cards = db_result.scalars().all()
        assert all(c.verified for c in saved_cards)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.anyio
async def test_verify_evidence_bad_set(setup_db, sample_research_brief, bad_verification_result):
    """verify_evidence rejects cards and flags gaps when coverage is insufficient."""
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

        db_cards = [
            EvidenceCard(
                course_id=course.id,
                section_position=1,
                claim=f"Claim {i}",
                source_url=f"https://example.com/{i}",
                source_title=f"Source {i}",
                source_tier=i + 1,
                passage=f"Passage {i}",
                retrieved_date=date.today(),
                confidence=0.9 - (i * 0.1),
                explanation=f"Explanation {i}",
            )
            for i in range(3)
        ]
        session.add_all(db_cards)
        await session.commit()

        brief = ResearchBrief(
            course_id=course.id,
            section_position=1,
            questions=["Q1?", "Q2?", "Q3?", "Q4?"],
            source_policy={},
        )

        with patch(
            "app.agent_service.create_verifier",
            return_value=_mock_structured_agent(bad_verification_result),
        ):
            from app.agent_service import verify_evidence

            result = await verify_evidence(db_cards, brief, session)

        assert result.needs_more_research is True
        assert len(result.gaps) == 2
        assert "What are Python's key features?" in result.gaps

        # Card 0 verified, cards 1 and 2 rejected
        assert db_cards[0].verified is True
        assert db_cards[1].verified is False
        assert db_cards[2].verified is False

        # Verification notes set
        assert db_cards[1].verification_note == "Passage doesn't support claim"
        assert db_cards[2].verification_note == "Source too unreliable"

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.anyio
async def test_verify_evidence_handles_dict_result(setup_db):
    """verify_evidence handles verifier returning a VerificationResult (always)."""
    engine = create_async_engine("sqlite+aiosqlite://")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        course = Course(topic="Python", status="researching")
        session.add(course)
        await session.commit()

        db_cards = [
            EvidenceCard(
                course_id=course.id,
                section_position=1,
                claim="Test claim",
                source_url="https://example.com",
                source_title="Test Source",
                source_tier=1,
                passage="Test passage",
                retrieved_date=date.today(),
                confidence=0.9,
                explanation="Test",
            )
        ]
        session.add_all(db_cards)
        await session.commit()

        brief = ResearchBrief(
            course_id=course.id,
            section_position=1,
            questions=["Q1?"],
            source_policy={},
        )

        verification_result = VerificationResult(
            card_verifications=[
                CardVerification(card_index=0, verified=True, note="OK"),
            ],
            needs_more_research=False,
            gaps=[],
        )

        with patch(
            "app.agent_service.create_verifier",
            return_value=_mock_structured_agent(verification_result),
        ):
            from app.agent_service import verify_evidence

            result = await verify_evidence(db_cards, brief, session)

        assert isinstance(result, VerificationResult)
        assert db_cards[0].verified is True

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.anyio
async def test_verify_evidence_skips_out_of_range_index(setup_db):
    """verify_evidence skips card_index that is out of range."""
    engine = create_async_engine("sqlite+aiosqlite://")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        course = Course(topic="Python", status="researching")
        session.add(course)
        await session.commit()

        db_cards = [
            EvidenceCard(
                course_id=course.id,
                section_position=1,
                claim="Only card",
                source_url="https://example.com",
                source_title="Test",
                source_tier=1,
                passage="Test",
                retrieved_date=date.today(),
                confidence=0.9,
                explanation="Test",
            )
        ]
        session.add_all(db_cards)
        await session.commit()

        brief = ResearchBrief(
            course_id=course.id,
            section_position=1,
            questions=["Q1?"],
            source_policy={},
        )

        # card_index=99 is out of range -- should be skipped safely
        result_data = VerificationResult(
            card_verifications=[
                CardVerification(card_index=0, verified=True, note="OK"),
                CardVerification(card_index=99, verified=False, note="Ghost card"),
            ],
            needs_more_research=False,
            gaps=[],
        )

        with patch(
            "app.agent_service.create_verifier",
            return_value=_mock_structured_agent(result_data),
        ):
            from app.agent_service import verify_evidence

            result = await verify_evidence(db_cards, brief, session)

        # Card 0 should be verified, out-of-range index skipped without error
        assert db_cards[0].verified is True
        assert result.needs_more_research is False

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ---------------------------------------------------------------------------
# Tests: research_section_targeted
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_research_section_targeted_returns_cards(
    setup_db, mock_tavily_advanced_response
):
    """research_section_targeted searches gaps with advanced depth and returns cards."""
    new_cards = [
        EvidenceCardItem(
            claim="Python features include readability",
            source_url="https://docs.python.org/3/tutorial/",
            source_title="Python Tutorial",
            source_tier=1,
            passage="Python's key features include simplicity, readability...",
            confidence=0.92,
            explanation="Answers gap about key features",
        ),
    ]
    mock_card_set = EvidenceCardSet(cards=new_cards)

    with (
        patch("tavily.AsyncTavilyClient") as mock_tavily_cls,
        patch(
            "app.agent_service.create_section_researcher",
            return_value=_mock_structured_agent(mock_card_set),
        ),
    ):
        mock_client = AsyncMock()
        mock_client.search = AsyncMock(return_value=mock_tavily_advanced_response)
        mock_tavily_cls.return_value = mock_client

        from app.agent_service import research_section_targeted

        gaps = ["What are Python's key features?", "What is Python used for?"]
        result = await research_section_targeted(gaps)

    # Should return evidence cards
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0].claim == "Python features include readability"

    # Tavily should be called once per gap with advanced search
    assert mock_client.search.call_count == 2
    # Check search_depth="advanced" and max_results=3
    for call in mock_client.search.call_args_list:
        assert call.kwargs["search_depth"] == "advanced"
        assert call.kwargs["max_results"] == 3


@pytest.mark.anyio
async def test_research_section_targeted_handles_all_failures(setup_db):
    """research_section_targeted returns empty list when all Tavily searches fail."""
    with patch("tavily.AsyncTavilyClient") as mock_tavily_cls:
        mock_client = AsyncMock()
        mock_client.search = AsyncMock(side_effect=RuntimeError("Tavily down"))
        mock_tavily_cls.return_value = mock_client

        from app.agent_service import research_section_targeted

        result = await research_section_targeted(["gap1", "gap2"])

    # Should return empty list, not raise
    assert result == []


@pytest.mark.anyio
async def test_research_section_targeted_partial_failure(
    setup_db, mock_tavily_advanced_response
):
    """research_section_targeted continues when some searches fail."""
    new_cards = [
        EvidenceCardItem(
            claim="Partial result",
            source_url="https://example.com",
            source_title="Source",
            source_tier=1,
            passage="Content",
            confidence=0.85,
            explanation="Partial",
        ),
    ]
    mock_card_set = EvidenceCardSet(cards=new_cards)

    with (
        patch("tavily.AsyncTavilyClient") as mock_tavily_cls,
        patch(
            "app.agent_service.create_section_researcher",
            return_value=_mock_structured_agent(mock_card_set),
        ),
    ):
        mock_client = AsyncMock()
        mock_client.search = AsyncMock(
            side_effect=[
                RuntimeError("Fail"),
                mock_tavily_advanced_response,
            ]
        )
        mock_tavily_cls.return_value = mock_client

        from app.agent_service import research_section_targeted

        result = await research_section_targeted(["gap1", "gap2"])

    assert len(result) == 1


@pytest.mark.anyio
async def test_research_section_targeted_handles_dict_result(
    setup_db, mock_tavily_advanced_response
):
    """research_section_targeted handles section researcher returning EvidenceCardSet."""
    new_cards = [
        EvidenceCardItem(
            claim="Dict result card",
            source_url="https://example.com",
            source_title="Source",
            source_tier=1,
            passage="Content",
            confidence=0.9,
            explanation="Test",
        ),
    ]
    mock_card_set = EvidenceCardSet(cards=new_cards)

    with (
        patch("tavily.AsyncTavilyClient") as mock_tavily_cls,
        patch(
            "app.agent_service.create_section_researcher",
            return_value=_mock_structured_agent(mock_card_set),
        ),
    ):
        mock_client = AsyncMock()
        mock_client.search = AsyncMock(return_value=mock_tavily_advanced_response)
        mock_tavily_cls.return_value = mock_client

        from app.agent_service import research_section_targeted

        result = await research_section_targeted(["gap1"])

    assert len(result) == 1
    assert result[0].claim == "Dict result card"
