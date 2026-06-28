"""API-key plumbing tests (SEC/INFRA): .env loading + use-time key checks.

These cover the secret-loading contract: keys load from a gitignored ``.env``
into the process environment (so Pydantic-AI reads ``ANTHROPIC_API_KEY``
natively), a required key missing *at use time* raises a clear error, and
importing the engine never requires a key a given phase doesn't use.

No real key value appears here — placeholders only.
"""

from __future__ import annotations

import importlib

import pytest

from harness.config import (
    MissingAPIKeyError,
    require_anthropic_api_key,
    require_foreplay_api_key,
)

# Obvious non-secrets. Never put a real ``sk-ant-...`` here.
ANTHROPIC_PLACEHOLDER = "sk-ant-PLACEHOLDER-not-a-real-key"
FOREPLAY_PLACEHOLDER = "foreplay-PLACEHOLDER-not-a-real-key"


def test_require_anthropic_returns_value_when_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_PLACEHOLDER)
    assert require_anthropic_api_key() == ANTHROPIC_PLACEHOLDER


def test_require_anthropic_raises_clear_error_when_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(MissingAPIKeyError) as exc:
        require_anthropic_api_key()
    msg = str(exc.value)
    assert "ANTHROPIC_API_KEY" in msg
    assert "Phase 2" in msg  # error points the operator at when/why it's needed


def test_require_foreplay_returns_value_when_set(monkeypatch):
    monkeypatch.setenv("FOREPLAY_API_KEY", FOREPLAY_PLACEHOLDER)
    assert require_foreplay_api_key() == FOREPLAY_PLACEHOLDER


def test_require_foreplay_raises_clear_error_when_missing(monkeypatch):
    monkeypatch.delenv("FOREPLAY_API_KEY", raising=False)
    with pytest.raises(MissingAPIKeyError) as exc:
        require_foreplay_api_key()
    msg = str(exc.value)
    assert "FOREPLAY_API_KEY" in msg
    assert "Phase 3" in msg


@pytest.mark.parametrize("name", ["ANTHROPIC_API_KEY", "FOREPLAY_API_KEY"])
def test_empty_string_key_treated_as_missing(monkeypatch, name):
    # An exported-but-empty var is a common footgun; treat it as absent so the
    # error fires here rather than as an opaque 401 deep inside a model call.
    monkeypatch.setenv(name, "")
    require = require_anthropic_api_key if name == "ANTHROPIC_API_KEY" else require_foreplay_api_key
    with pytest.raises(MissingAPIKeyError):
        require()


def test_importing_config_never_requires_a_key(monkeypatch):
    # Importing the engine config with NO keys present must not raise — keys are
    # validated lazily at use time (Foreplay isn't needed until Phase 3), so a
    # Phase-1/2 import never crashes on a missing Phase-3 key.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("FOREPLAY_API_KEY", raising=False)
    import harness.config as cfg

    importlib.reload(cfg)  # re-executes module body (incl. .env load) with no keys


def test_dotenv_loads_keys_into_environment(monkeypatch, tmp_path):
    # The whole point of the plumbing: a value present only in a .env file ends
    # up in os.environ, so a native reader (Pydantic-AI) sees ANTHROPIC_API_KEY.
    import os

    from harness import config as cfg

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(f"ANTHROPIC_API_KEY={ANTHROPIC_PLACEHOLDER}\n", encoding="utf-8")

    cfg.load_env_file(env_file)
    assert os.environ.get("ANTHROPIC_API_KEY") == ANTHROPIC_PLACEHOLDER


def test_real_env_var_wins_over_dotenv(monkeypatch, tmp_path):
    # CI/prod inject real env vars; a stray .env must never clobber them.
    import os

    from harness import config as cfg

    monkeypatch.setenv("ANTHROPIC_API_KEY", "real-exported-value")
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=value-from-file\n", encoding="utf-8")

    cfg.load_env_file(env_file)
    assert os.environ.get("ANTHROPIC_API_KEY") == "real-exported-value"
