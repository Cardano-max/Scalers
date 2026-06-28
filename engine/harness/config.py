"""Harness model policy: pinned models + temperature-0 enforcement (HARN-06).

Model versions are pinned to the exact strings from ``docs/stack-decision.md``.
Pinning is a harness law: an unpinned or drifting model silently changes
behaviour and invalidates the eval baseline. Decision/classify cells run at
``temperature == 0``; a non-zero override fails fast at construction time.

This is the harness's model/temperature policy — distinct from eng3's
per-tenant pack config (`engine/config/`, INFRA-04).
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# --------------------------------------------------------------------------- #
# Secret loading (SEC/INFRA): API keys load from a gitignored .env into the
# process environment at startup. The real .env is NEVER committed (.gitignore
# blocks .env / .env.*; only .env.example is tracked, with placeholders).
# --------------------------------------------------------------------------- #

_ENGINE_DIR = Path(__file__).resolve().parents[1]  # engine/
_REPO_ROOT = Path(__file__).resolve().parents[2]  # repo root


def load_env_file(path: Path) -> None:
    """Load one ``.env`` file into ``os.environ`` without clobbering real vars.

    ``override=False`` is the contract: an already-exported variable always wins,
    so CI/prod (which inject real env vars) ignore any stray ``.env``, and a
    missing file is a silent no-op (a ``.env`` is never required).
    """
    load_dotenv(path, override=False)


def load_local_env() -> None:
    """Load API keys + local overrides from the gitignored ``.env`` file(s).

    Both engine-local (``engine/.env``) and repo-root (``.env``) are loaded so the
    keys reach ``os.environ`` — Pydantic-AI reads ``ANTHROPIC_API_KEY`` directly
    from the environment, so loading here is what makes a ``.env``-supplied key
    work without any wiring at the call site. Precedence (highest first): real
    exported env var > ``engine/.env`` > repo-root ``.env``.
    """
    load_env_file(_ENGINE_DIR / ".env")
    load_env_file(_REPO_ROOT / ".env")


# Load at import so any later os.environ reader (Pydantic-AI, Settings) sees the
# keys. Idempotent and side-effect-free when no .env exists.
load_local_env()


class MissingAPIKeyError(RuntimeError):
    """A required API key is absent at use time.

    Raised lazily by the ``require_*`` accessors — NEVER at import — so importing
    the engine in a phase that does not need a given key (e.g. Foreplay before
    Phase 3) does not crash. The message names the var and the phase that needs
    it; it never echoes the value.
    """


def _require_api_key(name: str, *, needed_for: str) -> str:
    """Return ``os.environ[name]`` or raise :class:`MissingAPIKeyError`.

    An exported-but-empty value is treated as missing so the failure surfaces
    here with a clear message rather than as an opaque auth error inside a model
    call. The key value is never logged or included in the error.
    """
    value = os.environ.get(name)
    if not value:
        raise MissingAPIKeyError(
            f"{name} is not set but is required for {needed_for}. Add it to a "
            f"local .env (copy .env.example) or export it in the environment. "
            f"Never commit the real value."
        )
    return value


def require_anthropic_api_key() -> str:
    """ANTHROPIC_API_KEY, required from Phase 2 (real eval model calls)."""
    return _require_api_key("ANTHROPIC_API_KEY", needed_for="Phase 2 (eval real-model calls)")


def require_foreplay_api_key() -> str:
    """FOREPLAY_API_KEY, required from Phase 3 (Foreplay research)."""
    return _require_api_key("FOREPLAY_API_KEY", needed_for="Phase 3 (Foreplay research)")


# Pinned Claude model IDs (docs/stack-decision.md). Exact strings — no date
# suffixes. Opus 4.8 for the hardest writing/judging, Sonnet 4.6 as the
# balanced default, Haiku 4.5 for cheap classification/triage.
DEFAULT_OPUS = "claude-opus-4-8"
DEFAULT_SONNET = "claude-sonnet-4-6"
DEFAULT_HAIKU = "claude-haiku-4-5"


class ModelPins(BaseModel):
    """The pinned model versions used by the engine's typed cells (HARN-06)."""

    model_config = {"frozen": True}

    opus: str = DEFAULT_OPUS
    sonnet: str = DEFAULT_SONNET
    haiku: str = DEFAULT_HAIKU


class Settings(BaseSettings):
    """Engine settings, overridable via ``ENGINE_*`` environment variables.

    ``temperature`` is fixed at 0 for determinism on decision/classify cells;
    any other value raises at load time. ``database_url`` selects the durable
    Postgres checkpointer when set (else the in-memory checkpointer is used).
    """

    model_config = SettingsConfigDict(env_prefix="ENGINE_", frozen=True)

    temperature: float = 0.0
    models: ModelPins = ModelPins()
    database_url: str | None = None

    @field_validator("temperature")
    @classmethod
    def _temperature_must_be_zero(cls, value: float) -> float:
        if value != 0:
            raise ValueError(
                f"temperature must be 0 on decision/classify cells (HARN-06); got {value!r}"
            )
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""

    return Settings()
