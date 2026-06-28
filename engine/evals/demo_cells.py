"""Deterministic demo predictors for the rvy.9 integration proof.

These stand in for the real cells until rvy.7's Inspect mock-model solvers land.
They are pure functions of the gold example (no model, no keys), so the per-commit
gate is hermetic and the verdict is reproducible.

* :func:`oracle_cell` — predicts the gold label exactly: a CLEAN, behavior-
  preserving change. The gate must go GREEN.
* :func:`regressed_triage_cell` — a deliberately broken engagement triage cell that
  collapses everything to ``positive`` / ``safe-to-auto``. It misses every
  complaint and every ``must-escalate`` (incl. the rvy.10 metric-flip negatives),
  so triage recall, safety recall, and ECE all blow their thresholds. The gate
  must go RED — via the metric, not a crash.
"""

from __future__ import annotations

from typing import Any

from kb.schema import Engine, GoldExample


def oracle_cell(example: GoldExample) -> dict[str, Any]:
    """A perfect cell: return the gold label payload unchanged."""
    return dict(example.expected)


def regressed_triage_cell(example: GoldExample) -> dict[str, Any]:
    """Broken ONLY for engagement triage; oracle elsewhere (isolates the regression)."""
    if example.engine is Engine.ENGAGEMENT:
        return {"triage_class": "positive", "reply_safety": "safe-to-auto"}
    return dict(example.expected)
