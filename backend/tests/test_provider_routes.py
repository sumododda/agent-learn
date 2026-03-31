"""Tests for multi-provider LLM CRUD and default selection."""

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import get_current_user
from app.database import get_session
from app.key_cache import _clear_all
from app.limiter import limiter
from app.main import app
from app.models import Base, User

TEST_USER_UUID = uuid.UUID("00000000-0000-0000-0000-000000000001")
TEST_USER_ID = str(TEST_USER_UUID)


@pytest.fixture(autouse=True)
async def setup_db():
    _clear_all()
    engine = create_async_engine("sqlite+aiosqlite://")

    @sa_event.listens_for(engine.sync_engine, "connect")
    def _set_pragma(dbapi_conn, _connection_record):
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
    _clear_all()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


@pytest.mark.asyncio
async def test_registry_lists_all_supported_providers(client):
    response = await client.get("/api/providers/registry")
    assert response.status_code == 200
    data = response.json()["providers"]
    assert set(data.keys()) == {"openai", "anthropic", "openrouter"}
    assert data["openai"]["models"][0]["id"] == "gpt-5.4-mini"


@pytest.mark.asyncio
async def test_first_provider_becomes_default_and_second_does_not(client):
    first = await client.post(
        "/api/providers",
        json={"provider": "openai", "credentials": {"api_key": "sk-openai-1234"}},
    )
    second = await client.post(
        "/api/providers",
        json={"provider": "anthropic", "credentials": {"api_key": "sk-ant-9876"}},
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["is_default"] is True
    assert second.json()["is_default"] is False

    listed = await client.get("/api/providers")
    data = listed.json()
    assert [item["provider"] for item in data] == ["openai", "anthropic"]
    assert data[0]["is_default"] is True
    assert "api_key" not in json.dumps(data)


@pytest.mark.asyncio
async def test_duplicate_provider_is_rejected(client):
    await client.post(
        "/api/providers",
        json={"provider": "openrouter", "credentials": {"api_key": "sk-or-test1234"}},
    )

    response = await client.post(
        "/api/providers",
        json={"provider": "openrouter", "credentials": {"api_key": "sk-or-other5678"}},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_update_provider_credentials_and_model(client):
    await client.post(
        "/api/providers",
        json={"provider": "openrouter", "credentials": {"api_key": "sk-or-old1111"}},
    )

    response = await client.put(
        "/api/providers/openrouter",
        json={
            "credentials": {"api_key": "sk-or-new9999"},
            "extra_fields": {"model": "openrouter/auto"},
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["credential_hint"] == "****9999"
    assert data["extra_fields"]["model"] == "openrouter/auto"


@pytest.mark.asyncio
async def test_set_default_switches_provider(client):
    await client.post(
        "/api/providers",
        json={"provider": "openai", "credentials": {"api_key": "sk-openai-1234"}},
    )
    await client.post(
        "/api/providers",
        json={"provider": "anthropic", "credentials": {"api_key": "sk-ant-9876"}},
    )

    response = await client.put(
        "/api/providers/default",
        json={"provider": "anthropic"},
    )

    assert response.status_code == 200
    assert response.json()["provider"] == "anthropic"
    assert response.json()["is_default"] is True

    listed = await client.get("/api/providers")
    data = listed.json()
    assert data[0]["provider"] == "anthropic"
    assert data[0]["is_default"] is True
    assert data[1]["provider"] == "openai"
    assert data[1]["is_default"] is False


@pytest.mark.asyncio
async def test_delete_default_promotes_oldest_remaining_provider(client):
    await client.post(
        "/api/providers",
        json={"provider": "openai", "credentials": {"api_key": "sk-openai-1234"}},
    )
    await client.post(
        "/api/providers",
        json={"provider": "anthropic", "credentials": {"api_key": "sk-ant-9876"}},
    )
    await client.post(
        "/api/providers",
        json={"provider": "openrouter", "credentials": {"api_key": "sk-or-5555"}},
    )

    response = await client.delete("/api/providers/openai")
    assert response.status_code == 204

    listed = await client.get("/api/providers")
    data = listed.json()
    assert [item["provider"] for item in data] == ["anthropic", "openrouter"]
    assert data[0]["is_default"] is True


@pytest.mark.asyncio
async def test_provider_models_requires_saved_provider(client):
    response = await client.get("/api/providers/openai/models")
    assert response.status_code == 404
    assert response.json()["detail"] == "not_configured"


@pytest.mark.asyncio
async def test_openrouter_provider_models_can_use_public_catalog_without_saved_key(client):
    mocked_models = [
        {
            "id": "openrouter/auto",
            "name": "OpenRouter Auto",
            "context_length": 0,
            "pricing_prompt": "0",
            "pricing_completion": "0",
        }
    ]
    with patch("app.routers.provider_routes.provider_service.list_public_models", new=AsyncMock(return_value=mocked_models)) as list_public:
        response = await client.get("/api/providers/openrouter/models")

    assert response.status_code == 200
    assert response.json()[0]["id"] == "openrouter/auto"
    list_public.assert_awaited_once()


@pytest.mark.asyncio
async def test_provider_models_uses_saved_credentials(client):
    await client.post(
        "/api/providers",
        json={
            "provider": "openai",
            "credentials": {"api_key": "sk-openai-1234"},
            "extra_fields": {"model": "gpt-5.4-mini"},
        },
    )

    mocked_models = [
        {
            "id": "gpt-5.4-mini",
            "name": "GPT-5.4 Mini",
            "context_length": 0,
            "pricing_prompt": "0",
            "pricing_completion": "0",
        }
    ]
    with patch("app.routers.provider_routes.provider_service.list_models", new=AsyncMock(return_value=mocked_models)):
        response = await client.get("/api/providers/openai/models")

    assert response.status_code == 200
    assert response.json()[0]["id"] == "gpt-5.4-mini"


@pytest.mark.asyncio
async def test_test_endpoint_uses_non_billable_validation(client):
    mocked_models = [
        {
            "id": "openrouter/auto",
            "name": "OpenRouter Auto",
            "context_length": 0,
            "pricing_prompt": "0",
            "pricing_completion": "0",
        }
    ]
    with (
        patch("app.routers.provider_routes.provider_service.validate_credentials", new=AsyncMock(return_value=True)) as validate,
        patch("app.routers.provider_routes.provider_service.list_models", new=AsyncMock(return_value=mocked_models)) as list_models,
    ):
        response = await client.post(
            "/api/providers/openrouter/test",
            json={"credentials": {"api_key": "sk-or-test1234"}, "extra_fields": {}},
        )

    assert response.status_code == 200
    validate.assert_awaited_once_with("openrouter", {"api_key": "sk-or-test1234"}, {})
    list_models.assert_awaited_once_with("openrouter", {"api_key": "sk-or-test1234"}, {})
    assert response.json()["models"][0]["id"] == "openrouter/auto"
