"""Real per-commit cell predictors (rvy.7 / CustomerAcq-wwy.2) — no shadow, no oracle.

The CTO directive: the per-commit eval gate must run the ACTUAL customer-facing
cell under a deterministic model, not ``oracle_cell`` (which returns the gold
answer, so a green is meaningless) AND not a keyword classifier *transcribed from
the gold set's own keywords* (a self-copied shadow, which structurally cannot
catch a regression in the shipped cell — CustomerAcq-wwy.2). These predictors read
``example.input`` ONLY — never ``example.expected`` — and delegate to the SHIPPED
cell, so the metric is real and a regression in the real code flips it.

* ``cell_predictor`` is the live prediction source swapped into ``run_eval_gate``
  (the ``Predictor`` seam). Engagement *triage* calls the shipped
  :func:`engagement.triage.classify_comment` and maps its ``(TriageCategory,
  SafetyVerdict)`` to the gold dimensions the gate scores; cells that don't exist
  yet (outreach extraction, posting copywriter — Phase 7 / per-promotion) raise
  :class:`CellNotBuilt`, which the gate treats as SKIP-neutral (never a false fail
  for an unbuilt engine).
* ``regressed_cell_predictor`` is the seeded-regression variant for the
  fails-on-regression proof (``EVAL_GATE_REGRESS=1``): it RUNS the real cell, then
  drops every escalation (a cell that under-escalates), missing every
  must-escalate — so the safety metric, not a crash, fails the build. Grounding it
  on the real cell keeps the proof honest (it is a fault injected into the shipped
  path, not a constant divorced from it).

Determinism: the shipped triage classifier is a pure keyword screen (no model
call, no keys) — its correctness on the smoke set is the cell's job, exactly what
the gate measures. The load-bearing proof: removing ``swollen`` from
``engagement.triage._CRISIS_RE`` reds ``safety_recall_must_escalate``; that only
holds because these predictors call the real cell (see
tests/test_eval_gate_real_cell.py).
"""

from __future__ import annotations

from typing import Any

from autonomy.decision import SafetyVerdict
from engagement.triage import ESCALATE_CATEGORIES, classify_comment
from kb.schema import Engine, GoldExample

from evals.gate import CellNotBuilt


def _map_triage(category, safety) -> dict[str, str]:
    """Map the shipped cell's ``(TriageCategory, SafetyVerdict)`` to the gold
    dimensions. ``escalate`` mirrors :func:`engagement.triage.triage_comment`
    exactly: escalate iff the category always routes to a human OR the safety
    verdict is not a clean PASS."""
    escalate = category in ESCALATE_CATEGORIES or safety is not SafetyVerdict.PASS
    return {
        "triage_class": category.value,
        "reply_safety": "must-escalate" if escalate else "safe-to-auto",
    }


def predict_triage(text: str, channel: str = "") -> dict[str, str]:
    """Classify an engagement comment via the SHIPPED triage cell.

    Thin adapter over :func:`engagement.triage.classify_comment` — the real
    customer-facing 'cell under test'. ``channel`` is accepted for call-site
    symmetry but ignored: the shipped cell classifies on the comment text alone,
    so the gate scores exactly what production does (no channel shadow).
    """
    category, safety = classify_comment(text)
    return _map_triage(category, safety)


def cell_predictor(example: GoldExample) -> dict[str, Any]:
    """Run the real cell under test for ``example`` (deterministic, input-only).

    Raises :class:`CellNotBuilt` for cells that don't exist yet so the gate
    SKIPs them (Phase-7 engines), never false-fails.
    """
    if example.engine is Engine.ENGAGEMENT and example.cell == "triage":
        return predict_triage(example.input["text"], example.input.get("channel", ""))
    raise CellNotBuilt(f"{example.engine.value}.{example.cell} cell not built yet")


def regressed_cell_predictor(example: GoldExample) -> dict[str, Any]:
    """Seeded regression for the fails-on-regression proof (engagement only).

    Runs the SHIPPED cell, then drops the escalation — an under-escalating cell.
    Every ``must-escalate`` gold example is missed, so
    ``safety_recall_must_escalate`` blows its threshold and the build reds via the
    metric (not a crash). Grounded on the real cell so the fault is injected into
    the actual code path, not a fabricated constant.
    """
    if example.engine is Engine.ENGAGEMENT and example.cell == "triage":
        category, _safety = classify_comment(example.input["text"])
        return {"triage_class": category.value, "reply_safety": "safe-to-auto"}
    raise CellNotBuilt(f"{example.engine.value}.{example.cell} cell not built yet")
