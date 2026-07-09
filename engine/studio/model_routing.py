"""Compute routing + prompt caching for the studio spine (P1.5 blueprint #8).

Two honest, narrow jobs:

1. **Task → model tier.** :func:`model_for` maps a task class to a pinned model id.
   High-stakes reasoning (planning / adjudication / high-risk) routes to the best
   tier (Opus 4.8); strategy / copy to the mid tier (Sonnet 4.6); extraction /
   tagging / first-pass to the cheap tier (Haiku 4.5). The pins mirror
   ``harness.config`` so there is one source of truth. This module NEVER invents a
   route for a non-LLM code path — a caller records a routed model id on an
   ``agent_run`` ONLY when a real model call happened at that tier.

2. **Prompt caching seam.** :func:`cached_anthropic_settings` +
   :func:`build_cached_prompt` mark the STABLE prefix of a prompt (brand playbook,
   offers doc, taxonomy, tool schemas) with an Anthropic ``cache_control`` breakpoint
   so it is not re-billed on every planner turn. These are REAL pydantic-ai
   primitives (``AnthropicModelSettings.anthropic_cache_instructions`` /
   ``anthropic_cache_tool_definitions`` and ``messages.CachePoint``); nothing here
   fakes a cache. When pydantic-ai / the anthropic provider is unavailable the seam
   degrades to an un-cached prompt (a plain concatenation) rather than crashing — the
   call still runs, just without the cache marker.

Model ids carry the ``anthropic:`` provider prefix so they drop straight into a
pydantic-ai ``Agent`` / ``Cell`` and match the ``JURY_MODEL`` / ``HOST_AGUI_MODEL``
convention already in :mod:`studio.agui`.
"""

from __future__ import annotations

from typing import Any

# One source of truth for the pins (harness.config.DEFAULT_*). Imported lazily-safe:
# harness.config is pure/deterministic and always importable, but we fall back to the
# literal pins if the import ever fails so routing never hard-crashes a run.
try:
    from harness.config import DEFAULT_HAIKU, DEFAULT_OPUS, DEFAULT_SONNET
except Exception:  # pragma: no cover - defensive; harness.config is normally present
    DEFAULT_OPUS, DEFAULT_SONNET, DEFAULT_HAIKU = (
        "claude-sonnet-4-5",
        "claude-haiku-4-5",
        "claude-haiku-4-5",
    )

_PREFIX = "anthropic:"

# Tier ids (provider-prefixed) — what a routed model call actually runs at.
TIER_BEST = _PREFIX + DEFAULT_OPUS
TIER_MID = _PREFIX + DEFAULT_SONNET
TIER_CHEAP = _PREFIX + DEFAULT_HAIKU

# Task class → tier. Kept explicit (not a heuristic) so the route is auditable and a
# reviewer can see WHY the planner is Opus and extraction is Haiku.
_TASK_TIER: dict[str, str] = {
    # Best tier — the plan/adjudicate/high-risk reasoning where a wrong call is costly.
    "planning": TIER_BEST,
    "replanning": TIER_BEST,
    "adjudication": TIER_BEST,
    "jury": TIER_BEST,
    "high_risk": TIER_BEST,
    # Mid tier — strategy + on-brand copy.
    "strategy": TIER_MID,
    "copywriting": TIER_MID,
    "critique": TIER_MID,
    # Cheap tier — deterministic-adjacent extraction / tagging / first pass.
    "extraction": TIER_CHEAP,
    "tagging": TIER_CHEAP,
    "first_pass": TIER_CHEAP,
    "conversational": TIER_CHEAP,
}

# The default tier for an unknown task class — mid, never silently "best" (which would
# over-spend) and never silently "cheap" (which would under-serve).
_DEFAULT_TIER = TIER_MID

# Convenience pin for the planner node (blueprint #1): the plan is the highest-leverage
# reasoning in the run, so it routes to the best tier.
PLANNER_MODEL = _TASK_TIER["planning"]


def model_for(task: str) -> str:
    """The pinned, provider-prefixed model id for a task class (best/mid/cheap).

    Unknown task → the mid tier (never a silent best/cheap). Pure + deterministic."""
    return _TASK_TIER.get((task or "").strip().lower(), _DEFAULT_TIER)


