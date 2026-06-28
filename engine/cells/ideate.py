"""Ideate cell (bead a9m.4 / POST-01a) — research → candidate angles.

The research→strategy half of POST-01. The Ideate cell turns the research result
(a9m.2 `ResearchResult.items`) + brand-voice grounding into a set of candidate
**angles**, each with a rationale — the strategic options a human can sanity-check
and `SelectAngle` (pure code) picks from. Per the Phase-3 ADR, the model
*proposes* the candidates; *code* selects, so the chosen angle is reproducible.

Schemas + the builder are normative per the ADR. The cell is a standard
`Cell[AngleSet]` (temp-0, pinned model, validator bank, typed-or-raise); the only
a9m.4-specific work is the schema, the grounding-prompt assembly (research items +
practitioner-wisdom KB + voice skill), and the validators.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from cells.base import Cell
from cells.skills import Skill, compose_instructions
from cells.validators import (
    ValidatorBank,
    max_items,
    non_empty,
)
from research.content.items import ResearchResult


class MediaKind(str, Enum):
    """The asset kind an angle implies (also used by the copywriter cell, a9m.5)."""

    IMAGE = "image"
    REEL = "reel"
    CAROUSEL = "carousel"
    TEXT = "text"


class Angle(BaseModel):
    """One strategic angle candidate (ADR schema)."""

    model_config = {"frozen": True}

    hook: str = Field(description="The one-sentence strategic angle / hook.")
    rationale: str = Field(description="Why it fits this tenant + the findings.")
    format_hint: MediaKind = Field(description="REEL | IMAGE | CAROUSEL | TEXT — informs media spec.")


class AngleSet(BaseModel):
    """N candidate angles; SelectAngle (pure code) picks one."""

    model_config = {"frozen": True}

    angles: list[Angle] = Field(description="Candidate angles, most-promising first.")


# Cap on candidates — enough variety for a real pick, not a wall of near-dupes.
_MAX_ANGLES = 6


def ideate_validators() -> ValidatorBank:
    """Deterministic gates for an angle set. ERROR issues trigger a repair retry;
    the set never flows downstream until it clears them (or the cell fails)."""
    return ValidatorBank(
        validators=(
            non_empty("angles"),                       # at least one candidate
            max_items("angles", _MAX_ANGLES),          # advisory: not a wall of dupes
            # Per-angle text quality (validators address nested fields by name on
            # each item is not supported, so the model-facing guardrails live in
            # the instructions; the hard per-angle checks run in SelectAngle).
        )
    )


_INSTRUCTIONS = (
    "You are a social-media strategist for a tattoo studio. Given research findings "
    "and brand context, propose 3-5 distinct candidate ANGLES for one organic post. "
    "Each angle needs: a concrete one-sentence hook (no AI boilerplate, no placeholders, "
    "no hashtags), a short rationale tying it to the findings and the artist's voice, "
    "and a format_hint (reel/image/carousel/text). Make the angles genuinely different "
    "from each other — different value propositions, not reworded twins. Ground every "
    "angle in the findings where you can; do not invent fake sources or numbers."
)


def build_ideate_cell(voice: Skill | None = None, **overrides) -> Cell[AngleSet]:
    """Build the Ideate cell, composing the brand-voice skill's instructions.

    ``voice`` is the tenant's brand-voice skill (``pack.voice.skill``); its
    instructions are prepended so the angles start from the artist's actual voice.
    Keyword overrides pass through to :class:`~cells.base.Cell` (e.g. ``model`` for
    tests). Runs at temperature 0 against the pinned default model.
    """
    params = dict(
        name="ideate",
        schema=AngleSet,
        instructions=compose_instructions(_INSTRUCTIONS, voice),
        validators=ideate_validators(),
    )
    params.update(overrides)
    return Cell(**params)


# ── grounding-prompt assembly (research items + KB wisdom -> the cell input) ──

# How many research items / wisdom snippets to put in the prompt (top-scored).
_TOP_RESEARCH = 8
_TOP_WISDOM = 4


def build_ideate_prompt(
    research: ResearchResult,
    *,
    topic: str,
    wisdom: tuple[str, ...] = (),
) -> tuple[str, bool]:
    """Assemble the Ideate cell input from the research result + optional
    practitioner-wisdom snippets (the 1mk.9 KB, retrieved by the caller).

    Returns ``(prompt, low_grounding)``. ``low_grounding`` is True when the
    research is empty / over-budget with no items — the cell then grounds on brand
    context only and the slice flags lower confidence (it never fabricates
    sources). Pure + deterministic (items are pre-sorted by score by the adapter).
    """
    items = list(research.items[:_TOP_RESEARCH])
    low_grounding = not items or research.over_budget and not items

    lines = [f"TOPIC: {topic}", ""]
    if items:
        lines.append("RESEARCH FINDINGS (most relevant first):")
        for it in items:
            tag = f"[{it.kind}/{it.source} score={it.score:.2f}]"
            lines.append(f"- {tag} {it.text}")
    else:
        lines.append(
            "RESEARCH FINDINGS: none available (thin/over-budget) — ground on the "
            "brand voice + topic only; do NOT invent findings."
        )
    if research.degraded:
        lines.append(f"(note: sources degraded: {', '.join(research.degraded)})")

    if wisdom:
        lines.append("")
        lines.append("PRACTITIONER PATTERNS (winning-strategies KB — adapt, don't copy):")
        for w in wisdom[:_TOP_WISDOM]:
            lines.append(f"- {w}")

    lines.append("")
    lines.append("Propose the candidate angles now.")
    return "\n".join(lines), bool(low_grounding)
