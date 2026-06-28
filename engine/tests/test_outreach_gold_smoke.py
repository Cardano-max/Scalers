"""Outreach smoke-eval: replay the gold set through the policy (bead 1mk.7).

DB-free, hermetic. Proves the gold-set-proven depth bar at SMOKE level for the
outreach engine: suppression-first, deliverability, sequence structure, hard-stop,
over-personalization, and the 439 safety hold. The real holdout + calibration are
Phase-2 rvy.7/.8; registry eval-gate stays PENDING-on-gold-set until then.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from outreach import OutreachPolicy, Prospect, SuppressionGate, prospect_ref

_GOLD = Path(__file__).resolve().parents[2] / "evals" / "gold" / "outreach-smoke.jsonl"


def _cases() -> list[dict]:
    return [json.loads(line) for line in _GOLD.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_gold_set_meets_hard_negative_floor():
    cases = _cases()
    hard = [c for c in cases if c.get("hard")]
    assert len(cases) >= 12
    assert len(hard) / len(cases) >= 0.30, f"hard floor not met: {len(hard)}/{len(cases)}"


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["id"])
def test_outreach_smoke_case(case):
    inp = case["input"]
    label = case["label_payload"]
    email = inp["email"]

    suppression = (
        SuppressionGate(emails=[email]) if inp.get("suppressed") else SuppressionGate()
    )
    policy = OutreachPolicy(suppression=suppression)
    plan = policy.plan(
        Prospect(email=email, signals=tuple(inp.get("signals", ()))),
        events=tuple(inp.get("events", ())),
    )

    assert plan.disposition.value == label["disposition"], case["id"]

    if label.get("no_sequence"):
        assert plan.sequence is None
    if label.get("no_verification"):
        assert plan.verification is None
    if "sequence_length" in label:
        assert plan.sequence.length == label["sequence_length"]
    if "day_offsets" in label:
        assert [t.day_offset for t in plan.sequence.touches] == label["day_offsets"]
    if label.get("every_touch_unsubscribe"):
        assert all(t.includes_unsubscribe for t in plan.sequence.touches)
    if "verification_status" in label:
        assert plan.verification.status == label["verification_status"]
    if "reason_contains" in label:
        assert any(label["reason_contains"] in r for r in (plan.notes + (plan.verification.reasons if plan.verification else ())))
    if "warning_contains" in label:
        assert any(label["warning_contains"] in w for w in plan.warnings)
    if label.get("creepy_blocked"):
        briefs = [b for t in plan.sequence.touches for b in t.personalization_brief]
        assert all("divorce" not in b for b in briefs)
    if "max_refs_per_touch" in label:
        assert all(len(t.personalization_brief) <= label["max_refs_per_touch"] for t in plan.sequence.touches)
    if label.get("pii_free"):
        assert email not in repr(plan)
        assert plan.prospect_ref == prospect_ref(email)
    if "will_send" in label:
        assert plan.will_send is label["will_send"]
        assert plan.routed_to == "review"
