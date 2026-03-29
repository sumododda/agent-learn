"""Tests for academic provider CRUD endpoints."""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import get_current_user
from app.database import get_session
from app.limiter import limiter
from app.main import app
from app.models import Base, User

TEST_USER_UUID = uuid.UUID("00000000-0000-0000-0000-000000000003")
TEST_USER_ID = str(TEST_USER_UUID)


@pytest.fixture(autouse=True)
async def setup_db():
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
        session.add(User(id=TEST_USER_UUID, email="academic@test.com", password_hash="fake-hash"))
        await session.commit()

    async def override_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_current_user] = lambda: TEST_USER_ID
    limiter.enabled = False
    yield

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    app.dependency_overrides.clear()
    limiter.enabled = True


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_test_academic_provider_sanitizes_exceptions(client):
    with patch(
        "app.search_service._search_openalex",
        new=AsyncMock(side_effect=RuntimeError("upstream auth failed: secret detail")),
    ):
        resp = await client.post(
            "/api/academic-providers/openalex/test",
            json={"provider": "openalex", "credentials": {"api_key": "test-key"}},
        )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Academic credential validation failed"
