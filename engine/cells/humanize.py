"""Humanize voice-QA rewrite cell (skill: human-tone, CustomerAcq-1mk.3, form b).

The deterministic :mod:`cells.ai_flagger` validator *flags* AI tells; this cell
*fixes* them. It is a typed :class:`~cells.base.Cell` that rewrites a flagged
draft toward natural human tone while preserving intent and approved claims. Its
own output is re-checked by the AI-flagger (an ERROR validator in its bank), so a
rewrite that still reads as AI slop is repaired or fails on a code path — the
human-tone bar is enforced on the rewrite itself, not just the input.

Pipeline ordering: run the deterministic flagger first (cheap, no model); only
route drafts it flags through this rewrite (a model call). The rewrite must never
introduce new claims or drop approved ones.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from cells.ai_flagger import FlaggerConfig, ai_flagger
from cells.base import Cell
from cells.validators import (
    FieldValidator,
    Severity,
    ValidationIssue,
    ValidatorBank,
    _get,
    non_empty,
)


class HumanizedDraft(BaseModel):
    """A rewritten draft in human tone."""

    text: str = Field(description="The rewritten draft, free of AI tells.")
    revised: bool = Field(description="Whether the rewrite changed the input.")


def claims_preserved(field_name: str, claims: tuple[str, ...]) -> FieldValidator:
    """Every approved claim must still appear in the rewrite (case-insensitive).

    Guards the edge case where a humanizing rewrite quietly drops or alters an
    approved claim. A dropped claim is an ERROR, so it triggers a repair retry.
    """

    def _fn(value):
        text = _get(value, field_name)
        if not isinstance(text, str):
            return []
        low = text.lower()
        return [
            ValidationIssue(
                "claims_preserved",
                Severity.ERROR,
                f"approved claim dropped from rewrite: {claim!r}",
            )
            for claim in claims
            if claim.lower() not in low
        ]

    return FieldValidator("claims_preserved", _fn)


_INSTRUCTIONS = (
    "You are a voice-QA editor. Rewrite the given draft so it reads as written by "
    "a real person, removing AI tells: em-dashes, contrast framing ('it's not X, "
    "it's Y'), the rhetorical rule-of-three, and generic transitions ('Moreover', "
    "'In conclusion'). Preserve the meaning and every approved claim exactly; do "
    "NOT add new claims, facts, or numbers. Keep it concise and concrete. Set "
    "'revised' to true if you changed anything."
)


def build_humanize_cell(
    *,
    approved_claims: tuple[str, ...] = (),
    config: FlaggerConfig = FlaggerConfig(),
    **overrides,
) -> Cell[HumanizedDraft]:
    """Build the humanize rewrite cell.

    The cell's validator bank enforces the human-tone bar on its OWN output (the
    AI-flagger) plus claim preservation, so the rewrite is repaired until it
    clears them. ``overrides`` pass through to :class:`~cells.base.Cell` (e.g.
    ``model`` for tests, or a cheaper model for routing).
    """
    validators = ValidatorBank(
        validators=(
            non_empty("text"),
            ai_flagger("text", config),
            claims_preserved("text", approved_claims),
        )
    )
    params = dict(
        name="humanize",
        schema=HumanizedDraft,
        instructions=_INSTRUCTIONS,
        validators=validators,
    )
    params.update(overrides)
    return Cell(**params)
