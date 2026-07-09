"""DELIVERY item 3 — configurable output hard cap (spec §14): env override, bounded, honest.

``_OUTPUT_HARD_CAP`` (=12) stays the DEFAULT; :func:`archetypes.compose.output_hard_cap`
resolves the effective cap at call time from ``SCALERS_OUTPUT_HARD_CAP``:

* unset/blank → the default 12 (back-compat: every existing cap test still holds);
* a valid integer in [1..500] → honored (ask-25-get-25 and 500-lead cohorts);
* out of bounds → CLAMPED into [1..500] with a warning (the 500 runaway ceiling
  is absolute — no env value can exceed it);
* not an integer → the default, with a warning (a config typo never silently
  changes the fan-out).

Pure/offline — exercises the resolver + the ``_planned_channels`` fan-out sizing.
"""

from __future__ import annotations

import logging

import pytest

from archetypes.compose import (
    _OUTPUT_HARD_CAP,
    CampaignState,
    _planned_channels,
    output_hard_cap,
)

_ENV = "SCALERS_OUTPUT_HARD_CAP"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)


def _state(**kw) -> CampaignState:
    return CampaignState(
        campaign_id="c1", run_id="r1", tenant_id="demo",
        archetype_id="win_back", **kw,
    )


# ── the resolver ─────────────────────────────────────────────────────────────────


def test_default_is_the_backcompat_constant():
    assert output_hard_cap() == _OUTPUT_HARD_CAP == 12


def test_blank_env_is_the_default(monkeypatch):
    monkeypatch.setenv(_ENV, "   ")
    assert output_hard_cap() == 12


def test_env_value_in_bounds_is_honored(monkeypatch):
    monkeypatch.setenv(_ENV, "25")
    assert output_hard_cap() == 25
    monkeypatch.setenv(_ENV, "500")
    assert output_hard_cap() == 500
    monkeypatch.setenv(_ENV, "1")
    assert output_hard_cap() == 1


def test_absurd_values_are_clamped_with_warning(monkeypatch, caplog):
    with caplog.at_level(logging.WARNING, logger="archetypes.compose"):
        monkeypatch.setenv(_ENV, "99999")
        assert output_hard_cap() == 500
        monkeypatch.setenv(_ENV, "0")
        assert output_hard_cap() == 1
        monkeypatch.setenv(_ENV, "-7")
        assert output_hard_cap() == 1
    assert sum("clamped" in r.message for r in caplog.records) == 3


def test_non_integer_falls_back_to_default_with_warning(monkeypatch, caplog):
    with caplog.at_level(logging.WARNING, logger="archetypes.compose"):
        monkeypatch.setenv(_ENV, "twelve")
        assert output_hard_cap() == 12
        monkeypatch.setenv(_ENV, "12.5")
        assert output_hard_cap() == 12
    assert sum("not an integer" in r.message for r in caplog.records) == 2


# ── threaded through the fan-out (read at CALL time, not import time) ────────────


def test_planned_channels_honors_env_cap_at_call_time(monkeypatch):
    # ask-25-get-25: with the env raised, a 25-draft request yields 25 drafts.
    monkeypatch.setenv(_ENV, "25")
    assert len(_planned_channels(_state(output_count=25))) == 25
    # and the same state under the default env clips at 12 — call-time resolution.
    monkeypatch.delenv(_ENV, raising=False)
    assert len(_planned_channels(_state(output_count=25))) == 12


def test_planned_channels_clamps_absurd_env(monkeypatch):
    monkeypatch.setenv(_ENV, "99999")
    assert len(_planned_channels(_state(output_count=9999))) == 500
    monkeypatch.setenv(_ENV, "-3")
    assert len(_planned_channels(_state(output_count=10))) == 1


def test_default_behavior_unchanged_without_env():
    # regression: the original hard-cap semantics stand when the env is unset.
    assert len(_planned_channels(_state(output_count=9999))) == _OUTPUT_HARD_CAP
    assert len(_planned_channels(_state(output_count=4))) == 4
