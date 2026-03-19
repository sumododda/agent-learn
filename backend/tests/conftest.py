import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import event

from app.main import app
from app.models import Base
from app.database import get_session
from app.agent import CourseOutline, OutlineSection, CourseContent, SectionContent

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
    return CourseOutline(sections=[
        OutlineSection(position=1, title="Introduction", summary="Getting started"),
        OutlineSection(position=2, title="Core Concepts", summary="Key ideas"),
        OutlineSection(position=3, title="Practice", summary="Hands-on exercises"),
    ])


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
