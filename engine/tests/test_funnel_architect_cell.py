"""Tests for the funnel-architect cell (cells/funnel_architect.py).

Mirrors tests/test_content_brief_cell.py: a scripted FunctionModel exercises the
happy path, the repair path (a funnel with no conversion step, too few assets, or
an unfilled asset is repaired), and fail-on-code-path.
"""

from __future__ import annotations

import copy

import pytest

from cells.base import CellError
from cells.funnel_architect import FunnelPlan, FunnelStage, build_funnel_architect_cell
from tests.conftest import tool_model

_AWARENESS = {
    "stage": "awareness", "channel": "instagram", "asset_type": "reel",
    "purpose": "Show the linework process to earn new follows",
    "success_signal": "saves and profile visits",
}
_CONSIDERATION = {
    "stage": "consideration", "channel": "facebook", "asset_type": "carousel",
    "purpose": "Explain the consult + aftercare to lower hesitation",
    "success_signal": "link clicks",
}
_CONVERSION = {
    "stage": "conversion", "channel": "instagram", "asset_type": "story",
    "purpose": "Open the spring booking window with a clear single ask",
    "success_signal": "booking link taps",
}

VALID_PLAN = {
    "objective": "Fill the spring calendar with blackwork sleeve bookings",
    "audience": (
        "Local clients in their late twenties who already follow tattoo artists "
        "and are saving for a large piece"
    ),
    "primary_conversion": "Book a consult for a spring sleeve",
    "assets": [_AWARENESS, _CONVERSION],
}


def _plan(**overrides):
    payload = copy.deepcopy(VALID_PLAN)
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #


def test_returns_validated_pydantic_object():
    cell = build_funnel_architect_cell()
    out = cell.run_sync("Spring booking push for a tattoo studio", model=tool_model(_plan()))
    assert isinstance(out, FunnelPlan)
    stages = {a.stage for a in out.assets}
    assert FunnelStage.CONVERSION in stages  # the funnel actually asks for the conversion


def test_first_pass_metrics_reported():
    cell = build_funnel_architect_cell()
    res = cell.run_detailed_sync("ctx", model=tool_model(_plan()))
    assert res.first_pass_valid is True
    assert res.repairs == 0
    assert res.validation.ok
    assert cell.metrics.first_pass_rate == 1.0


# --------------------------------------------------------------------------- #
# Repair path
# --------------------------------------------------------------------------- #


def test_no_conversion_step_is_repaired():
    cell = build_funnel_architect_cell()
    bad = _plan(assets=[_AWARENESS, _CONSIDERATION])  # no conversion-stage asset -> ERROR
    res = cell.run_detailed_sync("ctx", model=tool_model(bad, _plan()))
    assert isinstance(res.value, FunnelPlan)
    assert res.repairs >= 1
    assert FunnelStage.CONVERSION in {a.stage for a in res.value.assets}


def test_too_few_assets_is_repaired():
    cell = build_funnel_architect_cell()
    bad = _plan(assets=[_CONVERSION])  # only one asset -> assets_count ERROR (min 2)
    res = cell.run_detailed_sync("ctx", model=tool_model(bad, _plan()))
    assert isinstance(res.value, FunnelPlan)
    assert res.repairs >= 1


def test_unfilled_asset_is_repaired():
    cell = build_funnel_architect_cell()
    blank = {**_CONVERSION, "purpose": ""}  # empty purpose -> assets_filled ERROR
    bad = _plan(assets=[_AWARENESS, blank])
    res = cell.run_detailed_sync("ctx", model=tool_model(bad, _plan()))
    assert isinstance(res.value, FunnelPlan)
    assert res.repairs >= 1


# --------------------------------------------------------------------------- #
# Fail-on-code-path
# --------------------------------------------------------------------------- #


def test_persistent_no_conversion_raises_cell_error():
    cell = build_funnel_architect_cell(retries=2)
    bad = _plan(assets=[_AWARENESS, _CONSIDERATION])  # never has a conversion step
    with pytest.raises(CellError) as ei:
        cell.run_sync("ctx", model=tool_model(bad))
    assert ei.value.attempts == 3
    assert cell.metrics.failed == 1


# --------------------------------------------------------------------------- #
# Framework guarantees
# --------------------------------------------------------------------------- #


def test_cell_is_temperature_zero_and_pinned_by_default():
    cell = build_funnel_architect_cell()
    assert cell.temperature == 0.0
    assert isinstance(cell.model, str) and cell.model
