"""Decision write path (OBS-02) — produce a decision record and persist it.

This is the seam at the autonomy/router boundary: given an action's signals it
runs the (stub) jury, derives the route + escalation, and writes one decision
record. The console jury card binds to what this persists. Phase 5 swaps the stub
jury for the real cross-family panel here — the produced record shape is unchanged.
"""

from __future__ import annotations

from autonomy.aggregate import aggregate_jury
from autonomy.confidence import IDENTITY_CALIBRATION, Calibration, compute_confidence
from autonomy.decision import (
    DecisionRecord,
    GateResult,
    SafetyVerdict,
    derive_decision,
)
from autonomy.judges import DEFAULT_PANEL, JudgeRunner, JudgeSpec, is_cross_family, run_jury
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


async def produce_and_record_decision_real(
    store,
    *,
    decision_id: str,
    run_id: str,
    tenant_id: str,
    channel: str,
    action_kind: str,
    action: str,
    threshold: float = DEFAULT_THRESHOLD,
    gates: list[Gate] | None = None,
    autonomy: AutonomyMode = AutonomyMode.AUTO,
    safety_verdict: SafetyVerdict = SafetyVerdict.PASS,
    panel: tuple[JudgeSpec, ...] = DEFAULT_PANEL,
    judge_runner: JudgeRunner | None = None,
    self_consistency: float | None = None,
    calibration: Calibration = IDENTITY_CALIBRATION,
) -> DecisionRecord:
    """The REAL-jury write path (AUTON-01 / 4jx.2) — replaces the stub on the live
    path. Runs the cross-family panel on ``action``, aggregates per dimension
    (reliability-weighted means + the **hard-fail floor** + real per-dimension
    agreement), derives the route, and persists one decision record.

    Safety invariant (ADR monotonic composition): the jury only ever **blocks** an
    auto-fire (low score / split / hard-fail / degraded), never enables one — and a
    held channel's ``autonomy`` already forces review upstream, so no jury outcome
    can produce AUTO on a held channel. Auto stays OFF under 439.

    NOTE (gated): the new per-judge signals (``reliability_weight``, per-dimension
    ``hard_fail``, ``on_voice``) live on the in-memory record now but are dropped on
    DB persist until the 4jx.10 migration adds the columns; the decision itself
    (which already honors the hard-fail floor) is unaffected.
    """
    # Cross-family is a hard requirement (stack-decision): a single-family panel
    # cannot judge itself. Refuse a misconfigured panel loudly rather than silently
    # auto-firing on a single family.
    if not is_cross_family(panel):
        raise ValueError(
            f"jury panel is not cross-family (families={sorted({s.family for s in panel})}); "
            "need >= 2 distinct model families"
        )
    gates = gates or []
    jury = await run_jury(action, panel=panel, judge_runner=judge_runner)
    aggregate = aggregate_jury(jury.votes)
    # COMPUTED confidence (4jx.3): jury quality pooled conservatively with the
    # generator's self-consistency, calibrated. self_consistency=None (no probe ran /
    # too few samples) -> uncomputable -> the decision fails safe to review.
    conf = compute_confidence(
        jury_quality=aggregate.pooled,
        self_consistency_score=self_consistency,
        calibration=calibration,
        # 4jx.15: the live path supplies the channel threshold so a raw landing in
        # an unmeasured calibration bin at/above the bar is uncomputable -> review.
        threshold=threshold,
    )
    decision, esc, pooled, agreement = derive_decision(
        votes=jury.votes,
        threshold=threshold,
        gates=gates,
        autonomy=autonomy,
        safety_verdict=safety_verdict,
        expected_judges=jury.expected_judges,
        aggregate=aggregate,
        catalog_drift=jury.catalog_drift,
        catalog_drift_reason=jury.drift_reason,
        confidence=conf.confidence,
        confidence_uncomputable=conf.uncomputable,
        confidence_uncomputable_reason=conf.uncomputable_reason,
    )
    record = DecisionRecord(
        decision_id=decision_id,
        run_id=run_id,
        tenant_id=tenant_id,
        channel=channel,
        action_kind=action_kind,
        jury=jury.votes,
        pooled_confidence=pooled,
        threshold=threshold,
        agreement=agreement,
        self_consistency=conf.self_consistency,
        gates=[GateResult.from_gate(g) for g in gates],
        safety_verdict=safety_verdict,
        decision=decision,
        esc=esc,
    )
    store.record_decision(record)
    return record
