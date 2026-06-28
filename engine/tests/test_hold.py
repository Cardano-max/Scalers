"""bead-439 autonomy HOLD — deterministic, fail-safe (CustomerAcq-b3f, P0).

DB-free unit coverage: the router forces REVIEW for HOLD overriding confidence +
dial (never AUTO), the registry is fail-safe (held unless lifted), and the slice
router honors the hold. The stub jury's hardcoded 0.9 confidence cannot unblock a
held tenant.
"""

from __future__ import annotations

import pytest

from config.loader import load_pack
from config.schema import Channel as PackChannel
from harness.hold import DEFAULT_HOLD_REGISTRY, HoldRegistry
from harness.router import route
from harness.state import AutonomyMode, Gate, RouteDecision
from phase1_slice import slice_route

PACK = load_pack("ink-studio")


# ── router.route: HOLD -> REVIEW, overriding everything but a broken gate ─────


def test_route_hold_forces_review():
    assert route(0.99, 0.85, None, AutonomyMode.HOLD) is RouteDecision.REVIEW


@pytest.mark.parametrize("confidence", [0.0, 0.5, 0.85, 0.9, 1.0])
def test_hold_overrides_any_confidence_never_auto(confidence):
    # AC#2/#3: the stub jury's hardcoded 0.9 (and any confidence) cannot route a
    # held tenant to AUTO. The decision is invariant to confidence under HOLD.
    assert route(confidence, 0.0, None, AutonomyMode.HOLD) is RouteDecision.REVIEW


def test_hold_overrides_the_dial():
    # Even an AUTO dial at max confidence is REVIEW when held.
    assert route(1.0, 0.5, None, AutonomyMode.HOLD) is RouteDecision.REVIEW


def test_broken_gate_still_regenerates_when_held():
    # arch's call: a failed gate is caught first (re-draft), preserving the
    # regenerate/escalate distinction; HOLD still never yields AUTO.
    gates = [Gate(name="banned_phrase", passed=False)]
    assert route(0.99, 0.85, gates, AutonomyMode.HOLD) is RouteDecision.REGENERATE


# ── HoldRegistry: fail-safe default (AC#4) ───────────────────────────────────


def test_registry_default_is_held():
    assert DEFAULT_HOLD_REGISTRY.is_held("any-tenant") is True
    assert DEFAULT_HOLD_REGISTRY.is_held("any-tenant", "instagram") is True


def test_whole_tenant_lift():
    reg = HoldRegistry().lift("ink-studio")
    assert reg.is_held("ink-studio") is False
    assert reg.is_held("ink-studio", "instagram") is False
    assert reg.is_held("other") is True  # other tenants still held


def test_per_channel_lift_is_scoped():
    reg = HoldRegistry().lift("ink-studio", "instagram")
    assert reg.is_held("ink-studio", "instagram") is False
    assert reg.is_held("ink-studio", "gmail") is True  # only instagram lifted


def test_effective_autonomy_holds_unless_lifted():
    held = HoldRegistry()
    assert held.effective_autonomy(AutonomyMode.AUTO, "t", "instagram") is AutonomyMode.HOLD
    lifted = held.lift("t", "instagram")
    assert lifted.effective_autonomy(AutonomyMode.AUTO, "t", "instagram") is AutonomyMode.AUTO


# ── slice_route honors the hold (AC#1/#2 at the slice layer) ─────────────────


def test_slice_route_held_routes_review_overriding_auto_pack():
    # ink-studio instagram is auto/0.85; held + the stub 0.9 confidence -> REVIEW.
    assert slice_route(PACK, PackChannel.INSTAGRAM, 0.9, held=True) is RouteDecision.REVIEW
    assert slice_route(PACK, PackChannel.INSTAGRAM, 1.0, held=True) is RouteDecision.REVIEW


def test_slice_route_not_held_preserves_pack_dial():
    # Regression guard (2kp/epq): not held -> the pack dial still governs.
    assert slice_route(PACK, PackChannel.INSTAGRAM, 0.9, held=False) is RouteDecision.AUTO
    assert slice_route(PACK, PackChannel.GMAIL, 0.9, held=False) is RouteDecision.REVIEW


# ── regenerate-cap-exceeded terminates to REVIEW, never auto (arch / ADR D4) ──


def test_regenerate_cap_exhausted_escalates_to_review_not_auto():
    from harness.recovery import RecoverableError, RecoveryLevel, run_with_recovery

    def always_fails():
        raise RecoverableError("still broken")

    result = run_with_recovery(always_fails, max_retries=2, regenerate=always_fails)
    # Cap exhausted -> human review, never an auto value.
    assert result.level is RecoveryLevel.HUMAN_REVIEW
    assert result.escalated is True
    assert result.value is None


def test_held_tenant_with_cap_exceeded_routes_review():
    from harness.recovery import RecoverableError, RecoveryLevel, run_with_recovery

    def always_fails():
        raise RecoverableError("broken")

    # The regenerate budget is exhausted -> escalate (never auto)...
    rec = run_with_recovery(always_fails, max_retries=1, regenerate=always_fails)
    assert rec.level is RecoveryLevel.HUMAN_REVIEW and rec.escalated
    # ...and for a HELD tenant the route is REVIEW regardless of any signal —
    # doubly safe: cap-exceeded AND held both forbid auto.
    assert slice_route(PACK, PackChannel.INSTAGRAM, 1.0, held=True) is RouteDecision.REVIEW
