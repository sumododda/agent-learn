"""Tests for provider service (LiteLLM adapter)."""
import pytest
from unittest.mock import AsyncMock, patch
from app.provider_service import (
    PROVIDERS,
    get_provider_registry,
    _build_litellm_params,
    list_models,
)


class TestProviderRegistry:
    def test_has_six_providers(self):
        assert len(PROVIDERS) == 6
        assert set(PROVIDERS.keys()) == {"anthropic", "azure", "mistral", "nvidia", "vertex_ai", "openrouter"}

    def test_each_provider_has_required_keys(self):
        for name, defn in PROVIDERS.items():
            assert "name" in defn
            assert "model_prefix" in defn
            assert "fields" in defn
            assert "models" in defn

    def test_secret_fields_marked(self):
        for name, defn in PROVIDERS.items():
            secret_fields = [f for f in defn["fields"] if f.get("secret")]
            assert len(secret_fields) >= 1, f"{name} has no secret fields"

    def test_get_provider_registry_returns_providers(self):
        result = get_provider_registry()
        assert result is PROVIDERS


class TestBuildLitellmParams:
    def test_anthropic(self):
        params = _build_litellm_params("anthropic", "claude-sonnet-4-20250514", {"api_key": "sk-test"})
        assert params["model"] == "anthropic/claude-sonnet-4-20250514"
        assert params["api_key"] == "sk-test"

    def test_azure(self):
        params = _build_litellm_params(
            "azure", "gpt-4o", {"api_key": "az-key"},
            {"api_base": "https://my.azure.com/", "api_version": "2024-06-01"}
        )
        assert params["model"] == "azure/gpt-4o"
        assert params["api_key"] == "az-key"
        assert params["api_base"] == "https://my.azure.com/"
        assert params["api_version"] == "2024-06-01"

    def test_nvidia(self):
        params = _build_litellm_params("nvidia", "meta/llama-3.1-70b-instruct", {"api_key": "nv-key"}, {"api_base": "https://custom.nvidia.com/v1/"})
        assert params["model"] == "nvidia_nim/meta/llama-3.1-70b-instruct"
        assert params["api_base"] == "https://custom.nvidia.com/v1/"

    def test_vertex_ai(self):
        params = _build_litellm_params(
            "vertex_ai", "gemini-2.5-pro",
            {"vertex_credentials": '{"type":"service_account"}'},
            {"vertex_ai_project": "my-project", "vertex_ai_location": "us-central1"},
        )
        assert params["model"] == "vertex_ai/gemini-2.5-pro"
        assert params["vertex_credentials"] == '{"type":"service_account"}'
        assert params["vertex_ai_project"] == "my-project"
        assert params["vertex_ai_location"] == "us-central1"

    def test_openrouter(self):
        params = _build_litellm_params("openrouter", "anthropic/claude-3-opus", {"api_key": "or-key"})
        assert params["model"] == "openrouter/anthropic/claude-3-opus"
        assert params["api_key"] == "or-key"

    def test_mistral(self):
        params = _build_litellm_params("mistral", "mistral-large-latest", {"api_key": "ms-key"})
        assert params["model"] == "mistral/mistral-large-latest"
        assert params["api_key"] == "ms-key"

    def test_extra_fields_override_credentials_api_base(self):
        """extra_fields api_base takes precedence over credentials api_base."""
        params = _build_litellm_params(
            "nvidia", "model",
            {"api_key": "nv-key", "api_base": "https://from-creds.com/"},
            {"api_base": "https://from-extra.com/"},
        )
        assert params["api_base"] == "https://from-extra.com/"

    def test_credentials_api_base_fallback(self):
        """api_base from credentials used when extra_fields has no api_base."""
        params = _build_litellm_params(
            "nvidia", "model",
            {"api_key": "nv-key", "api_base": "https://from-creds.com/"},
            {},
        )
        assert params["api_base"] == "https://from-creds.com/"


class TestListModels:
    @pytest.mark.asyncio
    async def test_static_models(self):
        models = await list_models("anthropic")
        assert isinstance(models, list)
        assert len(models) > 0
        assert all("id" in m and "name" in m for m in models)

    @pytest.mark.asyncio
    async def test_static_models_mistral(self):
        models = await list_models("mistral")
        assert len(models) == 3
        assert models[0]["id"] == "mistral-large-latest"

    @pytest.mark.asyncio
    async def test_dynamic_no_credentials(self):
        models = await list_models("openrouter")
        assert models == []

    @pytest.mark.asyncio
    async def test_dynamic_no_credentials_azure(self):
        models = await list_models("azure")
        assert models == []

    @pytest.mark.asyncio
    async def test_static_models_nvidia(self):
        models = await list_models("nvidia")
        assert len(models) == 2

    @pytest.mark.asyncio
    async def test_static_models_vertex(self):
        models = await list_models("vertex_ai")
        assert len(models) == 2
        assert any(m["id"] == "gemini-2.5-pro" for m in models)
