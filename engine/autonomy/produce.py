"""Decision write path (OBS-02) — produce a decision record and persist it.

This is the seam at the autonomy/router boundary: given an action's signals it
runs the (stub) jury, derives the route + escalation, and writes one decision
record. The console jury card binds to what this persists. Phase 5 swaps the stub
jury for the real cross-family panel here — the produced record shape is unchanged.
"""

from __future__ import annotations

from autonomy.decision import (
    DecisionRecord,
    GateResult,
    SafetyVerdict,
    derive_decision,
)
from autonomy.jury import expected_judge_count, stub_jury
from harness.router import DEFAULT_THRESHOLD
from harness.state import AutonomyMode, Gate


def resolve_channel_policy(pack, channel) -> tuple[float, AutonomyMode]:
    """Resolve ``(threshold, autonomy)`` for a channel from a tenant pack (INFRA-04).

    Maps the pack's autonomy dial onto the router's: a disabled/``OFF`` channel is
    treated as approve-first (it must never auto-fire). Imported types are the
    config layer's; the mapping keeps the router decoupled from pack config.
    """
    from config.schema import AutonomyMode as PackAutonomyMode

    cfg = pack.autonomy_for(channel)
    autonomy = AutonomyMode.AUTO if cfg.mode is PackAutonomyMode.AUTO else AutonomyMode.REVIEW
    return cfg.threshold, autonomy


def produce_and_record_decision(
    store,
    *,
    decision_id: str,
    run_id: str,
    tenant_id: str,
    channel: str,
    action_kind: str,
    base_confidence: float,
    threshold: float = DEFAULT_THRESHOLD,
    gates: list[Gate] | None = None,
    autonomy: AutonomyMode = AutonomyMode.AUTO,
    safety_verdict: SafetyVerdict = SafetyVerdict.PASS,
) -> DecisionRecord:
    """Produce a decision record from signals and persist it; return the record.

    ``base_confidence`` is the run's computed confidence signal feeding the stub
    jury; ``gates`` are the deterministic gate results from eng2's validator bank.
    """
    gates = gates or []
    votes = stub_jury(base_confidence)
    decision, esc, pooled, agreement = derive_decision(
        votes=votes,
        threshold=threshold,
        gates=gates,
        autonomy=autonomy,
        safety_verdict=safety_verdict,
        expected_judges=expected_judge_count(),
    )
    record = DecisionRecord(
        decision_id=decision_id,
        run_id=run_id,
        tenant_id=tenant_id,
        channel=channel,
        action_kind=action_kind,
        jury=votes,
        pooled_confidence=pooled,
        threshold=threshold,
        agreement=agreement,
        gates=[GateResult.from_gate(g) for g in gates],
        safety_verdict=safety_verdict,
        decision=decision,
        esc=esc,
    )
    store.record_decision(record)
    return record
