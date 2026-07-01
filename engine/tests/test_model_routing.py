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


def test_caching_gates_on_provider_and_prefix_size_never_net_negative() -> None:
    from pydantic_ai.messages import CachePoint

    big = "x " * 3000  # ~1500 tokens, clears the Sonnet/Opus 1024 minimum
    small = "brand/offers"  # ~a few tokens — below the minimum

    # Anthropic + big prefix -> a REAL CachePoint after the stable prefix.
    prompt = mr.build_cached_prompt(big, "VOLATILE per-lead", mr.TIER_BEST)
    assert isinstance(prompt, list)
    assert prompt[0] == big and isinstance(prompt[1], CachePoint) and prompt[2] == "VOLATILE per-lead"
    assert mr.should_cache(big, mr.TIER_BEST) is True

    # A small prefix is NOT cached (a cache write would be net-negative) -> plain string.
    assert mr.should_cache(small, mr.TIER_BEST) is False
    assert isinstance(mr.build_cached_prompt(small, "v", mr.TIER_BEST), str)

    # A non-anthropic model is never anthropic-cached (leaves ollama/openai untouched).
    assert mr.should_cache(big, "ollama:llama3") is False
    assert isinstance(mr.build_cached_prompt(big, "v", "ollama:llama3"), str)

    # Haiku's minimum is higher (4096) — the same 1500-token prefix does NOT clear it.
    assert mr.should_cache(big, mr.TIER_CHEAP) is False


def test_cached_anthropic_settings_sets_real_cache_flags_when_worth_it() -> None:
    big = "x " * 3000
    settings = mr.cached_anthropic_settings(temperature=0.0, model=mr.TIER_BEST, stable_context=big)
    assert settings.get("anthropic_cache_instructions") is True
    assert settings.get("anthropic_cache_tool_definitions") is True
    assert settings.get("temperature") == 0.0
    # A small prefix -> no cache flags (never net-negative).
    small = mr.cached_anthropic_settings(temperature=0.0, model=mr.TIER_BEST, stable_context="tiny")
    assert small.get("anthropic_cache_instructions") is None
    # A non-anthropic model -> plain settings, no anthropic cache_control.
    other = mr.cached_anthropic_settings(temperature=0.0, model="ollama:llama3", stable_context=big)
    assert other.get("anthropic_cache_instructions") is None
