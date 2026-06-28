"""Unit regression: the Phase-1 slice routes on the tenant PACK autonomy dial.

CustomerAcq-2kp (safety): run_slice loaded the pack but routed on caller-supplied
autonomy/threshold defaults, so the per-tenant dial — the control that prevents
off-policy auto-fire — was ignored. The seed pack's gmail is mode=review/0.9, so
at confidence 0.9 the slice must REVIEW (it wrongly returned AUTO). These checks
are pure (load_pack reads TOML; route() is pure), so they run in the DB-free
done-gate — no Postgres needed.
"""

from __future__ import annotations

from config.loader import load_pack
from config.schema import Channel as PackChannel
from harness.state import Gate, RouteDecision
from phase1_slice import _SIDE_EFFECT_CHANNEL, slice_route
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
