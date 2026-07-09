"""Autonomy decision layer (OBS-02 schema + write path; AUTON-* real engine).

Persists an explainable decision record per action — per-judge/per-dimension jury
scores, pooled confidence, per-channel threshold, agreement, gate results, safety
verdict, route, and escalation — that the console jury card + confidence bar bind
to.

The LIVE write path is :func:`produce_and_record_decision_real` (4jx.2 real
cross-family jury + 4jx.3 computed confidence). The legacy
:func:`produce_and_record_decision` drives the STUB jury from a caller-supplied
``base_confidence`` — kept for Phase-1/2 back-compat and demos only; never wire it
onto a live decision path.
"""

from autonomy.aggregate import JuryAggregate, aggregate_jury
from autonomy.confidence import (
    Calibration,
    ConfidenceResult,
    anchored_self_consistency,
    compute_confidence,
    probe_self_consistency,
    self_consistency,
)
from autonomy.decision import (
    AGREEMENT_MIN,
    DecisionRecord,
    Escalation,
    EscKind,
    GateResult,
    JudgeVote,
    SafetyVerdict,
    agreement,
    derive_decision,
    pool_confidence,
)
from autonomy.judges import DEFAULT_PANEL, JudgeScore, JudgeSpec, run_jury
from autonomy.jury import JURY_PANEL, expected_judge_count, stub_jury
from autonomy.produce import (
    produce_and_record_decision,
    produce_and_record_decision_real,
    resolve_channel_policy,
)
from autonomy.store import (
    DecisionStore,
    InMemoryDecisionStore,
    PostgresDecisionStore,
)

__all__ = [
    # schema
    "DecisionRecord",
    "JudgeVote",
    "GateResult",
    "Escalation",
    "EscKind",
    "SafetyVerdict",
    "AGREEMENT_MIN",
    # derivation
    "derive_decision",
    "pool_confidence",
    "agreement",
    # real jury (4jx.2)
    "JudgeScore",
    "JudgeSpec",
    "DEFAULT_PANEL",
    "run_jury",
    "JuryAggregate",
    "aggregate_jury",
    # computed confidence (4jx.3)
    "Calibration",
    "ConfidenceResult",
    "compute_confidence",
    "self_consistency",
    "anchored_self_consistency",
    "probe_self_consistency",
    # jury (legacy stub — back-compat only)
    "stub_jury",
    "JURY_PANEL",
    "expected_judge_count",
    # store
    "DecisionStore",
    "InMemoryDecisionStore",
    "PostgresDecisionStore",
    # write path (real first)
    "produce_and_record_decision_real",
    "produce_and_record_decision",
    "resolve_channel_policy",
]
