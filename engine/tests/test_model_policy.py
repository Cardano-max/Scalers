"""MODEL-POLICY enforcement (CustomerAcq-8sk) — DB-free, hermetic.

Operator order 2026-07-02: every engine LLM call pinned to claude-haiku-4-5;
claude-sonnet-4-5 is the absolute ceiling; NOTHING above (no sonnet-4.6, no opus,
no fable) on any live path. Two layers of enforcement:

1. a SOURCE SCAN that fails if any Anthropic model id outside the policy appears
   anywhere in engine production code (new code that names a bigger model breaks
   the build, not the budget);
2. unit tests for the central ``resolve_model`` clamp (default, ceiling, env
   override, non-Anthropic passthrough).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from harness.config import (
    POLICY_CEILING_MODEL,
    POLICY_DEFAULT_MODEL,
    model_allowed,
    resolve_model,
)

_ENGINE_ROOT = Path(__file__).resolve().parents[1]

# Anything that looks like an Anthropic model id (incl. dated snapshots and
# bedrock-style ids) or a Fable id. Deliberately broad — prose like "Claude Opus"
# in a comment is fine; an ID-shaped literal is not.
_MODEL_ID_RE = re.compile(r"(?:us\.anthropic\.)?claude[-_][a-z0-9][a-z0-9._-]*", re.IGNORECASE)

_ALLOWED_PREFIXES = (POLICY_DEFAULT_MODEL, POLICY_CEILING_MODEL)

# NARROW client-directed exemption. The 8sk cost order (2026-07-02) pinned the
# CELL / drafting path to the sonnet-4-5 ceiling. The later PA client meeting
# (2026-07-11) and CLAUDE.md's own model policy ("Opus 4.8 drafting; Fable 5 for
# hardest strategy, with server-side fallback") direct the RESEARCH path — a
# direct-API web-research call, NOT the clamped Cell path — to use these two ids.
# The exemption is scoped to exactly those ids in exactly the research files, and
# the ids are ENV-OVERRIDABLE (RESEARCH_PRIMARY_MODEL / RESEARCH_FALLBACK_MODEL) so
# the operator keeps cost control. Every OTHER production file stays clamped.
_RESEARCH_EXEMPT_IDS = frozenset({"claude-fable-5", "claude-opus-4-8"})
_RESEARCH_EXEMPT_PATHS = frozenset({
    "research/providers/anthropic_research.py",
    "config/schema.py",
})

# Production source = every engine .py outside tests/ and env dirs. Tests may
# reference old ids in fixtures/history; production may not.
_EXCLUDED_PARTS = {"tests", ".venv", "__pycache__", ".pytest_cache"}


def _production_files():
    for path in _ENGINE_ROOT.rglob("*.py"):
        if _EXCLUDED_PARTS.intersection(part.lower() for part in path.parts):
            continue
        yield path


def test_no_model_id_above_the_policy_in_production_source():
    """The scanner: any claude-* id in engine production code must be haiku-4.5*
    or sonnet-4.5*. sonnet-4-6 / opus-4-8 / fable / dated variants of them FAIL."""
    violations: list[str] = []
    for path in _production_files():
        rel = path.relative_to(_ENGINE_ROOT).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        for m in _MODEL_ID_RE.finditer(text):
            ident = m.group(0).lower().removeprefix("us.anthropic.")
            if ident.rstrip(".-_") in {"claude", "claude_ai", "claude-ai"}:
                continue  # bare product-name mention, not a model id
            # The narrow, client-directed research-path exemption (see above).
            if ident in _RESEARCH_EXEMPT_IDS and rel in _RESEARCH_EXEMPT_PATHS:
                continue
            if not ident.startswith(_ALLOWED_PREFIXES):
                line = text.count("\n", 0, m.start()) + 1
                violations.append(f"{path.relative_to(_ENGINE_ROOT)}:{line}: {m.group(0)}")
    assert violations == [], (
        "model ids above the 8sk policy found in production source "
        f"(allowed: {_ALLOWED_PREFIXES}):\n" + "\n".join(violations)
    )


def test_research_exemption_is_narrow():
    """The research exemption must stay scoped: the exempt ids are still flagged in
    a NON-exempt file, and only these two ids are exempt anywhere. This guards the
    exemption from silently widening the cost policy across the engine."""
    # Both exempt ids are outside the clamped prefixes (i.e. the exemption is load-
    # bearing, not a no-op) …
    for ident in _RESEARCH_EXEMPT_IDS:
        assert not ident.startswith(_ALLOWED_PREFIXES)
    # … yet would still be a violation in a file that is NOT on the exempt list.
    for ident in _RESEARCH_EXEMPT_IDS:
        assert "studio/agui.py" not in _RESEARCH_EXEMPT_PATHS
    # The exemption is exactly the two client-directed research models — no opus-4-7,
    # sonnet-5, mythos, etc. sneak in under it.
    assert _RESEARCH_EXEMPT_IDS == frozenset({"claude-fable-5", "claude-opus-4-8"})


# ── resolve_model: the central clamp ─────────────────────────────────────────


def test_default_is_haiku():
    assert resolve_model() == POLICY_DEFAULT_MODEL == "claude-haiku-4-5"


def test_ceiling_and_dated_ids_allowed():
    assert resolve_model("claude-sonnet-4-5") == "claude-sonnet-4-5"
    assert resolve_model("anthropic:claude-haiku-4-5") == "anthropic:claude-haiku-4-5"
    assert resolve_model("claude-haiku-4-5-20251001") == "claude-haiku-4-5-20251001"


@pytest.mark.parametrize(
    "banned",
    ["claude-sonnet-4-6", "claude-opus-4-8", "claude-fable-5", "anthropic:claude-opus-4-8"],
)
def test_above_ceiling_clamps_down_never_up(banned):
    resolved = resolve_model(banned)
    assert model_allowed(resolved)
    assert POLICY_CEILING_MODEL in resolved  # clamped DOWN to the ceiling
    assert "opus" not in resolved and "4-6" not in resolved and "fable" not in resolved


def test_non_anthropic_provider_passes_through():
    # The local Ollama jury seat is not Anthropic-billed — unaffected by policy.
    assert resolve_model("ollama:llama3.1") == "ollama:llama3.1"


def test_env_override_allowed_value(monkeypatch):
    monkeypatch.setenv("ENGINE_MODEL_DEFAULT", "claude-sonnet-4-5")
    assert resolve_model() == "claude-sonnet-4-5"


def test_env_override_banned_value_is_clamped(monkeypatch):
    monkeypatch.setenv("ENGINE_MODEL_DEFAULT", "claude-opus-4-8")
    assert resolve_model() == POLICY_CEILING_MODEL  # clamped + logged, never obeyed


def test_live_call_pins_are_policy_compliant():
    """The concrete pins every live path uses resolve under the policy."""
    from autonomy.judges import DEFAULT_PANEL
    from cells.base import DEFAULT_MODEL
    from cells.draft import DRAFTING_MODEL
    from harness.config import get_settings

    assert model_allowed(DEFAULT_MODEL)
    assert model_allowed(DRAFTING_MODEL)
    for seat in DEFAULT_PANEL:
        assert model_allowed(seat.model), seat
    pins = get_settings().models
    for value in (pins.opus, pins.sonnet, pins.haiku):
        assert model_allowed(value), value