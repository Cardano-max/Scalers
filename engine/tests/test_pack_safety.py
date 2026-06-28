"""Safety regression tests for pack typo/range hardening (CustomerAcq-vvi).

The bug: only ``TenantPack`` forbade extra keys, so a typo in a NESTED pack table
(e.g. ``treshold`` for ``threshold``) was silently dropped and the field fell
back to its loose default — an operator tightening the auto-bar could silently
*lower* it, causing off-policy auto-fire. These tests assert the typo now RAISES,
plus the secondary range/empty validators.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from config.loader import PackLoader, PackValidationError
from config.schema import (
    AutonomyConfig,
    ChannelConfig,
    RateLimits,
    ResearchConfig,
    ScheduleConfig,
    SecretRef,
    SuppressionConfig,
    TenantPack,
    VoiceRef,
)


# -- The core bug: a typo'd nested autonomy key must NOT be silently ignored - #


def test_autonomy_typo_threshold_raises_not_silently_ignored():
    # 'treshold' is the exact typo from the bug report. It must raise, not be
    # dropped (which would leave the loose default 0.8 auto-bar in place).
    with pytest.raises(ValidationError):
        AutonomyConfig(mode="auto", treshold=0.95)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "model, kwargs",
    [
        (AutonomyConfig, {"mode": "auto", "treshold": 0.95}),
        (ChannelConfig, {"enabledd": True}),
        (RateLimits, {"per_dya": 25}),
        (VoiceRef, {"skill": "v", "exampls_uri": "x"}),
        (SecretRef, {"env": "X", "value": "leaked"}),
        (SuppressionConfig, {"source": "s", "kind": "x"}),
        (ScheduleConfig, {"timezne": "UTC"}),
        (ResearchConfig, {"sourcs": []}),
    ],
)
def test_all_nested_models_forbid_extra_keys(model, kwargs):
    with pytest.raises(ValidationError):
        model(**kwargs)


# -- Same bug, end to end through the loader -------------------------------- #

PACK_WITH_NESTED_TYPO = """
tenant_id = "acme"
display_name = "Acme"

[voice]
skill = "brand-voice/acme"

[channels.instagram]
enabled = true
[channels.instagram.autonomy]
mode = "auto"
treshold = 0.95
"""


def test_loader_rejects_nested_typo(tmp_path):
    (tmp_path / "acme.toml").write_text(PACK_WITH_NESTED_TYPO, encoding="utf-8")
    loader = PackLoader(tmp_path)
    with pytest.raises(PackValidationError):
        loader.load("acme")


# -- Secondary: quiet_hours range + non-empty tenant_id --------------------- #


def test_quiet_hours_out_of_range_raises():
    with pytest.raises(ValidationError):
        ScheduleConfig(quiet_hours=(25, 30))
    with pytest.raises(ValidationError):
        ScheduleConfig(quiet_hours=(-1, 6))
    # In-range is accepted.
    assert ScheduleConfig(quiet_hours=(22, 7)).quiet_hours == (22, 7)


def test_empty_tenant_id_raises():
    with pytest.raises(ValidationError):
        TenantPack(tenant_id="", display_name="X", voice=VoiceRef(skill="v"))


PACK_WITH_BAD_QUIET_HOURS = """
tenant_id = "acme"
display_name = "Acme"
[voice]
skill = "brand-voice/acme"
[schedule]
quiet_hours = [25, 30]
"""


def test_loader_rejects_out_of_range_quiet_hours(tmp_path):
    (tmp_path / "acme.toml").write_text(PACK_WITH_BAD_QUIET_HOURS, encoding="utf-8")
    loader = PackLoader(tmp_path)
    with pytest.raises(PackValidationError):
        loader.load("acme")
