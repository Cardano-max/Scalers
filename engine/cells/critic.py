"""Critic cell — a REAL, independent critique pass over one produced asset.

The critic is the team's quality gate *before* an asset is queued for human
review. It is **one independent model call per asset**, not a staged debate: the
cell is handed ONLY the artifact text (plus the campaign objective and any hard
constraints) and must judge what is actually in front of it. It never sees the
author cell's chain-of-thought, never role-plays a back-and-forth, and never
"defends then attacks" — that theatre produces agreeable mush. One pass, one
independent verdict, grounded in the artifact.

Design mirrors :mod:`cells.content_brief`:

* a typed output schema (:class:`AssetCritique`) so a verdict can never leave the
  cell as raw prose,
* a deterministic :class:`~cells.validators.ValidatorBank` that enforces the
  critique is *substantive* (a real rationale, and a non-approve verdict must name
  at least one concrete issue — "revise" with no reason is not allowed), and
* :func:`build_critic_cell` pinning a real model at ``temperature=0``.

Independence note: for true cross-family independence (the same principle the
autonomy jury uses) the operator may route this cell to a *different* model
family than the author cell via the ``model`` override. The default pin below is
a real, single-family pin; it does not fake a second voice.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from cells.base import DEFAULT_MODEL, Cell
from cells.validators import (
    FieldValidator,
    Severity,
    ValidationIssue,
    ValidatorBank,
    no_placeholder,
    non_empty,
    word_count_between,
)

# Pinned, real model id for the critic (HARN-06). The critic runs at temp 0 like
# every other cell. Kept as a named constant so the pin is explicit and auditable.
CRITIC_MODEL = DEFAULT_MODEL


class Verdict(str, Enum):
    """The critic's independent disposition for one asset."""

    APPROVE = "approve"   # ship-quality as-is; route to the human review queue
    REVISE = "revise"     # fixable problems; name them so the author can repair
    REJECT = "reject"     # off-brief / unsafe-claim / unusable; do not queue


class Severity_(str, Enum):
    """How badly one issue hurts the asset."""

    MINOR = "minor"       # nit; would not block on its own
    MAJOR = "major"       # should be fixed before a human sees it
    BLOCKING = "blocking"  # makes the asset unusable / unsafe to queue


class CritiqueIssue(BaseModel):
    """One concrete, located problem the critic found in the asset."""

    dimension: str = Field(
        description="What the issue is about (e.g. hook_strength, brand_fit, "
        "clarity, claim_safety, length, cta)."
    )
    severity: Severity_ = Field(description="How badly this issue hurts the asset.")
    note: str = Field(description="The specific problem, quoting/pointing at the asset.")


class AssetCritique(BaseModel):
    """An independent critique of one asset."""

    verdict: Verdict = Field(description="The overall independent disposition.")
    rationale: str = Field(
        description="Why this verdict — grounded in the actual asset, not generic praise."
    )
    issues: list[CritiqueIssue] = Field(
        default_factory=list,
        description="Concrete problems found. Empty only when verdict is approve.",
    )
    suggested_fixes: list[str] = Field(
        default_factory=list,
        description="Actionable fixes the author can apply (only when revise).",
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="How sure the critic is of this verdict, 0..1.",
    )


# --------------------------------------------------------------------------- #
# Validators — enforce the critique is SUBSTANTIVE and INTERNALLY COHERENT.
# A critic that says "revise" or "reject" but names no issue is not a real pass;
# that is the exact failure the honesty gate forbids. We make it a code-path ERROR.
# --------------------------------------------------------------------------- #

_RATIONALE_MIN_WORDS = 8   # a one-liner "looks good" is not a critique
_RATIONALE_MAX_WORDS = 200


def verdict_requires_issue() -> FieldValidator:
    """A non-``approve`` verdict MUST name at least one concrete issue.

    This is what keeps the critic honest: it cannot wave an asset to "revise"
    without saying what is wrong. Conversely an ``approve`` with a ``blocking``
    issue is incoherent and also fails here.
    """

    def _fn(value) -> list[ValidationIssue]:
        verdict = getattr(value, "verdict", None)
        issues = getattr(value, "issues", None) or []
        out: list[ValidationIssue] = []
        if verdict in (Verdict.REVISE, Verdict.REJECT) and len(issues) == 0:
            out.append(ValidationIssue(
                "verdict_requires_issue", Severity.ERROR,
                f"verdict {getattr(verdict, 'value', verdict)!r} must name at least "
                "one concrete issue — a non-approve verdict with no reason is not a critique",
            ))
        if verdict is Verdict.APPROVE:
            blocking = [i for i in issues if getattr(i, "severity", None) is Severity_.BLOCKING]
            if blocking:
                out.append(ValidationIssue(
                    "verdict_requires_issue", Severity.ERROR,
                    "verdict 'approve' is incoherent with a blocking issue present",
                ))
        return out

    return FieldValidator("verdict_requires_issue", _fn)


def issues_filled() -> FieldValidator:
    """Every named issue must have a non-empty, placeholder-free note."""

    ne = non_empty("note")
    npl = no_placeholder("note")

    def _fn(value) -> list[ValidationIssue]:
        out: list[ValidationIssue] = []
        for i, issue in enumerate(getattr(value, "issues", None) or []):
            for r in (ne.check(issue).issues + npl.check(issue).issues):
                out.append(ValidationIssue(r.validator, r.severity, f"issues[{i}] {r.message}"))
        return out

    return FieldValidator("issues_filled", _fn)


def critic_validators() -> ValidatorBank:
    """The deterministic gates a critique must clear before it is trusted."""
    return ValidatorBank(validators=(
        non_empty("rationale"),
        no_placeholder("rationale"),
        word_count_between("rationale", _RATIONALE_MIN_WORDS, _RATIONALE_MAX_WORDS),
        verdict_requires_issue(),
        issues_filled(),
    ))


_INSTRUCTIONS = (
    "You are an independent senior creative critic reviewing ONE marketing asset "
    "before it goes to a human for approval. You did NOT write it and you have no "
    "stake in it.\n"
    "Hard rules:\n"
    "- Judge ONLY the artifact in front of you and the stated objective/constraints. "
    "Do NOT assume the author's intent, do NOT stage a debate, do NOT argue with "
    "yourself. One pass, one honest verdict.\n"
    "- Be specific and grounded: every problem must point at something actually in "
    "the asset (quote it). No generic praise, no generic complaints.\n"
    "- Verdict is one of: approve (ship-quality as-is), revise (fixable — you MUST "
    "name the concrete issues and give actionable fixes), reject (off-brief, unsafe "
    "claim, or unusable).\n"
    "- If you say revise or reject you MUST list at least one concrete issue. A "
    "verdict with no reason is not allowed.\n"
    "- Flag any factual/medical/efficacy claim that is not obviously supportable as "
    "a claim_safety issue. Brand-voice mismatch and weak hooks are real issues too.\n"
    "- Set confidence honestly (0..1). Low confidence is fine; pretending is not."
)


def build_critic_cell(*, model: str = CRITIC_MODEL, **overrides) -> Cell[AssetCritique]:
    """Build the critic cell — one independent, pinned, temp-0 critique pass.

    Run it with a prompt that contains the campaign objective, any hard
    constraints/approved-claims, and the asset text to judge. Route it to a
    different model family via ``model=`` for cross-family independence.
    """
    params = dict(
        name="critic",
        schema=AssetCritique,
        instructions=_INSTRUCTIONS,
        validators=critic_validators(),
        model=model,
    )
    params.update(overrides)
    return Cell(**params)
