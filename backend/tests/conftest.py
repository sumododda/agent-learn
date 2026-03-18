import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import event

from app.main import app
from app.models import Base
from app.database import get_session
from app.agent import (
    CourseOutline,
    CourseOutlineWithBriefs,
    OutlineSection,
    CourseContent,
    SectionContent,
    ResearchBriefItem,
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
    yield

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    app.dependency_overrides.clear()


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
