"""Provider registry and direct HTTP clients for supported LLM providers."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

OPENAI_BASE = "https://api.openai.com/v1"
ANTHROPIC_BASE = "https://api.anthropic.com/v1"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
ANTHROPIC_VERSION = "2023-06-01"

DEFAULT_PROVIDER = "openai"
DEFAULT_MODEL = "gpt-5.4-mini"
REQUEST_TIMEOUT = 360.0


@dataclass(frozen=True)
class ProviderField:
    key: str
    label: str
    type: str
    required: bool
    secret: bool
    placeholder: str | None = None


@dataclass(frozen=True)
class RecommendedModel:
    id: str
    name: str


@dataclass(frozen=True)
class ProviderDefinition:
    name: str
    fields: tuple[ProviderField, ...]
    models: tuple[RecommendedModel, ...]


PROVIDER_REGISTRY: dict[str, ProviderDefinition] = {
    "openai": ProviderDefinition(
        name="OpenAI",
        fields=(
            ProviderField(
                key="api_key",
                label="API Key",
                type="password",
                required=True,
                secret=True,
                placeholder="sk-proj-...",
            ),
        ),
        models=(
            RecommendedModel(id="gpt-5.4-mini", name="GPT-5.4 Mini"),
        ),
    ),
    "anthropic": ProviderDefinition(
        name="Anthropic",
        fields=(
            ProviderField(
                key="api_key",
                label="API Key",
                type="password",
                required=True,
                secret=True,
                placeholder="sk-ant-...",
            ),
        ),
        models=(
            RecommendedModel(
                id="claude-sonnet-4-6",
                name="Claude Sonnet 4.6",
            ),
        ),
    ),
    "openrouter": ProviderDefinition(
        name="OpenRouter",
        fields=(
            ProviderField(
                key="api_key",
                label="API Key",
                type="password",
                required=True,
                secret=True,
                placeholder="sk-or-v1-...",
            ),
        ),
        models=(
            RecommendedModel(
                id="google/gemini-3.1-pro-preview",
                name="Gemini 3.1 Pro Preview",
            ),
            RecommendedModel(id="openrouter/auto", name="OpenRouter Auto"),
            RecommendedModel(
                id="anthropic/claude-sonnet-4",
                name="Claude Sonnet 4",
            ),
        ),
    ),
}


def get_provider_registry() -> dict[str, dict[str, Any]]:
    """Return JSON-serializable provider metadata for the frontend."""
    return {
        "providers": {
            key: {
                "name": definition.name,
                "fields": [
                    {
                        "key": field.key,
                        "label": field.label,
                        "type": field.type,
                        "required": field.required,
                        "secret": field.secret,
                        "placeholder": field.placeholder,
                    }
                    for field in definition.fields
                ],
                "models": [
                    {"id": model.id, "name": model.name}
                    for model in definition.models
                ],
            }
            for key, definition in PROVIDER_REGISTRY.items()
        }
    }


def get_provider_name(provider: str) -> str:
    definition = PROVIDER_REGISTRY.get(provider)
    return definition.name if definition else provider.title()


def is_supported_provider(provider: str) -> bool:
    return provider in PROVIDER_REGISTRY


def get_default_model(provider: str) -> str:
    definition = PROVIDER_REGISTRY.get(provider)
    if definition and definition.models:
        return definition.models[0].id
    return DEFAULT_MODEL


class ProviderError(Exception):
    """Base exception for provider-specific failures."""


class UnsupportedProviderError(ProviderError):
    """Raised when a provider name is not supported."""


class ProviderAuthError(ProviderError):
    """Raised when credentials are invalid."""


class ProviderNotConfiguredError(ProviderError):
    """Raised when a provider is not configured for a user."""


class ProviderResponseError(ProviderError):
    """Raised when the upstream provider returns an invalid payload."""


@dataclass
class NormalizedStreamEvent:
    type: str
    text: str = ""
    tool_name: str | None = None
    tool_arguments_delta: str | None = None
    error: str | None = None


class CompatAIMessage:
    """Minimal AI message compatible with the existing call sites."""

    def __init__(self, content: str):
        self.content = content


class CompatChatModel:
    """Thin async interface matching the old LangChain usage."""

    def __init__(
        self,
        provider: str,
        model: str,
        credentials: dict[str, Any],
        extra_fields: dict[str, Any] | None = None,
    ) -> None:
        self.provider = provider
        self.model_name = model
        self.credentials = credentials
        self.extra_fields = extra_fields or {}
        self.openai_api_base = OPENROUTER_BASE if provider == "openrouter" else None

    async def ainvoke(self, payload: Any) -> CompatAIMessage:
        messages = _coerce_messages(payload)
        content = await complete_text(
            self.provider,
            self.model_name,
            messages,
            self.credentials,
            self.extra_fields,
        )
        return CompatAIMessage(content)

    def invoke(self, payload: Any) -> CompatAIMessage:
        return asyncio.run(self.ainvoke(payload))


class CompatStructuredAgent:
    """Local replacement for the small LangChain agent surface we use."""

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        credentials: dict[str, Any],
        extra_fields: dict[str, Any] | None,
        system_prompt: str,
        response_schema: type[BaseModel] | None,
    ) -> None:
        self.provider = provider
        self.model = model
        self.credentials = credentials
        self.extra_fields = extra_fields or {}
        self.system_prompt = system_prompt
        self.response_schema = response_schema

    async def ainvoke(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        raw_messages = payload.get("messages", [])
        messages = _coerce_messages(raw_messages, default_role="user")
        messages = [{"role": "system", "content": self.system_prompt}, *messages]

        if self.response_schema is not None:
            structured = await complete_structured(
                self.provider,
                self.model,
                messages,
                self.response_schema,
                self.credentials,
                self.extra_fields,
            )
            return {"structured_response": structured, "messages": []}

        content = await complete_text(
            self.provider,
            self.model,
            messages,
            self.credentials,
            self.extra_fields,
        )
        return {"structured_response": None, "messages": [CompatAIMessage(content)]}

    def invoke(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return asyncio.run(self.ainvoke(payload))


def build_chat_model(
    provider: str,
    model: str,
    credentials: dict[str, Any],
    extra_fields: dict[str, Any] | None = None,
) -> CompatChatModel:
    """Return a lightweight compatibility wrapper for plain-text calls."""
    _require_supported_provider(provider)
    return CompatChatModel(provider, model or get_default_model(provider), credentials, extra_fields)


def build_structured_agent(
    *,
    provider: str,
    model: str,
    credentials: dict[str, Any],
    extra_fields: dict[str, Any] | None,
    system_prompt: str,
    response_schema: type[BaseModel],
) -> CompatStructuredAgent:
    """Return a compatibility agent that mimics the LangChain call shape."""
    _require_supported_provider(provider)
    return CompatStructuredAgent(
        provider=provider,
        model=model or get_default_model(provider),
        credentials=credentials,
        extra_fields=extra_fields,
        system_prompt=system_prompt,
        response_schema=response_schema,
    )


async def validate_credentials(
    provider: str,
    credentials: dict[str, Any],
    extra_fields: dict[str, Any] | None = None,
) -> bool:
    """Validate credentials using non-billable model-list endpoints."""
    _require_supported_provider(provider)
    api_key = _require_api_key(credentials)
    try:
        async with _client() as client:
            if provider == "openai":
                await _openai_list_models(client, api_key)
            elif provider == "anthropic":
                await _anthropic_list_models(client, api_key)
            elif provider == "openrouter":
                await _openrouter_list_models_user(client, api_key)
            else:
                raise UnsupportedProviderError(provider)
        return True
    except ProviderError:
        return False


async def list_models(
    provider: str,
    credentials: dict[str, Any],
    extra_fields: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return filtered, chat-capable models for a provider."""
    _require_supported_provider(provider)
    api_key = _require_api_key(credentials)
    extra_fields = extra_fields or {}

    async with _client() as client:
        if provider == "openai":
            models = await _openai_list_models(client, api_key)
        elif provider == "anthropic":
            models = await _anthropic_list_models(client, api_key)
        elif provider == "openrouter":
            models = await _openrouter_list_models(client, api_key)
        else:
            raise UnsupportedProviderError(provider)

    chosen_model = str(extra_fields.get("model") or get_default_model(provider))
    return _ensure_selected_model_present(models, chosen_model)


