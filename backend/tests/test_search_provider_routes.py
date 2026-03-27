"""Tests for search provider CRUD endpoints."""
import uuid

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.main import app
from app.database import get_session
from app.auth import get_current_user
from app.models import Base, ProviderConfig, User
from app.limiter import limiter

TEST_USER_UUID = uuid.UUID("00000000-0000-0000-0000-000000000002")
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
        session.add(User(id=TEST_USER_UUID, email="search@test.com", password_hash="fake-hash"))
        await session.commit()
        session.add(
            ProviderConfig(
                user_id=TEST_USER_UUID,
                provider="duckduckgo",
                encrypted_credentials="{}",
                credential_hint="No key required",
                is_default=True,
            )
        )
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
async def test_delete_duckduckgo_rejected(client):
    resp = await client.delete("/api/search-providers/duckduckgo")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "DuckDuckGo is built in and cannot be removed"
