"""LiteLLM adapter with provider registry and completion wrappers."""
import json
import logging
from collections.abc import AsyncGenerator

import httpx
import litellm

logger = logging.getLogger(__name__)

PROVIDERS = {
    "anthropic": {
        "name": "Anthropic",
        "model_prefix": "anthropic/",
        "fields": [
            {"key": "api_key", "label": "API Key", "type": "password", "required": True, "secret": True}
        ],
        "models": [
            {"id": "claude-opus-4-20250514", "name": "Claude Opus 4"},
            {"id": "claude-sonnet-4-20250514", "name": "Claude Sonnet 4"},
            {"id": "claude-haiku-4-5-20251001", "name": "Claude Haiku 4.5"},
        ],
    },
    "azure": {
        "name": "Azure OpenAI",
        "model_prefix": "azure/",
        "fields": [
            {"key": "api_key", "label": "API Key", "type": "password", "required": True, "secret": True},
            {"key": "api_base", "label": "Endpoint URL", "type": "text", "required": True, "secret": False, "placeholder": "https://your-resource.openai.azure.com/"},
            {"key": "api_version", "label": "API Version", "type": "text", "required": True, "secret": False, "placeholder": "2024-06-01"},
        ],
        "models": "dynamic",
    },
    "mistral": {
        "name": "Mistral",
        "model_prefix": "mistral/",
        "fields": [
            {"key": "api_key", "label": "API Key", "type": "password", "required": True, "secret": True}
        ],
        "models": [
            {"id": "mistral-large-latest", "name": "Mistral Large"},
            {"id": "mistral-medium-latest", "name": "Mistral Medium"},
            {"id": "mistral-small-latest", "name": "Mistral Small"},
        ],
    },
    "nvidia": {
        "name": "NVIDIA NIM",
        "model_prefix": "nvidia_nim/",
        "fields": [
            {"key": "api_key", "label": "API Key", "type": "password", "required": True, "secret": True},
            {"key": "api_base", "label": "Base URL", "type": "text", "required": False, "secret": False, "placeholder": "https://integrate.api.nvidia.com/v1/"},
        ],
        "models": [
            {"id": "meta/llama-3.1-405b-instruct", "name": "Llama 3.1 405B"},
            {"id": "meta/llama-3.1-70b-instruct", "name": "Llama 3.1 70B"},
        ],
    },
    "vertex_ai": {
        "name": "Vertex AI",
        "model_prefix": "vertex_ai/",
        "fields": [
            {"key": "vertex_credentials", "label": "Service Account JSON", "type": "textarea", "required": True, "secret": True},
            {"key": "vertex_ai_project", "label": "Project ID", "type": "text", "required": True, "secret": False},
            {"key": "vertex_ai_location", "label": "Region", "type": "text", "required": True, "secret": False, "placeholder": "us-central1"},
        ],
        "models": [
            {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro"},
            {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash"},
        ],
    },
    "openrouter": {
        "name": "OpenRouter",
        "model_prefix": "openrouter/",
        "fields": [
            {"key": "api_key", "label": "API Key", "type": "password", "required": True, "secret": True}
        ],
        "models": "dynamic",
    },
}


def get_provider_registry() -> dict:
    """Return provider definitions for frontend form rendering."""
    return PROVIDERS


def _build_litellm_params(provider: str, model: str, credentials: dict, extra_fields: dict | None = None) -> dict:
    """Map provider config to litellm.acompletion() kwargs."""
    prefix = PROVIDERS[provider]["model_prefix"]
    params = {"model": f"{prefix}{model}"}

    # Secret fields from credentials
    if "api_key" in credentials:
        params["api_key"] = credentials["api_key"]
    if "vertex_credentials" in credentials:
        params["vertex_credentials"] = credentials["vertex_credentials"]

    # Non-secret fields from extra_fields
    ef = extra_fields or {}
    if "api_base" in ef:
        params["api_base"] = ef["api_base"]
    elif "api_base" in credentials:
        params["api_base"] = credentials["api_base"]
    if "api_version" in ef:
        params["api_version"] = ef["api_version"]
    elif "api_version" in credentials:
        params["api_version"] = credentials["api_version"]
    if "vertex_ai_project" in ef:
        params["vertex_ai_project"] = ef["vertex_ai_project"]
    if "vertex_ai_location" in ef:
        params["vertex_ai_location"] = ef["vertex_ai_location"]

    return params


async def validate_credentials(provider: str, credentials: dict, extra_fields: dict | None = None) -> bool:
    """Test credentials with a lightweight completion call."""
    params = _build_litellm_params(provider, _get_test_model(provider), credentials, extra_fields)
    try:
        await litellm.acompletion(
            messages=[{"role": "user", "content": "Hi"}],
            max_tokens=5,
            **params,
        )
        return True
    except Exception as e:
        logger.warning("Credential validation failed for %s: %s", provider, e)
        return False


def _get_test_model(provider: str) -> str:
    """Get a cheap model for testing credentials."""
    models = PROVIDERS[provider]["models"]
    if isinstance(models, list) and models:
        return models[0]["id"]
    # Dynamic providers
    if provider == "openrouter":
        return "openai/gpt-4o-mini"
    if provider == "azure":
        return "gpt-4o-mini"  # User must have this deployment
    return "test"


async def completion(provider: str, model: str, messages: list[dict], credentials: dict, extra_fields: dict | None = None, **kwargs) -> "litellm.ModelResponse":
    """Wrap litellm.acompletion() with provider credential mapping."""
    params = _build_litellm_params(provider, model, credentials, extra_fields)
    params.update(kwargs)
    return await litellm.acompletion(messages=messages, **params)


async def stream_completion(provider: str, model: str, messages: list[dict], credentials: dict, extra_fields: dict | None = None, **kwargs) -> AsyncGenerator:
    """Wrap litellm.acompletion(stream=True) with provider credential mapping."""
    params = _build_litellm_params(provider, model, credentials, extra_fields)
    params.update(kwargs)
    return await litellm.acompletion(messages=messages, stream=True, **params)


async def list_models(provider: str, credentials: dict | None = None, extra_fields: dict | None = None) -> list[dict]:
    """Return available models for a provider (static list or dynamic fetch)."""
    models = PROVIDERS[provider]["models"]
    if isinstance(models, list):
        return models

    # Dynamic: OpenRouter
    if provider == "openrouter" and credentials and credentials.get("api_key"):
        return await _fetch_openrouter_models(credentials["api_key"])

    # Dynamic: Azure (placeholder)
    return []


async def _fetch_openrouter_models(api_key: str) -> list[dict]:
    """Fetch models from OpenRouter API."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()

    models = []
    for m in data.get("data", []):
        arch = m.get("architecture") or {}
        if "text" in (arch.get("input_modalities") or []) and "text" in (arch.get("output_modalities") or []):
            models.append({"id": m["id"], "name": m.get("name", m["id"])})
    return models
