"""Tests for in-memory credential cache."""
import time
from datetime import datetime, timezone, timedelta
import app.key_cache as cache


def setup_function():
    cache._clear_all()


class TestPopulateAndGet:
    def test_populate_and_get(self):
        cache.populate("user1", {"anthropic": {"api_key": "sk-123"}}, "anthropic")
        creds = cache.get("user1", "anthropic")
        assert creds == {"api_key": "sk-123"}

    def test_get_missing_user(self):
        assert cache.get("nonexistent", "anthropic") is None

    def test_get_missing_provider(self):
        cache.populate("user1", {"anthropic": {"api_key": "sk-123"}}, "anthropic")
        assert cache.get("user1", "openai") is None


class TestGetDefault:
    def test_returns_default_provider(self):
        cache.populate("user1", {
            "anthropic": {"api_key": "sk-ant"},
            "openrouter": {"api_key": "sk-or"},
        }, "openrouter")
        result = cache.get_default("user1")
        assert result == ("openrouter", {"api_key": "sk-or"})

    def test_falls_back_to_first(self):
        cache.populate("user1", {"mistral": {"api_key": "sk-m"}}, None)
        result = cache.get_default("user1")
        assert result == ("mistral", {"api_key": "sk-m"})

    def test_returns_none_for_empty(self):
        cache.populate("user1", {}, None)
        assert cache.get_default("user1") is None

    def test_returns_none_for_missing_user(self):
        assert cache.get_default("nonexistent") is None


class TestTTLEviction:
    def test_expired_entry_returns_none(self):
        cache.populate("user1", {"anthropic": {"api_key": "sk-123"}}, "anthropic", ttl_seconds=0)
        # Entry should be expired immediately (or within milliseconds)
        import time
        time.sleep(0.01)
        assert cache.get("user1", "anthropic") is None

    def test_expired_entry_is_removed(self):
        cache.populate("user1", {"anthropic": {"api_key": "sk-123"}}, "anthropic", ttl_seconds=0)
        time.sleep(0.01)
        cache.get("user1", "anthropic")  # triggers eviction
        assert "user1" not in cache._cache


class TestClear:
    def test_clear_removes_user(self):
        cache.populate("user1", {"anthropic": {"api_key": "sk-123"}}, "anthropic")
        cache.clear("user1")
        assert cache.get("user1", "anthropic") is None

    def test_clear_nonexistent_no_error(self):
        cache.clear("nonexistent")  # should not raise
