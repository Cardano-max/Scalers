"""Tests for the Langfuse wiring — focus on the hermetic, unconfigured paths."""

import observability as obs


def test_is_configured_false_when_env_missing():
    assert obs.is_configured({}) is False


def test_is_configured_false_when_partial():
    assert obs.is_configured({"LANGFUSE_PUBLIC_KEY": "pk"}) is False


def test_is_configured_false_when_empty_string():
    # Empty values must not count as configured.
    assert obs.is_configured({"LANGFUSE_PUBLIC_KEY": "", "LANGFUSE_SECRET_KEY": ""}) is False


def test_is_configured_true_when_all_present():
    assert obs.is_configured({"LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk"}) is True


def test_get_langfuse_returns_none_when_unconfigured(monkeypatch):
    # Clear any ambient config and the lru_cache so the test is deterministic.
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    obs.get_langfuse.cache_clear()
    assert obs.get_langfuse() is None
