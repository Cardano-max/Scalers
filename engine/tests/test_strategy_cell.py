"""Tests for the campaign-strategy cell (P0 make-real / slice 2).

Cover the same HARN-02 guarantees the content-brief cell does — a cell returns a
validated Pydantic object, malformed output is repaired or fails on a code path
(never returned raw), valid rates are reported — plus the slice-2 helpers that feed
the strategy forward into the draft prompt.
"""

from __future__ import annotations

import copy

import pytest

from cells.base import CellError
from cells.strategy import (
    CampaignStrategy,
    build_strategy_cell,
    build_strategy_prompt,
    render_strategy,
)
from tests.conftest import tool_model

# A well-formed strategy payload reused across tests.
VALID_STRATEGY = {
    "target_angle": "Show the multi-session linework process to earn first-time client trust",
    "positioning": "The women-led studio where nervous first-timers feel safe and seen",
    "key_messages": [
        "Custom blackwork designed with you, not picked off a wall",
        "A calm, women-led space for your first tattoo",
        "Spring chairs are filling — book before the calendar closes",
    ],
    "channel_rationale": (
        "Instagram carries the process reels; Facebook reaches the local 30+ "
        "audience who book consultations"
    ),
}


def _strategy(**overrides):
    payload = copy.deepcopy(VALID_STRATEGY)
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------- #
# Happy path: validated typed object on the first pass
# --------------------------------------------------------------------------- #


def test_returns_validated_strategy_object():
    cell = build_strategy_cell()
    out = cell.run_sync("Spring booking push for the studio", model=tool_model(_strategy()))
    assert isinstance(out, CampaignStrategy)
    assert out.target_angle == VALID_STRATEGY["target_angle"]
    assert out.key_messages == VALID_STRATEGY["key_messages"]


def test_first_pass_metrics_reported():
    cell = build_strategy_cell()
    res = cell.run_detailed_sync("ctx", model=tool_model(_strategy()))
    assert res.first_pass_valid is True
    assert res.repairs == 0
    assert res.attempts == 1
    assert res.validation.ok
    assert cell.metrics.first_pass_rate == 1.0


# --------------------------------------------------------------------------- #
# Repair path: malformed output is repaired on retry, not returned raw
# --------------------------------------------------------------------------- #


def test_too_short_target_angle_is_repaired():
    # A one-word target angle trips word_count_between (min 2) -> repair -> valid.
    cell = build_strategy_cell()
    bad = _strategy(target_angle="Trust")
    res = cell.run_detailed_sync("ctx", model=tool_model(bad, _strategy()))
    assert isinstance(res.value, CampaignStrategy)
    assert res.first_pass_valid is False
    assert res.repairs >= 1


def test_empty_key_messages_is_repaired():
    # An empty key_messages list trips non_empty (ERROR) -> repair -> valid.
    cell = build_strategy_cell()
    bad = _strategy(key_messages=[])
    res = cell.run_detailed_sync("ctx", model=tool_model(bad, _strategy()))
    assert isinstance(res.value, CampaignStrategy)
    assert res.repairs >= 1


def test_placeholder_triggers_repair():
    cell = build_strategy_cell()
    tell = _strategy(positioning="TODO: positioning to be defined later")
    res = cell.run_detailed_sync("ctx", model=tool_model(tell, _strategy()))
    assert isinstance(res.value, CampaignStrategy)
    assert res.repairs >= 1


# --------------------------------------------------------------------------- #
# Fail-on-code-path: persistently malformed output raises, never returns raw text
# --------------------------------------------------------------------------- #


def test_persistent_validator_failure_raises_cell_error():
    cell = build_strategy_cell(retries=2)
    bad = _strategy(key_messages=[])  # always missing messages
    with pytest.raises(CellError) as ei:
        cell.run_sync("ctx", model=tool_model(bad))
    assert ei.value.attempts == 3  # 1 initial + 2 repairs
    assert cell.metrics.failed == 1


# --------------------------------------------------------------------------- #
# Framework-level guarantees
# --------------------------------------------------------------------------- #


def test_cell_is_temperature_zero_and_pinned_by_default():
    cell = build_strategy_cell()
    assert cell.temperature == 0.0
    assert isinstance(cell.model, str) and cell.model  # a pinned id string


# --------------------------------------------------------------------------- #
# Slice-2 helpers: strategy feeds forward into the draft prompt
# --------------------------------------------------------------------------- #


def test_build_strategy_prompt_includes_descriptor_and_brief():
    # build_strategy_prompt now takes the honest descriptor (from describe_tenant),
    # not a raw tenant_id — callers resolve it so identity is never fabricated.
    prompt = build_strategy_prompt(
        "@inkhaven — Inkhaven, a fine-line studio", "Spring booking push; warm voice"
    )
    assert "@inkhaven — Inkhaven, a fine-line studio" in prompt
    assert "Spring booking push" in prompt


def test_render_strategy_renders_every_field():
    strategy = CampaignStrategy(**VALID_STRATEGY)
    rendered = render_strategy(strategy)
    assert VALID_STRATEGY["target_angle"] in rendered
    assert VALID_STRATEGY["positioning"] in rendered
    assert VALID_STRATEGY["channel_rationale"] in rendered
    # every key message is rendered as a bullet
    for msg in VALID_STRATEGY["key_messages"]:
        assert msg in rendered
    assert "Target angle:" in rendered
    assert "Key messages:" in rendered
