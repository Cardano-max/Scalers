"""Tests for the critic cell (cells/critic.py).

Mirrors tests/test_content_brief_cell.py: drives the cell with a scripted
FunctionModel to exercise the happy path, the repair path (a non-approve verdict
with no issue is NOT allowed — it must be repaired), and fail-on-code-path.
"""

from __future__ import annotations

import copy

import pytest

from cells.base import CellError
from cells.critic import AssetCritique, Verdict, build_critic_cell
from tests.conftest import tool_model

# A clean APPROVE critique (issues may be empty only when approving).
VALID_APPROVE = {
    "verdict": "approve",
    "rationale": (
        "The hook is concrete and the caption leads with the client story before "
        "the studio, which fits the brief and earns the booking ask."
    ),
    "issues": [],
    "suggested_fixes": [],
    "confidence": 0.82,
}

# A clean REVISE critique — names a concrete issue and a fix (required).
VALID_REVISE = {
    "verdict": "revise",
    "rationale": (
        "The opening line buries the hook and the call to action is vague, so the "
        "post will not stop the scroll or drive a booking in its current form."
    ),
    "issues": [
        {
            "dimension": "hook_strength",
            "severity": "major",
            "note": "The first line states a fact instead of opening a curiosity loop.",
        }
    ],
    "suggested_fixes": ["Lead with the three-session transformation, not the studio name."],
    "confidence": 0.7,
}


def _crit(base, **overrides):
    payload = copy.deepcopy(base)
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #


def test_returns_validated_pydantic_object():
    cell = build_critic_cell()
    out = cell.run_sync("objective + asset text here", model=tool_model(VALID_APPROVE))
    assert isinstance(out, AssetCritique)
    assert out.verdict is Verdict.APPROVE
    assert 0.0 <= out.confidence <= 1.0


def test_first_pass_metrics_reported():
    cell = build_critic_cell()
    res = cell.run_detailed_sync("ctx", model=tool_model(VALID_REVISE))
    assert res.first_pass_valid is True
    assert res.repairs == 0
    assert res.validation.ok
    assert cell.metrics.first_pass_rate == 1.0


# --------------------------------------------------------------------------- #
# Honesty gate: a non-approve verdict with NO issue is not a critique -> repair
# --------------------------------------------------------------------------- #


def test_revise_with_no_issue_is_repaired():
    cell = build_critic_cell()
    bad = _crit(VALID_REVISE, issues=[])  # revise but names nothing -> validator ERROR
    res = cell.run_detailed_sync("ctx", model=tool_model(bad, VALID_REVISE))
    assert isinstance(res.value, AssetCritique)
    assert res.value.verdict is Verdict.REVISE
    assert res.value.issues  # the repaired version names the issue
    assert res.repairs >= 1


def test_approve_with_blocking_issue_is_repaired():
    cell = build_critic_cell()
    incoherent = _crit(
        VALID_APPROVE,
        issues=[{"dimension": "claim_safety", "severity": "blocking",
                 "note": "Claims a medical outcome with no support."}],
    )  # approve + blocking issue is incoherent -> ERROR
    res = cell.run_detailed_sync("ctx", model=tool_model(incoherent, VALID_APPROVE))
    assert isinstance(res.value, AssetCritique)
    assert res.repairs >= 1


def test_short_rationale_is_repaired():
    cell = build_critic_cell()
    terse = _crit(VALID_APPROVE, rationale="Looks good.")  # < 8 words
    res = cell.run_detailed_sync("ctx", model=tool_model(terse, VALID_APPROVE))
    assert isinstance(res.value, AssetCritique)
    assert res.repairs >= 1


def test_out_of_range_confidence_is_repaired():
    cell = build_critic_cell()
    bad = _crit(VALID_APPROVE, confidence=1.5)  # schema constraint ge<=1 -> pydantic repair
    res = cell.run_detailed_sync("ctx", model=tool_model(bad, VALID_APPROVE))
    assert isinstance(res.value, AssetCritique)
    assert res.repairs >= 1


# --------------------------------------------------------------------------- #
# Fail-on-code-path: persistently invalid output raises, never returns raw text
# --------------------------------------------------------------------------- #


def test_persistent_invalid_verdict_raises_cell_error():
    cell = build_critic_cell(retries=2)
    bad = _crit(VALID_REVISE, issues=[])  # always a non-approve verdict with no issue
    with pytest.raises(CellError) as ei:
        cell.run_sync("ctx", model=tool_model(bad))
    assert ei.value.attempts == 3
    assert cell.metrics.failed == 1


# --------------------------------------------------------------------------- #
# Framework guarantees
# --------------------------------------------------------------------------- #


def test_cell_is_temperature_zero_and_pinned_by_default():
    cell = build_critic_cell()
    assert cell.temperature == 0.0
    assert isinstance(cell.model, str) and cell.model
