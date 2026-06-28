"""Tests for the typed tenant-pack schema (config.schema)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from config.schema import (
    AutonomyConfig,
    AutonomyMode,
    Channel,
    ChannelConfig,
    SecretRef,
    TenantPack,
    VoiceRef,
)


def _pack(**overrides) -> TenantPack:
    base = dict(
        tenant_id="acme",
        display_name="Acme",
        voice=VoiceRef(skill="brand-voice/acme"),
        channels={
            Channel.INSTAGRAM: ChannelConfig(
                autonomy=AutonomyConfig(mode=AutonomyMode.AUTO, threshold=0.85)
            ),
        },
    )
    base.update(overrides)
    return TenantPack(**base)


# -- AutonomyConfig.decision: the pack value that drives behavior ------------ #


def test_auto_above_threshold_decides_auto():
    cfg = AutonomyConfig(mode=AutonomyMode.AUTO, threshold=0.8)
    assert cfg.decision(0.81) == "auto"
    assert cfg.decision(0.80) == "auto"  # boundary is inclusive


def test_auto_below_threshold_decides_review():
    cfg = AutonomyConfig(mode=AutonomyMode.AUTO, threshold=0.8)
    assert cfg.decision(0.79) == "review"


def test_review_mode_always_reviews():
    cfg = AutonomyConfig(mode=AutonomyMode.REVIEW, threshold=0.1)
    assert cfg.decision(0.99) == "review"


def test_off_mode_is_off():
    cfg = AutonomyConfig(mode=AutonomyMode.OFF)
    assert cfg.decision(1.0) == "off"


def test_threshold_out_of_range_rejected():
    with pytest.raises(ValidationError):
        AutonomyConfig(threshold=1.5)
    with pytest.raises(ValidationError):
        AutonomyConfig(threshold=-0.1)


def test_autonomy_defaults_are_approve_first():
    cfg = AutonomyConfig()
    assert cfg.mode is AutonomyMode.REVIEW
    assert cfg.threshold == 0.8


# -- SecretRef: env-resolved, never inlined --------------------------------- #


def test_secret_ref_resolves_from_env(monkeypatch):
    ref = SecretRef(env="MY_TOKEN")
    assert ref.resolve() is None
    monkeypatch.setenv("MY_TOKEN", "s3cret")
    assert ref.resolve() == "s3cret"
    assert ref.require() == "s3cret"


def test_secret_ref_require_raises_when_unset():
    with pytest.raises(KeyError):
        SecretRef(env="DEFINITELY_NOT_SET_12345").require()


# -- TenantPack accessors + defaults ---------------------------------------- #


def test_channel_accessor_defaults_for_unconfigured():
    pack = _pack()
    # facebook not configured -> default ChannelConfig (review/0.8)
    fb = pack.channel(Channel.FACEBOOK)
    assert fb.autonomy.mode is AutonomyMode.REVIEW
    assert fb.autonomy.threshold == 0.8


def test_autonomy_for_returns_configured():
    pack = _pack()
    ig = pack.autonomy_for(Channel.INSTAGRAM)
    assert ig.mode is AutonomyMode.AUTO and ig.threshold == 0.85


def test_enabled_channels_excludes_off_and_disabled():
    pack = _pack(
        channels={
            Channel.INSTAGRAM: ChannelConfig(enabled=True, autonomy=AutonomyConfig(mode=AutonomyMode.AUTO)),
            Channel.FACEBOOK: ChannelConfig(enabled=False),
            Channel.GMAIL: ChannelConfig(autonomy=AutonomyConfig(mode=AutonomyMode.OFF)),
        }
    )
    assert pack.enabled_channels() == (Channel.INSTAGRAM,)
    assert pack.is_enabled(Channel.INSTAGRAM)
    assert not pack.is_enabled(Channel.FACEBOOK)
    assert not pack.is_enabled(Channel.GMAIL)


def test_unknown_top_level_key_rejected():
    # A typo'd field should fail loudly, not be silently ignored.
    with pytest.raises(ValidationError):
        TenantPack(
            tenant_id="acme",
            display_name="Acme",
            voice=VoiceRef(skill="v"),
            autonmy="typo",  # noqa: misspelled on purpose
        )


def test_pack_is_frozen():
    pack = _pack()
    with pytest.raises(ValidationError):
        pack.tenant_id = "other"  # type: ignore[misc]
