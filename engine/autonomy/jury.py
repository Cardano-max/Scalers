"""Stub jury producer (OBS-02) — placeholder until the Phase-5 jury (AUTON-01).

The real quality jury is a cross-family panel of judges scoring each action on
voice / safety / appropriateness, calibrated against a gold set, with confidence
pooled (stack-decision.md). That is Phase 5. Until then this module emits
**deterministic placeholder votes** in the exact :class:`~autonomy.decision.JudgeVote`
shape so the persistence layer and the console jury card can be exercised on real
data. When the real jury lands it replaces :func:`stub_jury` — **no schema change**.

The panel mirrors the cross-family roster in stack-decision.md (one Anthropic +
out-of-family jurors), so the cross-family rule is auditable from the records now.

HONEST-TRACE (CustomerAcq-65w.15): a deterministic verdict must NEVER be recorded
under a real model id — a trace must not claim a model deliberated when a Python
expression decided. Stub seat labels are therefore ``stub:<family>-seat`` (the
FAMILY keeps the panel shape auditable; the judge id says plainly that no model
ran), and deterministic verdict paths label their "model" via
:func:`deterministic_model_label` (e.g. ``deterministic:staged-count-gate``) —
never an ``anthropic:*`` id.
"""

from __future__ import annotations

from autonomy.decision import JudgeVote


def deterministic_model_label(gate: str) -> str:
    """The honest 'model' string for a verdict computed by pure code (65w.15).

    Recorded wherever an agent_run/trace row needs a model attribution for a
    deterministic step — e.g. ``deterministic:staged-count-gate`` — so the
    observability trail never shows a model that did not run."""
    return f"deterministic:{gate}"


#: The honest label for the studio provided-leads jury (aggregate = staged-count
#: check, zero model calls). phase3's agui/JURY_MODEL imports this on trunk pull.
DETERMINISTIC_JURY_MODEL = deterministic_model_label("staged-count-gate")

# Cross-family panel labels (stub records only — no model is EVER called here).
# HONEST labels (65w.15): the judge id is an explicit stub marker, never a model
# id (the earlier Opus / Haiku model-id labels claimed a model that never ran).
# The family keeps the cross-family shape auditable.
JURY_PANEL: tuple[tuple[str, str], ...] = (
    ("stub:anthropic-seat", "anthropic"),
    ("stub:openai-seat", "openai"),
    ("stub:google-seat", "google"),
    ("stub:deepseek-seat", "deepseek"),
)


def expected_judge_count() -> int:
    return len(JURY_PANEL)


def stub_jury(
    base_confidence: float,
    *,
    panel: tuple[tuple[str, str], ...] = JURY_PANEL,
) -> list[JudgeVote]:
    """Produce one placeholder vote per cross-family judge.

    Every juror scores all three dimensions at ``base_confidence`` (clamped to
    ``[0, 1]``) — a unanimous, deterministic stand-in derived from the run's
    computed confidence signal, so the same input always yields the same record.
    The Phase-5 jury replaces this with real per-judge, per-dimension model
    scoring; the return shape is identical.
    """
    c = max(0.0, min(1.0, base_confidence))
    return [
        JudgeVote(judge=judge, family=family, voice=c, safety=c, appr=c)
        for judge, family in panel
    ]
