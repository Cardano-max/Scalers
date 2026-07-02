"""Unit tests for the in-memory decision store + the producer write path (OBS-02)."""

from __future__ import annotations

from autonomy.decision import EscKind, GateResult, SafetyVerdict
from autonomy.jury import expected_judge_count
from autonomy.produce import produce_and_record_decision
from autonomy.store import InMemoryDecisionStore
from harness.state import AutonomyMode, Gate, RouteDecision


def test_producer_persists_full_record_queryable_by_id_and_run():
    store = InMemoryDecisionStore()
    rec = produce_and_record_decision(
        store,
        decision_id="d1",
        run_id="r1",
        tenant_id="ink-studio",
        channel="instagram",
        action_kind="post",
        base_confidence=0.9,
        threshold=0.85,
        allow_stub_auto=True,  # 4jx.17: stub auto is demo-flag-only
    )
    # Clean + confident -> auto, full per-judge jury persisted.
    assert rec.decision is RouteDecision.AUTO
    assert rec.esc.kind is EscKind.NONE
    assert len(rec.jury) == expected_judge_count()
    assert rec.pooled_confidence == 0.9
    assert rec.agreement == 1.0

    got = store.get_decision("d1")
    assert got == rec  # round-trips through the store
    assert [r.decision_id for r in store.list_decisions("r1")] == ["d1"]


def test_stub_producer_reviews_by_default_and_tags_stub_provenance():
    """4jx.17 structural closure at the producer: the stub path cannot AUTO
    without the explicit demo flag, and every stub decision is provenance-tagged
    so the LiftController can prove a channel is NOT driven by a stub (lift
    precondition (e)). Stub decisions never carry confidence_components."""
    from autonomy.produce import PROVENANCE_STUB_JURY

    store = InMemoryDecisionStore()
    rec = produce_and_record_decision(
        store,
        decision_id="d5",
        run_id="r5",
        tenant_id="t",
        channel="instagram",
        action_kind="post",
        base_confidence=0.95,  # would have been AUTO pre-closure
    )
    assert rec.decision is RouteDecision.REVIEW
    assert rec.esc.kind is EscKind.DEGRADED
    assert rec.confidence_provenance == PROVENANCE_STUB_JURY
    assert rec.confidence_components is None


def test_producer_records_gate_failure_as_regenerate():
    store = InMemoryDecisionStore()
    rec = produce_and_record_decision(
        store,
        decision_id="d2",
        run_id="r2",
        tenant_id="t",
        channel="instagram",
        action_kind="post",
        base_confidence=0.95,
        gates=[Gate(name="length", passed=False)],
    )
    assert rec.decision is RouteDecision.REGENERATE
    assert rec.esc.kind is EscKind.GATE
    assert rec.gates == [GateResult(label="length", ok=False)]


def test_producer_records_safety_veto_escalation():
    store = InMemoryDecisionStore()
    rec = produce_and_record_decision(
        store,
        decision_id="d3",
        run_id="r3",
        tenant_id="t",
        channel="gmail",
        action_kind="email",
        base_confidence=0.95,
        safety_verdict=SafetyVerdict.VETO,
    )
    assert rec.decision is RouteDecision.REVIEW
    assert rec.esc.kind is EscKind.SAFETY
    assert rec.safety_verdict is SafetyVerdict.VETO


def test_producer_review_mode_records_mode_escalation():
    store = InMemoryDecisionStore()
    rec = produce_and_record_decision(
        store,
        decision_id="d4",
        run_id="r4",
        tenant_id="t",
        channel="gmail",
        action_kind="email",
        base_confidence=0.95,
        autonomy=AutonomyMode.REVIEW,
    )
    assert rec.decision is RouteDecision.REVIEW
    assert rec.esc.kind is EscKind.MODE


def test_resolve_channel_policy_from_pack():
    from autonomy.produce import resolve_channel_policy
    from config import Channel, load_pack

    pack = load_pack("ink-studio")
    threshold, autonomy = resolve_channel_policy(pack, Channel.INSTAGRAM)
    assert threshold == pack.autonomy_for(Channel.INSTAGRAM).threshold
    assert autonomy is AutonomyMode.AUTO  # seed pack: IG is auto
    # gmail is approve-first in the seed pack -> maps to REVIEW
    _, gmail_autonomy = resolve_channel_policy(pack, Channel.GMAIL)
    assert gmail_autonomy is AutonomyMode.REVIEW
