"""Stub jury producer (OBS-02) — placeholder until the Phase-5 jury (AUTON-01).

The real quality jury is a cross-family panel of judges scoring each action on
voice / safety / appropriateness, calibrated against a gold set, with confidence
pooled (stack-decision.md). That is Phase 5. Until then this module emits
**deterministic placeholder votes** in the exact :class:`~autonomy.decision.JudgeVote`
shape so the persistence layer and the console jury card can be exercised on real
data. When the real jury lands it replaces :func:`stub_jury` — **no schema change**.

The panel mirrors the cross-family roster in stack-decision.md (one Anthropic +
out-of-family jurors), so the cross-family rule is auditable from the records now.
"""

from __future__ import annotations

from autonomy.decision import JudgeVote

# Cross-family panel labels (stub records only — no model is ever called here).
# The Anthropic seat label is POLICY-PINNED to haiku-4.5 (CustomerAcq-8sk) so
# even stub provenance never claims a bigger model than policy allows.
JURY_PANEL: tuple[tuple[str, str], ...] = (
    ("claude-haiku-4-5", "anthropic"),
    ("gpt-5.5", "openai"),
    ("gemini-3-pro", "google"),
    ("deepseek-v3", "deepseek"),
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
