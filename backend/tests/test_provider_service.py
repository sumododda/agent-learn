"""Tests for provider registry and compatibility wrappers."""

from app.provider_service import (
    DEFAULT_MODEL,
    OPENROUTER_BASE,
    _ensure_selected_model_present,
    _openai_text_model_allowed,
    _openrouter_text_model_allowed,
    build_chat_model,
    get_default_model,
    get_provider_registry,
)


def test_build_chat_model_preserves_openrouter_base_for_compatibility():
    llm = build_chat_model("openrouter", "openai/gpt-4o-mini", {"api_key": "sk-test"})
    assert llm.model_name == "openai/gpt-4o-mini"
    assert llm.openai_api_base == OPENROUTER_BASE


def test_registry_lists_three_supported_providers():
    registry = get_provider_registry()["providers"]
    assert set(registry.keys()) == {"openai", "anthropic", "openrouter"}
    assert registry["anthropic"]["models"][0]["id"] == "claude-sonnet-4-6"


def test_provider_specific_defaults_are_available():
    assert DEFAULT_MODEL == "gpt-5.4-mini"
    assert get_default_model("openai") == "gpt-5.4-mini"
    assert get_default_model("anthropic") == "claude-sonnet-4-6"
    assert get_default_model("openrouter") == "google/gemini-3.1-pro-preview"


def test_openai_model_filter_excludes_non_text_models():
    assert _openai_text_model_allowed("gpt-5.4-mini") is True
    assert _openai_text_model_allowed("text-embedding-3-large") is False
    assert _openai_text_model_allowed("omni-moderation-latest") is False


def test_openrouter_model_filter_requires_text_capability():
    assert _openrouter_text_model_allowed(
        {
            "architecture": {
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            }
        }
    ) is True
    assert _openrouter_text_model_allowed(
        {
            "architecture": {
                "input_modalities": ["image"],
                "output_modalities": ["text"],
            }
        }
    ) is False


def test_selected_model_is_pinned_to_front_of_model_list():
    models = [
        {"id": "gpt-5.4", "name": "GPT-5.4", "context_length": 0, "pricing_prompt": "0", "pricing_completion": "0"},
        {"id": "gpt-5.4-mini", "name": "GPT-5.4 Mini", "context_length": 0, "pricing_prompt": "0", "pricing_completion": "0"},
    ]
    reordered = _ensure_selected_model_present(models, "gpt-5.4-mini")
    assert reordered[0]["id"] == "gpt-5.4-mini"


def test_missing_selected_model_is_injected_for_custom_saved_values():
    models = [
        {"id": "gpt-5.4-mini", "name": "GPT-5.4 Mini", "context_length": 0, "pricing_prompt": "0", "pricing_completion": "0"},
    ]
    reordered = _ensure_selected_model_present(models, "custom/provider-model")
    assert reordered[0]["id"] == "custom/provider-model"
