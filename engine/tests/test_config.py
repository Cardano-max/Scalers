"""Config tests (HARN-06): temperature-0 enforcement + pinned model versions."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from harness.config import (
    DEFAULT_HAIKU,
    POLICY_CEILING_MODEL,
    DEFAULT_SONNET,
    ModelPins,
    Settings,
)


def test_default_temperature_is_zero():
    assert Settings().temperature == 0.0


@pytest.mark.parametrize("bad", [0.1, 0.7, 1.0, -0.5])
def test_nonzero_temperature_rejected(bad):
    with pytest.raises(ValidationError):
        Settings(temperature=bad)


def test_models_are_pinned_to_stack_decision():
    models = Settings().models
    assert models.opus == POLICY_CEILING_MODEL == "claude-sonnet-4-5"  # 8sk: no opus tier
    assert models.sonnet == DEFAULT_SONNET == "claude-sonnet-4-5"  # 8sk ceiling
    assert models.haiku == DEFAULT_HAIKU == "claude-haiku-4-5"


def test_model_pins_are_frozen():
    pins = ModelPins()
    with pytest.raises(ValidationError):
        pins.opus = "claude-something-else"


def test_database_url_defaults_to_none(monkeypatch):
    # Hermetic: ignore any ambient ENGINE_DATABASE_URL (e.g. when the Postgres
    # integration suite is run in the same session).
    monkeypatch.delenv("ENGINE_DATABASE_URL", raising=False)
    assert Settings().database_url is None


def test_env_override_of_database_url(monkeypatch):
    monkeypatch.setenv("ENGINE_DATABASE_URL", "postgresql://localhost/scalers")
    assert Settings().database_url == "postgresql://localhost/scalers"
