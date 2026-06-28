"""Harness model policy: pinned models + temperature-0 enforcement (HARN-06).

Model versions are pinned to the exact strings from ``docs/stack-decision.md``.
Pinning is a harness law: an unpinned or drifting model silently changes
behaviour and invalidates the eval baseline. Decision/classify cells run at
``temperature == 0``; a non-zero override fails fast at construction time.

This is the harness's model/temperature policy — distinct from eng3's
per-tenant pack config (`engine/config/`, INFRA-04).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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
                "temperature must be 0 on decision/classify cells (HARN-06); "
                f"got {value!r}"
            )
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""

    return Settings()
