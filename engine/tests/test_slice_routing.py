"""Unit regression: the Phase-1 slice routes on the tenant PACK autonomy dial.

CustomerAcq-2kp (safety): run_slice loaded the pack but routed on caller-supplied
autonomy/threshold defaults, so the per-tenant dial — the control that prevents
off-policy auto-fire — was ignored. The seed pack's gmail is mode=review/0.9, so
at confidence 0.9 the slice must REVIEW (it wrongly returned AUTO). These checks
are pure (load_pack reads TOML; route() is pure), so they run in the DB-free
done-gate — no Postgres needed.
"""

from __future__ import annotations

import pytest

import phase1_slice
from config.loader import load_pack
from config.schema import Channel as PackChannel
from harness.state import Gate, RouteDecision
from phase1_slice import _assert_channel_map_total, _SIDE_EFFECT_CHANNEL, slice_route
from sideeffects import Channel

PACK = load_pack("ink-studio")  # shipped seed pack (TOML, no DB)


# ── The bug: review-mode channel must NOT auto-fire at high confidence ───────


def test_gmail_review_channel_routes_review_not_auto():
    # Seed pack: gmail mode=review threshold=0.9. At confidence 0.9 the OLD slice
    # returned AUTO (caller default auto/0.85); the dial must force REVIEW.
    assert slice_route(PACK, PackChannel.GMAIL, 0.9) is RouteDecision.REVIEW
    assert slice_route(PACK, PackChannel.GMAIL, 0.99) is RouteDecision.REVIEW


# ── Auto channels behave per pack ────────────────────────────────────────────


def test_instagram_and_facebook_auto_above_their_bar():
    # Seed pack: instagram/facebook mode=auto threshold=0.85.
    assert slice_route(PACK, PackChannel.INSTAGRAM, 0.9) is RouteDecision.AUTO
    assert slice_route(PACK, PackChannel.FACEBOOK, 0.9) is RouteDecision.AUTO


def test_auto_channel_below_its_bar_reviews():
    assert slice_route(PACK, PackChannel.INSTAGRAM, 0.5) is RouteDecision.REVIEW


def test_threshold_boundary_is_inclusive_auto():
    # instagram bar is 0.85; exactly at the bar auto-fires.
    assert slice_route(PACK, PackChannel.INSTAGRAM, 0.85) is RouteDecision.AUTO


def test_failed_gate_regenerates_regardless_of_channel():
    gates = [Gate(name="banned_phrase", passed=False)]
    assert slice_route(PACK, PackChannel.INSTAGRAM, 0.95, gates) is RouteDecision.REGENERATE
    assert slice_route(PACK, PackChannel.GMAIL, 0.95, gates) is RouteDecision.REGENERATE


# ── The secondary enum-mismatch fix: pack channel -> side-effect channel ──────


def test_side_effect_channel_mapping_is_total_and_correct():
    # Every pack/platform channel maps to a side-effect (outbox) channel.
    assert set(_SIDE_EFFECT_CHANNEL) == set(PackChannel)
    assert _SIDE_EFFECT_CHANNEL[PackChannel.INSTAGRAM] is Channel.POSTING
    assert _SIDE_EFFECT_CHANNEL[PackChannel.FACEBOOK] is Channel.POSTING
    assert _SIDE_EFFECT_CHANNEL[PackChannel.GMAIL] is Channel.OUTREACH


# ── epq: fail-closed hardening (defense-in-depth on the vvi safety surface) ───


def test_totality_guard_fails_fast_on_unmapped_channel():
    # Structural totality: an incomplete map raises at the guard (which runs at
    # import), so adding a config.Channel without mapping it breaks the build/test.
    with pytest.raises(RuntimeError) as ei:
        _assert_channel_map_total({PackChannel.INSTAGRAM: Channel.POSTING})
    msg = str(ei.value)
    assert "facebook" in msg and "gmail" in msg
    # The shipped map is total -> the guard passes.
    _assert_channel_map_total(_SIDE_EFFECT_CHANNEL)


def test_unmapped_channel_is_fail_closed_to_review_never_auto(monkeypatch):
    # Simulate a future channel that is NOT in the side-effect map. Even though
    # the pack would auto-fire instagram at 0.9, an unmapped channel must route
    # REVIEW (never AUTO) — it cannot be auto-delivered.
    partial = {PackChannel.GMAIL: Channel.OUTREACH}  # instagram now unmapped
    monkeypatch.setattr(phase1_slice, "_SIDE_EFFECT_CHANNEL", partial)
    assert slice_route(PACK, PackChannel.INSTAGRAM, 0.99) is RouteDecision.REVIEW
    # A mapped channel still behaves per pack under the same patch.
    assert slice_route(PACK, PackChannel.GMAIL, 0.9) is RouteDecision.REVIEW
