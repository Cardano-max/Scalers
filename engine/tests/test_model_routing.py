"""Compute routing + prompt-caching seam — pure/offline (no key, no network).

Proves (1) task→tier routing pins the best model for planning/adjudication and the cheap
model for extraction, and (2) the prompt-caching seam emits REAL Anthropic cache markers
(a ``CachePoint`` after the stable prefix + ``anthropic_cache_*`` settings), never a fake.
"""

from __future__ import annotations

from studio import model_routing as mr


def test_task_tiers_route_high_stakes_to_best_and_extraction_to_cheap() -> None:
    assert mr.model_for("planning") == mr.TIER_BEST
    assert mr.model_for("replanning") == mr.TIER_BEST
    assert mr.model_for("adjudication") == mr.TIER_BEST
    assert mr.model_for("extraction") == mr.TIER_CHEAP
    assert mr.model_for("tagging") == mr.TIER_CHEAP
    # A strategy task is the mid tier; an unknown task falls back to mid (never silent best).
    assert mr.model_for("strategy") == mr.TIER_MID
    assert mr.model_for("something-unknown") == mr.TIER_MID
    # The pins carry the anthropic provider prefix and the planner uses the best tier.
    assert mr.PLANNER_MODEL == mr.TIER_BEST
    assert mr.TIER_BEST.startswith("anthropic:claude-opus")
    assert mr.TIER_CHEAP.startswith("anthropic:claude-haiku")


def test_tier_of_labels_models() -> None:
    assert mr.tier_of(mr.TIER_BEST) == "best"
    assert mr.tier_of(mr.TIER_MID) == "mid"
    assert mr.tier_of(mr.TIER_CHEAP) == "cheap"
    assert mr.tier_of("anthropic:some-other-model") == "other"


def test_build_cached_prompt_marks_a_real_cache_point_after_the_stable_prefix() -> None:
    from pydantic_ai.messages import CachePoint

    prompt = mr.build_cached_prompt("STABLE brand/offers/taxonomy", "VOLATILE per-run")
    # A list: [stable, CachePoint, volatile] — the breakpoint sits AFTER the stable prefix.
    assert isinstance(prompt, list)
    assert prompt[0] == "STABLE brand/offers/taxonomy"
    assert isinstance(prompt[1], CachePoint)
    assert prompt[2] == "VOLATILE per-run"


def test_cached_anthropic_settings_sets_real_cache_flags() -> None:
    settings = mr.cached_anthropic_settings(temperature=0.0)
    # A real AnthropicModelSettings mapping carrying the cache_control markers.
    assert settings.get("anthropic_cache_instructions") is True
    assert settings.get("anthropic_cache_tool_definitions") is True
    assert settings.get("temperature") == 0.0
