"""Harness model policy: pinned models + temperature-0 enforcement (HARN-06).

Model versions are pinned to the exact strings from ``docs/stack-decision.md``.
Pinning is a harness law: an unpinned or drifting model silently changes
behaviour and invalidates the eval baseline. Decision/classify cells run at
``temperature == 0``; a non-zero override fails fast at construction time.

This is the harness's model/temperature policy — distinct from eng3's
per-tenant pack config (`engine/config/`, INFRA-04).
"""

from __future__ import annotations

import logging
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


# ── MODEL POLICY (CustomerAcq-8sk; operator orders 2026-07-02 + 2026-07-14) ───
# Every engine LLM call defaults to claude-haiku-4-5 — and per the operator's
# 2026-07-14 order ("use haiku 4.5 all the places, not bigger model, to avoid
# API cost") haiku is now ALSO the ceiling: every tier resolves to haiku and any
# bigger request clamps DOWN with an honest log. When the operator lifts the
# policy, set ENGINE_MODEL_CEILING to the bigger allowed id (e.g. the sonnet-4-5
# one) in the environment — no code edit.
POLICY_DEFAULT_MODEL = "claude-haiku-4-5"
POLICY_CEILING_MODEL = os.environ.get("ENGINE_MODEL_CEILING", "claude-haiku-4-5")
# Dated snapshots of the allowed models (e.g. claude-haiku-4-5-20251001) pass too.
_POLICY_ALLOWED_PREFIXES = (POLICY_DEFAULT_MODEL, POLICY_CEILING_MODEL)

_policy_log = logging.getLogger("engine.model_policy")


def _split_provider(model_id: str) -> tuple[str, str]:
    """Split ``provider:model`` -> (provider, bare id); provider may be ''."""
    if ":" in model_id:
        provider, _, bare = model_id.partition(":")
        return provider, bare
    return "", model_id


def model_allowed(model_id: str) -> bool:
    """True if ``model_id`` may be used for a live call under the 8sk policy.

    Non-Anthropic providers (e.g. the local ``ollama:`` jury seat) are not
    Anthropic-billed and pass through; Anthropic ids must be haiku-4.5* or
    sonnet-4.5*.
    """
    provider, bare = _split_provider(model_id)
    if provider and provider != "anthropic":
        return True
    return bare.startswith(_POLICY_ALLOWED_PREFIXES)


def resolve_model(requested: str | None = None) -> str:
    """Resolve a model id under the policy: default, allow, or CLAMP to ceiling.

    * ``requested=None`` → the ``ENGINE_MODEL_DEFAULT`` env override if set
      (itself clamped), else :data:`POLICY_DEFAULT_MODEL`.
    * an allowed id (haiku-4.5*/sonnet-4.5*, any provider-prefix form) → as-is.
    * a non-Anthropic provider id → as-is (not Anthropic-billed).
    * anything else (sonnet-4-6, opus, fable, unknown) → the CEILING, with an
      honest warning log — never silently, and never upward.
    """
    raw = requested if requested is not None else os.environ.get("ENGINE_MODEL_DEFAULT", "")
    if not raw:
        return POLICY_DEFAULT_MODEL
    if model_allowed(raw):
        return raw
    provider, _ = _split_provider(raw)
    clamped = f"{provider}:{POLICY_CEILING_MODEL}" if provider else POLICY_CEILING_MODEL
    _policy_log.warning(
        "model policy clamp (CustomerAcq-8sk): %r is above the ceiling; using %r",
        raw,
        clamped,
    )
    return clamped


# Pinned Claude model IDs. Exact strings — no date suffixes. POLICY-CLAMPED
# (8sk): every tier resolves through the policy — the "bigger" tiers are the
# ceiling (haiku under the 2026-07-14 order; ENGINE_MODEL_CEILING lifts it).
# DEFAULT_OPUS exists so studio.model_routing's import binds to THIS policy
# instead of its own fallback literals (which silently pinned sonnet).
DEFAULT_OPUS = POLICY_CEILING_MODEL
DEFAULT_SONNET = POLICY_CEILING_MODEL
DEFAULT_HAIKU = POLICY_DEFAULT_MODEL


class ModelPins(BaseModel):
    """The pinned model versions used by the engine's typed cells (HARN-06).

    Field NAMES are the legacy tier labels (kept for the API/console wire
    contract); the VALUES are what is actually called — under the 8sk policy the
    former opus tier is clamped to the sonnet-4.5 ceiling and the balanced tier
    to haiku. Provenance records use the values, so they stay honest.
    """

    model_config = {"frozen": True}

    opus: str = POLICY_CEILING_MODEL  # top tier ≡ the ceiling under 8sk (no opus)
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
