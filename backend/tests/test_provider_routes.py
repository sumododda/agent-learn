"""Tests for provider CRUD API endpoints."""
import json
import uuid

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.main import app
from app.database import get_session
from app.auth import get_current_user
from app.models import Base, User
from app.limiter import limiter

TEST_USER_UUID = uuid.UUID("00000000-0000-0000-0000-000000000001")
TEST_USER_ID = str(TEST_USER_UUID)
TEST_PASSWORD = "test-password-secure-123"

# Speed up Argon2 for tests
import app.crypto as crypto_mod
crypto_mod._test_time_cost = 1
crypto_mod._test_memory_cost = 1024


@pytest.fixture(autouse=True)
async def setup_db():
    """Create a fresh in-memory SQLite DB with user and clean provider tables."""
    engine = create_async_engine("sqlite+aiosqlite://")

    @sa_event.listens_for(engine.sync_engine, "connect")
    def _set_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # Insert test user via ORM to handle UUID correctly
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
        "provider": "anthropic",
        "credentials": {"api_key": "sk-ant-test1234"},
        "extra_fields": {},
        "password": TEST_PASSWORD,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["provider"] == "anthropic"
    assert data["credential_hint"] == "****1234"
    assert "api_key" not in json.dumps(data)  # key never returned


@pytest.mark.asyncio
async def test_save_duplicate_rejects(client):
    await client.post("/api/providers", json={
        "provider": "anthropic",
        "credentials": {"api_key": "sk-ant-test1234"},
        "password": TEST_PASSWORD,
    })
    resp = await client.post("/api/providers", json={
        "provider": "anthropic",
        "credentials": {"api_key": "sk-ant-other"},
        "password": TEST_PASSWORD,
    })
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_save_invalid_provider(client):
    resp = await client.post("/api/providers", json={
        "provider": "invalid_provider",
        "credentials": {"api_key": "sk-test"},
        "password": TEST_PASSWORD,
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_update_credentials(client):
    await client.post("/api/providers", json={
        "provider": "mistral",
        "credentials": {"api_key": "sk-old-key1111"},
        "password": TEST_PASSWORD,
    })
    resp = await client.put("/api/providers/mistral", json={
        "credentials": {"api_key": "sk-new-key9999"},
        "password": TEST_PASSWORD,
    })
    assert resp.status_code == 200
    assert resp.json()["credential_hint"] == "****9999"


@pytest.mark.asyncio
async def test_update_credentials_requires_password(client):
    await client.post("/api/providers", json={
        "provider": "mistral",
        "credentials": {"api_key": "sk-old-key1111"},
        "password": TEST_PASSWORD,
    })
    resp = await client.put("/api/providers/mistral", json={
        "credentials": {"api_key": "sk-new-key9999"},
        # no password!
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_update_extra_fields_no_password(client):
    await client.post("/api/providers", json={
        "provider": "nvidia",
        "credentials": {"api_key": "sk-nv-test1234"},
        "password": TEST_PASSWORD,
    })
    resp = await client.put("/api/providers/nvidia", json={
        "extra_fields": {"api_base": "https://custom.nvidia.com/v1/"},
    })
    assert resp.status_code == 200
    assert resp.json()["extra_fields"]["api_base"] == "https://custom.nvidia.com/v1/"


@pytest.mark.asyncio
async def test_delete_provider(client):
    await client.post("/api/providers", json={
        "provider": "openrouter",
        "credentials": {"api_key": "sk-or-test5678"},
        "password": TEST_PASSWORD,
    })
    resp = await client.delete("/api/providers/openrouter")
    assert resp.status_code == 204
    # Verify it's gone
    resp = await client.get("/api/providers")
    assert len(resp.json()) == 0


@pytest.mark.asyncio
async def test_delete_nonexistent(client):
    resp = await client.delete("/api/providers/anthropic")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_set_default(client):
    await client.post("/api/providers", json={
        "provider": "anthropic",
        "credentials": {"api_key": "sk-ant-test1234"},
        "password": TEST_PASSWORD,
    })
    resp = await client.put("/api/providers/default", json={"provider": "anthropic"})
    assert resp.status_code == 200
    assert resp.json()["is_default"] is True


@pytest.mark.asyncio
async def test_list_returns_hint_not_key(client):
    await client.post("/api/providers", json={
        "provider": "anthropic",
        "credentials": {"api_key": "sk-ant-secretkey9876"},
        "password": TEST_PASSWORD,
    })
    resp = await client.get("/api/providers")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["credential_hint"] == "****9876"
    assert "sk-ant" not in json.dumps(data)
    assert "secretkey" not in json.dumps(data)
