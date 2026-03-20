import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import event

from app.main import app
from app.models import Base, Blackboard, EvidenceCard, ResearchBrief
from app.database import get_session
from app.auth import get_current_user
from app.limiter import limiter
from app.agent import (
    BlackboardUpdates,
    CardVerification,
    CourseOutline,
    CourseOutlineWithBriefs,
    EditorResult,
    EvidenceCardItem,
    EvidenceCardSet,
    OutlineSection,
    CourseContent,
    SectionContent,
    ResearchBriefItem,
    VerificationResult,
)

# Use sqlite for tests (requires aiosqlite)
TEST_DATABASE_URL = "sqlite+aiosqlite://"


@pytest.fixture(autouse=True)
async def setup_db():
    engine = create_async_engine(TEST_DATABASE_URL)

    # SQLite needs PRAGMA foreign_keys = ON for each connection
    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_current_user] = lambda: "test-user-id"
    limiter.enabled = False
    yield

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    app.dependency_overrides.clear()
    limiter.enabled = True


@pytest.fixture
def mock_outline():
    """Legacy CourseOutline fixture (for backward compatibility)."""
    return CourseOutline(sections=[
        OutlineSection(position=1, title="Introduction", summary="Getting started"),
        OutlineSection(position=2, title="Core Concepts", summary="Key ideas"),
        OutlineSection(position=3, title="Practice", summary="Hands-on exercises"),
    ])


@pytest.fixture
def mock_outline_with_briefs():
    """CourseOutlineWithBriefs fixture for M2 generate_outline tests."""
    return CourseOutlineWithBriefs(
        sections=[
            OutlineSection(position=1, title="Introduction", summary="Getting started"),
            OutlineSection(position=2, title="Core Concepts", summary="Key ideas"),
            OutlineSection(position=3, title="Practice", summary="Hands-on exercises"),
        ],
        research_briefs=[
            ResearchBriefItem(
                section_position=1,
                questions=["What is the topic?", "Why does it matter?", "What are prerequisites?"],
                source_policy={"preferred_tiers": [1, 2], "scope": "introductory material", "out_of_scope": "advanced topics"},
            ),
            ResearchBriefItem(
                section_position=2,
                questions=["What are the core concepts?", "How do they relate?", "What are common misconceptions?"],
                source_policy={"preferred_tiers": [1, 2], "scope": "fundamental concepts", "out_of_scope": "implementation details"},
            ),
            ResearchBriefItem(
                section_position=3,
                questions=["What exercises work best?", "What tools are needed?", "What are common mistakes?"],
                source_policy={"preferred_tiers": [2, 3], "scope": "practical exercises", "out_of_scope": "theory"},
            ),
        ],
    )


@pytest.fixture
def mock_content():
    return CourseContent(sections=[
        SectionContent(position=1, content="# Introduction\n\nWelcome to the course."),
        SectionContent(position=2, content="# Core Concepts\n\nHere are the key ideas."),
        SectionContent(position=3, content="# Practice\n\nLet's practice."),
    ])


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Phase 8: New fixtures for EvidenceCard, ResearchBrief, Blackboard
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_evidence_card_items():
    """A list of EvidenceCardItem Pydantic instances (not ORM) for test data."""
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
def sample_research_brief_orm():
    """A ResearchBrief ORM instance (not persisted, no course_id set)."""
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


@pytest.fixture
def sample_evidence_card_orm():
    """A single EvidenceCard ORM instance (not persisted)."""
    return EvidenceCard(
        id=uuid.uuid4(),
        course_id=uuid.uuid4(),
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
    )


@pytest.fixture
def sample_blackboard_updates():
    """A BlackboardUpdates Pydantic instance for testing blackboard merges."""
    return BlackboardUpdates(
        new_glossary_terms={
            "dynamic typing": {
                "definition": "Types are determined at runtime",
                "defined_in_section": 1,
            },
        },
        new_concept_ownership={"Python origins": 1, "typing system": 1},
        topics_covered=["Python history", "dynamic typing"],
        key_points_summary="Python uses dynamic typing with optional type hints.",
        new_sources=[
            {"url": "https://docs.python.org", "title": "Python Docs"},
        ],
    )


@pytest.fixture
def sample_verification_result_pass():
    """A VerificationResult where all cards pass."""
    return VerificationResult(
        card_verifications=[
            CardVerification(card_index=0, verified=True, note="Strong source"),
            CardVerification(card_index=1, verified=True, note="Reputable"),
            CardVerification(card_index=2, verified=True, note="Acceptable"),
        ],
        needs_more_research=False,
        gaps=[],
    )


@pytest.fixture
def sample_verification_result_fail():
    """A VerificationResult where coverage is insufficient."""
    return VerificationResult(
        card_verifications=[
            CardVerification(card_index=0, verified=True, note="Good"),
            CardVerification(card_index=1, verified=False, note="Passage contradicts claim"),
            CardVerification(card_index=2, verified=False, note="Source unreliable"),
        ],
        needs_more_research=True,
        gaps=["What are Python's key features?", "What is Python used for?"],
    )


@pytest.fixture
def sample_editor_result():
    """An EditorResult for testing the edit pipeline step."""
    return EditorResult(
        edited_content="## Introduction\n\nPython was created in 1991 [1]. It uses dynamic typing [2].",
        blackboard_updates=BlackboardUpdates(
            new_glossary_terms={
                "interpreter": {
                    "definition": "A program that executes code line by line",
                    "defined_in_section": 1,
                }
            },
            new_concept_ownership={"interpretation": 1},
            topics_covered=["Python basics", "interpretation"],
            key_points_summary="Python is an interpreted language.",
            new_sources=[
                {"url": "https://docs.python.org", "title": "Python Docs"},
            ],
        ),
    )


# ---------------------------------------------------------------------------
# Phase 8: Mock fixtures for agents
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_discovery_researcher():
    """A mock for create_discovery_researcher that returns a mock agent."""
    mock_agent = MagicMock()
    mock_agent.ainvoke = AsyncMock()
    return mock_agent


@pytest.fixture
def mock_section_researcher():
    """A mock for create_section_researcher that returns a mock agent."""
    mock_agent = MagicMock()
    mock_agent.ainvoke = AsyncMock()
    return mock_agent


@pytest.fixture
def mock_verifier_agent():
    """A mock for create_verifier that returns a mock agent."""
    mock_agent = MagicMock()
    mock_agent.ainvoke = AsyncMock()
    return mock_agent


@pytest.fixture
def mock_editor_agent():
    """A mock for create_editor that returns a mock agent."""
    mock_agent = MagicMock()
    mock_agent.ainvoke = AsyncMock()
    return mock_agent
