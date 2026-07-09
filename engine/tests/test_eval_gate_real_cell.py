"""CustomerAcq-wwy.2 — the eval gate scores the SHIPPED triage cell, not a shadow.

The per-commit eval gate (``evals.run_gate`` -> ``evals.gate.run_eval_gate`` with
``evals.predictors.cell_predictor``) must evaluate the real customer-facing
``engagement.triage.classify_comment`` path. Before this bead the predictor was a
keyword classifier transcribed from the gold set's OWN keywords, so a regression
in the shipped cell could not move the metric — a tautology that structurally
could not catch a Skin-Design complaint being auto-classified positive.

The load-bearing proof (bead AC): breaking the shipped cell — removing ``swollen``
from ``engagement.triage._CRISIS_RE`` — must turn the gate RED on
``safety_recall_must_escalate``; restoring it makes the gate GREEN again. This can
only hold if the predictor calls the real cell. On the old shadow predictor the
patch had no effect (the shadow kept its own copy of the keyword), so this test
fails on the unpatched code and passes only once the adapter is wired.

DB-free: the gate logic runs over a fake store (same pattern as
test_eval_gate_integration), so this proof runs in the done-gate without Postgres.
"""

from __future__ import annotations

import re

import engagement.triage as triage
from evals.gate import run_eval_gate
from evals.predictors import cell_predictor, regressed_cell_predictor
from evals.smoke_gold_set import SMOKE_TENANT, iter_smoke_examples
from kb.schema import Engine, GoldExample, Split
from kb.store import content_hash


def _gold_from_smoke() -> list[GoldExample]:
    return [
        GoldExample(
            id=e.slug, tenant_id=SMOKE_TENANT, engine=e.engine, cell=e.cell,
            input=e.input, expected=e.expected, rubric_dimensions=e.dimensions,
            split=Split.SMOKE, label_version=1, content_hash=content_hash(e.input),
        )
        for e in iter_smoke_examples()
    ]


class _FakeStore:
    def __init__(self, rows: list[GoldExample]) -> None:
        self._rows = rows

    def get_gold_set(self, *, tenant_id, engine, split=None, label_version=None, cell=None):
        eng = engine if isinstance(engine, Engine) else Engine(engine)
        return [
            r for r in self._rows
            if r.tenant_id == tenant_id and r.engine is eng
            and (split is None or r.split == split)
        ]

    def record_metric(self, metric):  # never called (record=False)
        raise AssertionError("record=False in these tests")


def _run(predictor):
    return run_eval_gate(_FakeStore(_gold_from_smoke()), predictor,
                         tenant_id=SMOKE_TENANT, record=False)


def test_predictor_calls_the_real_cell_not_a_shadow():
    """The adapter's output must equal the shipped cell's classification for a
    text only the REAL cell handles (crisis via ``swollen``)."""
    from evals.predictors import predict_triage

    got = predict_triage("is it normal for it to still look this swollen after a week?", "comment")
    assert got == {"triage_class": "crisis", "reply_safety": "must-escalate"}, got


def test_clean_shipped_cell_is_green():
    res = _run(cell_predictor)
    assert res.verdict == "PASS", res.message()
    metrics = {o.metric: o for o in res.outcomes}
    # Both engagement gates were scored on the real cell and passed.
    assert metrics["triage_recall"].passed
    assert metrics["safety_recall_must_escalate"].passed


def test_removing_swollen_from_the_shipped_cell_fails_the_safety_gate(monkeypatch):
    """THE proof: a regression in the shipped ``_CRISIS_RE`` reds the gate on
    ``safety_recall_must_escalate``. Fails on the pre-bead shadow predictor."""
    # Clean baseline: green.
    assert _run(cell_predictor).verdict == "PASS"

    # Break the SHIPPED cell — drop the ``swollen`` crisis token only.
    assert "swollen|" in triage._CRISIS_RE.pattern
    patched = re.compile(triage._CRISIS_RE.pattern.replace("swollen|", ""), re.IGNORECASE)
    monkeypatch.setattr(triage, "_CRISIS_RE", patched)

    res = _run(cell_predictor)
    assert res.verdict == "FAIL", res.message()
    failed = {o.metric for o in res.failures}
    assert "safety_recall_must_escalate" in failed, res.message()


def test_restoring_swollen_is_green_again():
    """Restoring the token (the unpatched module state) is back to GREEN — the
    gate is sensitive to the real cell in both directions, not stuck-red."""
    assert "swollen" in triage._CRISIS_RE.pattern
    assert _run(cell_predictor).verdict == "PASS"


def test_seeded_regression_predictor_still_reds_the_gate():
    """``EVAL_GATE_REGRESS=1``'s predictor (now grounded on the real cell) still
    fails via the safety metric — the build-fail wiring proof is preserved."""
    res = _run(regressed_cell_predictor)
    assert res.verdict == "FAIL", res.message()
    assert "safety_recall_must_escalate" in {o.metric for o in res.failures}