async def list_public_models(
    provider: str,
    extra_fields: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return a public model catalog when the provider supports it."""
    _require_supported_provider(provider)
    if provider != "openrouter":
        raise ProviderNotConfiguredError(f"{provider} requires credentials for model listing")

    async with _client() as client:
        payload = await _request_json(
            client,
            "GET",
            f"{OPENROUTER_BASE}/models",
            headers={"Content-Type": "application/json"},
        )
        models = _parse_openrouter_models(payload)

    chosen_model = str((extra_fields or {}).get("model") or get_default_model(provider))
    return _ensure_selected_model_present(models, chosen_model)


async def complete_text(
    provider: str,
    model: str,
    messages: Sequence[dict[str, str]],
    credentials: dict[str, Any],
    extra_fields: dict[str, Any] | None = None,
) -> str:
    """Generate plain text from a provider-specific API."""
    _require_supported_provider(provider)
    api_key = _require_api_key(credentials)
    resolved_model = model or str((extra_fields or {}).get("model") or get_default_model(provider))

    async with _client() as client:
        if provider == "openai":
            response = await _openai_create_response(
                client,
                api_key,
                resolved_model,
                messages,
            )
            return _openai_extract_text(response)
        if provider == "anthropic":
            response = await _anthropic_create_message(
                client,
                api_key,
                resolved_model,
                messages,
            )
            return _anthropic_extract_text(response)
        if provider == "openrouter":
            response = await _openrouter_create_chat_completion(
                client,
                api_key,
                resolved_model,
                messages,
            )
            return _openrouter_extract_text(response)
    raise UnsupportedProviderError(provider)


async def complete_structured(
    provider: str,
    model: str,
    messages: Sequence[dict[str, str]],
    response_schema: type[BaseModel],
    credentials: dict[str, Any],
    extra_fields: dict[str, Any] | None = None,
) -> BaseModel:
    """Generate structured output and validate it against a Pydantic schema."""
    _require_supported_provider(provider)
    api_key = _require_api_key(credentials)
    resolved_model = model or str((extra_fields or {}).get("model") or get_default_model(provider))

    async with _client() as client:
        if provider == "openai":
            response = await _openai_create_structured_response(
                client,
                api_key,
                resolved_model,
                messages,
                response_schema,
            )
            return _openai_extract_structured(response, response_schema)
        if provider == "anthropic":
            response = await _anthropic_create_structured_message(
                client,
                api_key,
                resolved_model,
                messages,
                response_schema,
            )
            return _anthropic_extract_structured(response, response_schema)
        if provider == "openrouter":
            response = await _openrouter_create_structured_completion(
                client,
                api_key,
                resolved_model,
                messages,
                response_schema,
            )
            return _openrouter_extract_structured(response, response_schema)
    raise UnsupportedProviderError(provider)


async def stream_text(
    provider: str,
    model: str,
    messages: Sequence[dict[str, str]],
    credentials: dict[str, Any],
    extra_fields: dict[str, Any] | None = None,
) -> AsyncGenerator[NormalizedStreamEvent, None]:
    """Yield normalized stream events for chat/text generation."""
    _require_supported_provider(provider)
    api_key = _require_api_key(credentials)
    resolved_model = model or str((extra_fields or {}).get("model") or get_default_model(provider))

    async with _client() as client:
        if provider == "openai":
            async for event in _stream_openai(client, api_key, resolved_model, messages):
                yield event
            return
        if provider == "anthropic":
            async for event in _stream_anthropic(client, api_key, resolved_model, messages):
                yield event
            return
        if provider == "openrouter":
            async for event in _stream_openrouter(client, api_key, resolved_model, messages):
                yield event
            return
    raise UnsupportedProviderError(provider)


def _require_supported_provider(provider: str) -> None:
    if provider not in PROVIDER_REGISTRY:
        raise UnsupportedProviderError(f"Unsupported provider: {provider}")


def _require_api_key(credentials: Mapping[str, Any]) -> str:
    api_key = str(credentials.get("api_key", "")).strip()
    if not api_key:
        raise ProviderAuthError("API key required")
    return api_key


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=REQUEST_TIMEOUT)


def _coerce_messages(payload: Any, default_role: str = "user") -> list[dict[str, str]]:
    if isinstance(payload, str):
        return [{"role": default_role, "content": payload}]
    if isinstance(payload, Mapping):
        if "messages" in payload:
            return _coerce_messages(payload["messages"], default_role=default_role)
        role = str(payload.get("role") or default_role)
        content = _extract_message_content(payload)
        return [{"role": _normalize_role(role), "content": content}]
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        messages: list[dict[str, str]] = []
        for item in payload:
            if isinstance(item, str):
                messages.append({"role": default_role, "content": item})
                continue
            role = _extract_role(item) or default_role
            content = _extract_message_content(item)
            messages.append({"role": _normalize_role(role), "content": content})
        return messages
    return [{"role": default_role, "content": str(payload)}]


def _extract_role(item: Any) -> str | None:
    if isinstance(item, Mapping):
        role = item.get("role")
        return str(role) if role is not None else None
    role = getattr(item, "role", None)
    if role:
        return str(role)
    msg_type = getattr(item, "type", None)
    if msg_type == "human":
        return "user"
    if msg_type == "ai":
        return "assistant"
    if msg_type == "system":
        return "system"
    return None


def _extract_message_content(item: Any) -> str:
    if isinstance(item, Mapping):
        content = item.get("content", "")
    else:
        content = getattr(item, "content", item)

    if isinstance(content, str):
        return content
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes, bytearray)):
        parts: list[str] = []
        for part in content:
            if isinstance(part, Mapping):
                text = part.get("text")
                if text is not None:
                    parts.append(str(text))
            else:
                parts.append(str(part))
        return "\n".join(p for p in parts if p)
    return str(content)


def _normalize_role(role: str) -> str:
    lowered = role.lower()
    if lowered == "human":
        return "user"
    if lowered == "ai":
        return "assistant"
    if lowered in {"system", "user", "assistant", "tool"}:
        return lowered
    return "user"


def _ensure_selected_model_present(
    models: list[dict[str, Any]],
    selected_model: str,
) -> list[dict[str, Any]]:
    if not selected_model:
        return models
    existing_index = next(
        (index for index, model in enumerate(models) if model["id"] == selected_model),
        None,
    )
    if existing_index is None:
        return [
            {
                "id": selected_model,
                "name": selected_model,
                "context_length": 0,
                "pricing_prompt": "0",
                "pricing_completion": "0",
            },
            *models,
        ]
    if existing_index == 0:
        return models
    chosen = models[existing_index]
    return [chosen, *models[:existing_index], *models[existing_index + 1 :]]


def _json_schema_for_model(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    schema.setdefault("additionalProperties", False)
    return schema


def _schema_name(model: type[BaseModel]) -> str:
    return model.__name__.lower()


def _openai_auth_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _anthropic_headers(api_key: str) -> dict[str, str]:
    return {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }


def _openrouter_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


async def _request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = await client.request(method, url, headers=headers, json=json_body)
    _raise_for_provider_error(response)
    try:
        return response.json()
    except ValueError as exc:
        raise ProviderResponseError("Invalid JSON response from provider") from exc


def _raise_for_provider_error(response: httpx.Response) -> None:
    if response.is_success:
        return
    message = _extract_error_message(response)
    if response.status_code in {401, 403}:
        raise ProviderAuthError(message)
    raise ProviderResponseError(message)


def _extract_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        text = response.text.strip()
        return text or f"Provider request failed with status {response.status_code}"

    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("type")
            if message:
                return str(message)
        detail = payload.get("detail")
        if detail:
            return str(detail)
        message = payload.get("message")
        if message:
            return str(message)

    return f"Provider request failed with status {response.status_code}"


def _openai_text_model_allowed(model_id: str) -> bool:
    lowered = model_id.lower()
    if any(
        token in lowered
        for token in (
            "embedding",
            "moderation",
            "transcribe",
            "tts",
            "image",
            "audio",
            "realtime",
            "search-preview",
            "vision-preview",
        )
    ):
        return False
    return lowered.startswith(("gpt-", "o1", "o3", "o4"))


def _openrouter_text_model_allowed(model_data: Mapping[str, Any]) -> bool:
    architecture = model_data.get("architecture")
    if not isinstance(architecture, Mapping):
        return True
    input_modalities = architecture.get("input_modalities")
    output_modalities = architecture.get("output_modalities")
    if isinstance(input_modalities, Sequence) and "text" not in input_modalities:
        return False
    if isinstance(output_modalities, Sequence) and "text" not in output_modalities:
        return False
    return True


def _sort_models(models: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(models, key=lambda item: str(item["name"]).lower())


async def _openai_list_models(
    client: httpx.AsyncClient,
    api_key: str,
) -> list[dict[str, Any]]:
    payload = await _request_json(
        client,
        "GET",
        f"{OPENAI_BASE}/models",
        headers=_openai_auth_headers(api_key),
    )
    data = payload.get("data", [])
    models: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, Mapping):
            continue
        model_id = str(item.get("id", ""))
        if not model_id or not _openai_text_model_allowed(model_id):
            continue
        models.append(
            {
                "id": model_id,
                "name": model_id,
                "context_length": 0,
                "pricing_prompt": "0",
                "pricing_completion": "0",
            }
        )
    return _sort_models(models)


async def _anthropic_list_models(
    client: httpx.AsyncClient,
    api_key: str,
) -> list[dict[str, Any]]:
    payload = await _request_json(
        client,
        "GET",
        f"{ANTHROPIC_BASE}/models",
        headers=_anthropic_headers(api_key),
    )
    data = payload.get("data", [])
    models: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, Mapping):
            continue
        model_id = str(item.get("id", ""))
        if not model_id:
            continue
        name = str(item.get("display_name") or model_id)
        models.append(
            {
                "id": model_id,
                "name": name,
                "context_length": 0,
                "pricing_prompt": "0",
                "pricing_completion": "0",
            }
        )
    return _sort_models(models)


async def _openrouter_list_models_user(
    client: httpx.AsyncClient,
    api_key: str,
) -> list[dict[str, Any]]:
    payload = await _request_json(
        client,
        "GET",
        f"{OPENROUTER_BASE}/models/user",
        headers=_openrouter_headers(api_key),
    )
    return _parse_openrouter_models(payload)


async def _openrouter_list_models(
    client: httpx.AsyncClient,
    api_key: str,
) -> list[dict[str, Any]]:
    try:
        return await _openrouter_list_models_user(client, api_key)
    except ProviderResponseError:
        payload = await _request_json(
            client,
            "GET",
            f"{OPENROUTER_BASE}/models",
            headers=_openrouter_headers(api_key),
        )
        return _parse_openrouter_models(payload)


def _parse_openrouter_models(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data", [])
    models: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, Mapping):
            continue
        if not _openrouter_text_model_allowed(item):
            continue
        model_id = str(item.get("id", ""))
        if not model_id:
            continue
        pricing = item.get("pricing")
        pricing_prompt = "0"
        pricing_completion = "0"
        if isinstance(pricing, Mapping):
            pricing_prompt = str(pricing.get("prompt", "0"))
            pricing_completion = str(pricing.get("completion", "0"))
        context_length = int(item.get("context_length") or 0)
        models.append(
            {
                "id": model_id,
                "name": str(item.get("name") or model_id),
                "context_length": context_length,
                "pricing_prompt": pricing_prompt,
                "pricing_completion": pricing_completion,
            }
        )
    return _sort_models(models)


def _openai_input(messages: Sequence[dict[str, str]]) -> list[dict[str, Any]]:
    return [
        {
            "role": _normalize_role(message["role"]),
            "content": message["content"],
        }
        for message in messages
    ]


async def _openai_create_response(
    client: httpx.AsyncClient,
    api_key: str,
    model: str,
    messages: Sequence[dict[str, str]],
) -> dict[str, Any]:
    return await _request_json(
        client,
        "POST",
        f"{OPENAI_BASE}/responses",
        headers=_openai_auth_headers(api_key),
        json_body={
            "model": model,
            "input": _openai_input(messages),
        },
    )


async def _openai_create_structured_response(
    client: httpx.AsyncClient,
    api_key: str,
    model: str,
    messages: Sequence[dict[str, str]],
    response_schema: type[BaseModel],
) -> dict[str, Any]:
    return await _request_json(
        client,
        "POST",
        f"{OPENAI_BASE}/responses",
        headers=_openai_auth_headers(api_key),
        json_body={
            "model": model,
            "input": _openai_input(messages),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": _schema_name(response_schema),
                    "strict": True,
                    "schema": _json_schema_for_model(response_schema),
                }
            },
        },
    )


def _openai_extract_text(payload: Mapping[str, Any]) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str):
        return output_text

    output = payload.get("output", [])
    if not isinstance(output, Sequence):
        raise ProviderResponseError("OpenAI response missing output")

    parts: list[str] = []
    for item in output:
        if not isinstance(item, Mapping):
            continue
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if not isinstance(content, Mapping):
                continue
            if content.get("type") in {"output_text", "text"}:
                text = content.get("text")
                if text:
                    parts.append(str(text))
    if parts:
        return "".join(parts)
    raise ProviderResponseError("OpenAI response did not contain text output")


def _openai_extract_structured(
    payload: Mapping[str, Any],
    response_schema: type[BaseModel],
) -> BaseModel:
    text = _openai_extract_text(payload)
    try:
        return response_schema.model_validate_json(text)
    except ValidationError as exc:
        raise ProviderResponseError(f"OpenAI structured output validation failed: {exc}") from exc


def _anthropic_system_and_messages(
    messages: Sequence[dict[str, str]],
) -> tuple[str | None, list[dict[str, str]]]:
    system_parts = [message["content"] for message in messages if _normalize_role(message["role"]) == "system"]
    api_messages = [
        {"role": _normalize_role(message["role"]), "content": message["content"]}
        for message in messages
        if _normalize_role(message["role"]) != "system"
    ]
    return ("\n\n".join(system_parts) if system_parts else None, api_messages)


async def _anthropic_create_message(
    client: httpx.AsyncClient,
    api_key: str,
    model: str,
    messages: Sequence[dict[str, str]],
) -> dict[str, Any]:
    system_prompt, api_messages = _anthropic_system_and_messages(messages)
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": 4000,
        "messages": api_messages,
    }
    if system_prompt:
        body["system"] = system_prompt
    return await _request_json(
        client,
        "POST",
        f"{ANTHROPIC_BASE}/messages",
        headers=_anthropic_headers(api_key),
        json_body=body,
    )


async def _anthropic_create_structured_message(
    client: httpx.AsyncClient,
    api_key: str,
    model: str,
    messages: Sequence[dict[str, str]],
    response_schema: type[BaseModel],
) -> dict[str, Any]:
    system_prompt, api_messages = _anthropic_system_and_messages(messages)
    tool_name = "emit_result"
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": 4000,
        "messages": api_messages,
        "tools": [
            {
                "name": tool_name,
                "description": f"Return the response as {response_schema.__name__}.",
                "input_schema": _json_schema_for_model(response_schema),
            }
        ],
        "tool_choice": {"type": "tool", "name": tool_name},
    }
    if system_prompt:
        body["system"] = system_prompt
    return await _request_json(
        client,
        "POST",
        f"{ANTHROPIC_BASE}/messages",
        headers=_anthropic_headers(api_key),
        json_body=body,
    )


def _anthropic_extract_text(payload: Mapping[str, Any]) -> str:
    content = payload.get("content", [])
    if not isinstance(content, Sequence):
        raise ProviderResponseError("Anthropic response missing content")
    text_parts = []
    for block in content:
        if isinstance(block, Mapping) and block.get("type") == "text":
            text = block.get("text")
            if text:
                text_parts.append(str(text))
    if text_parts:
        return "".join(text_parts)
    raise ProviderResponseError("Anthropic response did not contain text output")


def _anthropic_extract_structured(
    payload: Mapping[str, Any],
    response_schema: type[BaseModel],
) -> BaseModel:
    content = payload.get("content", [])
    if not isinstance(content, Sequence):
        raise ProviderResponseError("Anthropic structured response missing content")
    for block in content:
        if not isinstance(block, Mapping):
            continue
        if block.get("type") != "tool_use":
            continue
        tool_input = block.get("input")
        if tool_input is None:
            continue
        try:
            return response_schema.model_validate(tool_input)
        except ValidationError as exc:
            raise ProviderResponseError(
                f"Anthropic structured output validation failed: {exc}"
            ) from exc
    raise ProviderResponseError("Anthropic response did not contain a tool result")


async def _openrouter_create_chat_completion(
    client: httpx.AsyncClient,
    api_key: str,
    model: str,
    messages: Sequence[dict[str, str]],
) -> dict[str, Any]:
    return await _request_json(
        client,
        "POST",
        f"{OPENROUTER_BASE}/chat/completions",
        headers=_openrouter_headers(api_key),
        json_body={
            "model": model,
            "messages": list(messages),
        },
    )


async def _openrouter_create_structured_completion(
    client: httpx.AsyncClient,
    api_key: str,
    model: str,
    messages: Sequence[dict[str, str]],
    response_schema: type[BaseModel],
) -> dict[str, Any]:
    tool_name = "emit_result"
    return await _request_json(
        client,
        "POST",
        f"{OPENROUTER_BASE}/chat/completions",
        headers=_openrouter_headers(api_key),
        json_body={
            "model": model,
            "messages": list(messages),
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": f"Return the response as {response_schema.__name__}.",
                        "parameters": _json_schema_for_model(response_schema),
                    },
                }
            ],
            "tool_choice": {
                "type": "function",
                "function": {"name": tool_name},
            },
        },
    )


def _openrouter_extract_text(payload: Mapping[str, Any]) -> str:
    choices = payload.get("choices", [])
    if not isinstance(choices, Sequence) or not choices:
        raise ProviderResponseError("OpenRouter response missing choices")
    message = choices[0].get("message", {}) if isinstance(choices[0], Mapping) else {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence):
        return "".join(str(part) for part in content)
    raise ProviderResponseError("OpenRouter response did not contain text output")


def _strip_code_fences(text: str) -> str:
    """Strip markdown code fences if the model wrapped its JSON output in them."""
    if text.startswith("```"):
        lines = text.split("\n", 1)
        text = lines[1] if len(lines) > 1 else ""
    if text.endswith("```"):
        text = text[: -3]
    return text.strip()


def _repair_stringified_objects(data: Any) -> Any:
    """Some models via OpenRouter return nested objects/arrays as JSON strings.
    Recursively detect and parse them back into dicts/lists."""
    if isinstance(data, str):
        try:
            parsed = json.loads(data)
            if isinstance(parsed, (dict, list)):
                return _repair_stringified_objects(parsed)
        except (json.JSONDecodeError, ValueError):
            pass
        return data
    if isinstance(data, dict):
        return {k: _repair_stringified_objects(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_repair_stringified_objects(item) for item in data]
    return data


def _openrouter_extract_structured(
    payload: Mapping[str, Any],
    response_schema: type[BaseModel],
) -> BaseModel:
    choices = payload.get("choices", [])
    if not isinstance(choices, Sequence) or not choices:
        raise ProviderResponseError("OpenRouter structured response missing choices")
    message = choices[0].get("message", {}) if isinstance(choices[0], Mapping) else {}
    tool_calls = message.get("tool_calls", [])

    # Primary path: extract from tool call arguments
    raw_json: str | None = None
    if isinstance(tool_calls, Sequence) and tool_calls:
        function = tool_calls[0].get("function", {}) if isinstance(tool_calls[0], Mapping) else {}
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            raw_json = arguments

    # Fallback: some models ignore tool_choice and return JSON in content
    if raw_json is None:
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            raw_json = _strip_code_fences(content.strip())

    if raw_json is None:
        raise ProviderResponseError("OpenRouter response contained neither tool call nor parseable content")

    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ProviderResponseError(f"OpenRouter structured output invalid JSON: {raw_json[:200]}") from exc
    parsed = _repair_stringified_objects(parsed)
    try:
        return response_schema.model_validate(parsed)
    except ValidationError as exc:
        logger.warning(
            "OpenRouter structured output validation failed for %s: %s\nParsed data keys: %s",
            response_schema.__name__,
            exc,
            list(parsed.keys()) if isinstance(parsed, dict) else type(parsed).__name__,
        )
        raise ProviderResponseError(
            f"OpenRouter structured output validation failed: {exc}"
        ) from exc


async def _stream_openai(
    client: httpx.AsyncClient,
    api_key: str,
    model: str,
    messages: Sequence[dict[str, str]],
) -> AsyncGenerator[NormalizedStreamEvent, None]:
    async with client.stream(
        "POST",
        f"{OPENAI_BASE}/responses",
        headers=_openai_auth_headers(api_key),
        json={
            "model": model,
            "input": _openai_input(messages),
            "stream": True,
        },
    ) as response:
        _raise_for_provider_error(response)
        async for event_name, data in _iter_sse_events(response):
            if not data:
                continue
            if data == "[DONE]":
                yield NormalizedStreamEvent(type="done")
                continue
            payload = _safe_json_loads(data)
            if event_name == "response.output_text.delta":
                yield NormalizedStreamEvent(type="text_delta", text=str(payload.get("delta", "")))
            elif event_name == "response.function_call_arguments.delta":
                yield NormalizedStreamEvent(
                    type="tool_delta",
                    tool_arguments_delta=str(payload.get("delta", "")),
                )
            elif event_name in {"response.completed", "response.failed"}:
                if event_name == "response.failed":
                    message = str(payload.get("error", {}).get("message") or "OpenAI streaming failed")
                    yield NormalizedStreamEvent(type="error", error=message)
                yield NormalizedStreamEvent(type="done")
            elif event_name == "error":
                message = str(payload.get("error", {}).get("message") or payload.get("message") or "OpenAI streaming failed")
                yield NormalizedStreamEvent(type="error", error=message)


async def _stream_anthropic(
    client: httpx.AsyncClient,
    api_key: str,
    model: str,
    messages: Sequence[dict[str, str]],
) -> AsyncGenerator[NormalizedStreamEvent, None]:
    system_prompt, api_messages = _anthropic_system_and_messages(messages)
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": 4000,
        "messages": api_messages,
        "stream": True,
    }
    if system_prompt:
        body["system"] = system_prompt

    tool_names: dict[int, str] = {}
    async with client.stream(
        "POST",
        f"{ANTHROPIC_BASE}/messages",
        headers=_anthropic_headers(api_key),
        json=body,
    ) as response:
        _raise_for_provider_error(response)
        async for event_name, data in _iter_sse_events(response):
            if not data:
                continue
            payload = _safe_json_loads(data)
            if event_name == "content_block_start":
                block = payload.get("content_block", {})
                if isinstance(block, Mapping) and block.get("type") == "tool_use":
                    tool_names[int(payload.get("index", 0))] = str(block.get("name", "tool"))
                continue
            if event_name == "content_block_delta":
                delta = payload.get("delta", {})
                if not isinstance(delta, Mapping):
                    continue
                delta_type = delta.get("type")
                if delta_type == "text_delta":
                    yield NormalizedStreamEvent(type="text_delta", text=str(delta.get("text", "")))
                elif delta_type == "input_json_delta":
                    index = int(payload.get("index", 0))
                    yield NormalizedStreamEvent(
                        type="tool_delta",
                        tool_name=tool_names.get(index),
                        tool_arguments_delta=str(delta.get("partial_json", "")),
                    )
            elif event_name == "message_stop":
                yield NormalizedStreamEvent(type="done")
            elif event_name == "error":
                error = payload.get("error", {})
                message = str(error.get("message") or payload.get("message") or "Anthropic streaming failed")
                yield NormalizedStreamEvent(type="error", error=message)


async def _stream_openrouter(
    client: httpx.AsyncClient,
    api_key: str,
    model: str,
    messages: Sequence[dict[str, str]],
) -> AsyncGenerator[NormalizedStreamEvent, None]:
    async with client.stream(
        "POST",
        f"{OPENROUTER_BASE}/chat/completions",
        headers=_openrouter_headers(api_key),
        json={
            "model": model,
            "messages": list(messages),
            "stream": True,
        },
    ) as response:
        _raise_for_provider_error(response)
        async for _event_name, data in _iter_sse_events(response):
            if not data or data.startswith(":"):
                continue
            if data == "[DONE]":
                yield NormalizedStreamEvent(type="done")
                continue
            payload = _safe_json_loads(data)
            error = payload.get("error")
            if isinstance(error, Mapping):
                yield NormalizedStreamEvent(
                    type="error",
                    error=str(error.get("message") or "OpenRouter streaming failed"),
                )
                continue
            choices = payload.get("choices", [])
            if not isinstance(choices, Sequence) or not choices:
                continue
            choice = choices[0]
            if not isinstance(choice, Mapping):
                continue
            delta = choice.get("delta", {})
            if isinstance(delta, Mapping):
                content = delta.get("content")
                if content:
                    yield NormalizedStreamEvent(type="text_delta", text=str(content))
                tool_calls = delta.get("tool_calls", [])
                if isinstance(tool_calls, Sequence):
                    for tool_call in tool_calls:
                        if not isinstance(tool_call, Mapping):
                            continue
                        function = tool_call.get("function", {})
                        if not isinstance(function, Mapping):
                            continue
                        arguments = function.get("arguments")
                        if arguments:
                            yield NormalizedStreamEvent(
                                type="tool_delta",
                                tool_name=str(function.get("name") or "tool"),
                                tool_arguments_delta=str(arguments),
                            )
            if choice.get("finish_reason") == "error" and isinstance(error, Mapping):
                yield NormalizedStreamEvent(
                    type="error",
                    error=str(error.get("message") or "OpenRouter streaming failed"),
                )


async def _iter_sse_events(
    response: httpx.Response,
) -> AsyncGenerator[tuple[str | None, str], None]:
    event_name: str | None = None
    data_lines: list[str] = []

    async for line in response.aiter_lines():
        if line == "":
            if data_lines or event_name is not None:
                yield event_name, "\n".join(data_lines)
            event_name = None
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[6:].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())

    if data_lines or event_name is not None:
        yield event_name, "\n".join(data_lines)


def _safe_json_loads(data: str) -> dict[str, Any]:
    try:
        loaded = json.loads(data)
    except json.JSONDecodeError as exc:
        raise ProviderResponseError(f"Invalid JSON payload in stream: {data[:200]}") from exc
    if not isinstance(loaded, dict):
        raise ProviderResponseError("Expected JSON object in stream payload")
    return loaded
