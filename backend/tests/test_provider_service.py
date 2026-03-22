"""Tests for provider service (OpenRouter)."""
import pytest
from app.provider_service import build_chat_model, DEFAULT_MODEL, OPENROUTER_BASE


class TestBuildChatModel:
    def test_returns_chat_openai(self):
        llm = build_chat_model("openrouter", "openai/gpt-4o-mini", {"api_key": "sk-test"})
        assert llm.model_name == "openai/gpt-4o-mini"
        assert str(llm.openai_api_base) == OPENROUTER_BASE

    def test_default_model_set(self):
        assert DEFAULT_MODEL == "openai/gpt-4o-mini"
