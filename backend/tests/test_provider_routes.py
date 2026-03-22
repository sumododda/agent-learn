"""Tests for OpenRouter provider CRUD endpoints."""
import json
import uuid

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.main import app
from app.database import get_session
from app.auth import get_current_user
from app.models import Base, User
from app.limiter import limiter

TEST_USER_UUID = uuid.UUID("00000000-0000-0000-0000-000000000001")
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
        session.add(User(id=TEST_USER_UUID, email="test@test.com", password_hash="fake-hash"))
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
async def test_list_empty(client):
    resp = await client.get("/api/providers")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_save_provider(client):
    resp = await client.post("/api/providers", json={
        "provider": "openrouter",
        "credentials": {"api_key": "sk-or-test1234"},
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["provider"] == "openrouter"
    assert data["credential_hint"] == "****1234"
    assert data["is_default"] is True
    assert "api_key" not in json.dumps(data)


@pytest.mark.asyncio
async def test_save_duplicate_rejects(client):
    await client.post("/api/providers", json={
        "provider": "openrouter",
        "credentials": {"api_key": "sk-or-test1234"},
    })
    resp = await client.post("/api/providers", json={
        "provider": "openrouter",
        "credentials": {"api_key": "sk-or-other"},
    })
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_update_credentials(client):
    await client.post("/api/providers", json={
        "provider": "openrouter",
        "credentials": {"api_key": "sk-or-old1111"},
    })
    resp = await client.put("/api/providers/openrouter", json={
        "credentials": {"api_key": "sk-or-new9999"},
    })
    assert resp.status_code == 200
    assert resp.json()["credential_hint"] == "****9999"


@pytest.mark.asyncio
async def test_update_extra_fields(client):
    await client.post("/api/providers", json={
        "provider": "openrouter",
        "credentials": {"api_key": "sk-or-test1234"},
    })
    resp = await client.put("/api/providers/openrouter", json={
        "extra_fields": {"model": "anthropic/claude-sonnet-4"},
    })
    assert resp.status_code == 200
    assert resp.json()["extra_fields"]["model"] == "anthropic/claude-sonnet-4"


@pytest.mark.asyncio
async def test_delete_provider(client):
    await client.post("/api/providers", json={
        "provider": "openrouter",
        "credentials": {"api_key": "sk-or-test5678"},
    })
    resp = await client.delete("/api/providers/openrouter")
    assert resp.status_code == 204
    resp = await client.get("/api/providers")
    assert len(resp.json()) == 0


@pytest.mark.asyncio
async def test_delete_nonexistent(client):
    resp = await client.delete("/api/providers/openrouter")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_returns_hint_not_key(client):
    await client.post("/api/providers", json={
        "provider": "openrouter",
        "credentials": {"api_key": "sk-or-secretkey9876"},
    })
    resp = await client.get("/api/providers")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["credential_hint"] == "****9876"
    assert "sk-or" not in json.dumps(data)
    assert "secretkey" not in json.dumps(data)
