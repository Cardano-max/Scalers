"""Autonomy decision layer (OBS-02 schema + write path; AUTON-* engine in Phase 5).

Persists an explainable decision record per action — per-judge/per-dimension jury
scores, pooled confidence, per-channel threshold, agreement, gate results, safety
verdict, route, and escalation — that the console jury card + confidence bar bind
to. A stub jury (:mod:`autonomy.jury`) fills the votes now; the Phase-5
cross-family jury replaces it with no schema change.
"""

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
from autonomy.jury import JURY_PANEL, expected_judge_count, stub_jury
from autonomy.produce import produce_and_record_decision, resolve_channel_policy
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
    # jury (stub)
    "stub_jury",
    "JURY_PANEL",
    "expected_judge_count",
    # store
    "DecisionStore",
    "InMemoryDecisionStore",
    "PostgresDecisionStore",
    # write path
    "produce_and_record_decision",
    "resolve_channel_policy",
]