def tier_of(model: str) -> str:
    """Reverse map a model id to its human tier label ('best'/'mid'/'cheap'/'other'),
    for honest display in the war-room. Tolerant of a missing provider prefix."""
    m = model if model.startswith(_PREFIX) else _PREFIX + (model or "")
    return {TIER_BEST: "best", TIER_MID: "mid", TIER_CHEAP: "cheap"}.get(m, "other")


# --------------------------------------------------------------------------- #
# Prompt caching seam — REAL Anthropic cache_control markers, never faked, and never
# NET-NEGATIVE: a cache WRITE costs ~1.25× so a prefix must clear the model's minimum
# cacheable size to be worth it. We cache ONLY when (a) the model is an anthropic model
# and (b) the STABLE prefix estimated tokens ≥ that model's minimum.
# --------------------------------------------------------------------------- #
# Anthropic minimum cacheable prompt size (tokens) by tier — below this the API will not
# cache the block, so a marker there is pure overhead. Haiku's floor is higher.
_MIN_CACHE_TOKENS_DEFAULT = 1024   # Sonnet / Opus
_MIN_CACHE_TOKENS_HAIKU = 4096     # Haiku 4.5


def _is_anthropic(model: str | None) -> bool:
    return bool(model) and str(model).startswith(_PREFIX)


def _min_cache_tokens(model: str | None) -> int:
    return _MIN_CACHE_TOKENS_HAIKU if (model and "haiku" in str(model)) else _MIN_CACHE_TOKENS_DEFAULT


def _estimate_tokens(text: str) -> int:
    """Cheap token estimate (~4 chars/token) — good enough to gate whether a prefix clears
    the cache minimum without importing a tokenizer."""
    return len(text or "") // 4


def should_cache(stable_context: str, model: str | None) -> bool:
    """True iff caching the stable prefix is worth it: an anthropic model AND a prefix that
    clears the model's minimum cacheable size. Prevents net-negative caching of small
    blocks and leaves non-anthropic (ollama/openai) routes untouched."""
    return _is_anthropic(model) and _estimate_tokens(stable_context) >= _min_cache_tokens(model)


def cache_point() -> Any:
    """A pydantic-ai :class:`~pydantic_ai.messages.CachePoint` breakpoint, or ``None`` if
    pydantic-ai is unavailable. Placed AFTER a stable context block so everything before it
    is cached by Anthropic."""
    try:
        from pydantic_ai.messages import CachePoint

        return CachePoint()
    except Exception:  # pragma: no cover - pydantic-ai always present in this repo
        return None


def build_cached_prompt(stable_context: str, volatile: str, model: str | None = None) -> Any:
    """Assemble a prompt whose STABLE prefix (brand/offers/taxonomy) is marked for Anthropic
    prompt caching — but ONLY when :func:`should_cache` holds (anthropic model + prefix over
    the minimum). Returns ``[stable_context, CachePoint(), volatile]`` when caching, else the
    plain concatenated string (a valid, un-cached prompt). Never net-negative, never faked."""
    if not should_cache(stable_context, model):
        return "\n\n".join(p for p in (stable_context, volatile) if p)
    cp = cache_point()
    if cp is None:
        return "\n\n".join(p for p in (stable_context, volatile) if p)
    parts: list[Any] = [stable_context, cp]  # everything up to CachePoint is the cached prefix
    if volatile:
        parts.append(volatile)
    return parts


def cached_anthropic_settings(
    temperature: float = 0.0, *, model: str | None = None, stable_context: str | None = None
) -> Any:
    """Anthropic model settings that cache the STABLE instruction + tool-schema prefix — the
    cache flags are set ONLY for an anthropic model whose stable prefix clears the minimum
    (when ``stable_context`` is given; otherwise the flags are set for any anthropic model,
    letting the API cache the system/tool blocks when they are large enough). Returns plain
    ``{temperature}`` for a non-anthropic model (no anthropic cache_control on other providers)."""
    want_cache = _is_anthropic(model) if model is not None else True
    if want_cache and stable_context is not None:
        want_cache = should_cache(stable_context, model or TIER_MID)
    try:
        from pydantic_ai.models.anthropic import AnthropicModelSettings

        if not want_cache:
            return AnthropicModelSettings(temperature=temperature)
        return AnthropicModelSettings(
            temperature=temperature,
            anthropic_cache_instructions=True,
            anthropic_cache_tool_definitions=True,
        )
    except Exception:  # pragma: no cover - anthropic provider present in this repo
        return {"temperature": temperature}
