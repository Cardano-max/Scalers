"""Typed cell: the campaign strategy (P0 make-real / slice 2).

The strategy cell is the strategist's first move: from a campaign brief it produces
a short, concrete strategic plan — the single best **target angle** to lead with,
the brand **positioning**, the **key messages** to land, and the **channel
rationale** — BEFORE any copy is written. That plan is then fed forward into the
draft cell's prompt, so the draft is grounded by a real strategy rather than going
straight from the raw brief.

Like every other writing cell it is a :class:`~cells.base.Cell` with a typed output
schema and a deterministic validator bank, runs at temperature 0 against the pinned
default model, and either returns a validated :class:`CampaignStrategy` or fails on
a code path (raw model text never escapes the cell boundary).

Construct it with :func:`build_strategy_cell`. :func:`build_strategy_prompt` renders
the per-campaign prompt and :func:`render_strategy` renders the typed plan back into
the text the draft cell reads.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from cells.base import Cell
from cells.validators import (
    ValidatorBank,
    max_items,
    no_placeholder,
    non_empty,
    word_count_between,
)


class CampaignStrategy(BaseModel):
    """A short strategic plan for one campaign, produced before any copy."""

    target_angle: str = Field(
        description="The single best strategic angle / hook to lead the campaign with."
    )
    positioning: str = Field(
        description="How the brand is positioned for this campaign — what makes it distinct."
    )
    key_messages: list[str] = Field(
        default_factory=list,
        description="The core messages the campaign must land (each a short phrase).",
    )
    channel_rationale: str = Field(
        description="Why these channels, and how to play the angle to each one."
    )


def strategy_validators() -> ValidatorBank:
    """The deterministic gates a campaign strategy must clear.

    ERROR issues trigger a repair retry; the plan never flows forward to the draft
    until it clears them (or the cell fails on a code path).
    """
    return ValidatorBank(
        validators=(
            non_empty("target_angle"),
            non_empty("positioning"),
            non_empty("channel_rationale"),
            non_empty("key_messages"),  # a plan with no messages is not a plan
            no_placeholder("target_angle"),
            no_placeholder("positioning"),
            no_placeholder("channel_rationale"),
            # A target angle is a sentence, not a single word and not an essay.
            word_count_between("target_angle", 2, 40),
            # Advisory: too many "key" messages dilutes the plan.
            max_items("key_messages", 6),
        )
    )


_INSTRUCTIONS = (
    "You are a senior marketing strategist for a single brand. Given a campaign "
    "brief, produce a short, concrete strategic plan BEFORE any copy is written: "
    "the single best target angle to lead with, the brand positioning, the key "
    "messages to land, and the rationale for the channels. Be specific to this "
    "brand and this campaign — no generic marketing boilerplate, no placeholders. "
    "Keep it tight: this plan is the brief the copywriter will draft from."
)


def build_strategy_prompt(descriptor: str, brief: str, research: str | None = None) -> str:
    """Render the per-campaign prompt the strategy cell runs against.

    Campaign-level context (one strategy per campaign), composed the same way the
    draft prompt is: account/voice context, then the brief, then the task.

    ``descriptor`` is the REQUIRED, honest account-identity line from
    :func:`config.loader.describe_tenant` (e.g. ``"@ink-studio — Ink & Iron Tattoo
    Studio, a Brooklyn fine-line and blackwork tattoo studio"``, or the bare handle
    when no pack is on file). It replaces the old hardcoded ``"a women-led tattoo
    studio"`` literal so a tenant's identity is never fabricated. Callers resolve it
    with ``describe_tenant(tenant_id)``.

    When the upstream research step (slice-3 research agent) produced real findings
    grounded in cited web sources, they are composed in between the brief and the
    task so the strategy is informed by real research — research -> strategy ->
    draft. ``research=None`` (honest-empty research) degrades cleanly: the strategy
    proceeds from the brief alone, never from fabricated research.
    """
    parts = [
        f"Studio/account: {descriptor}.",
        f"Campaign brief: {brief}",
    ]
    if research and research.strip():
        parts.append(
            "Market research (real findings from the research agent, grounded in "
            "cited web sources — use these to inform the angle and messages):\n"
            + research.strip()
        )
    parts.append(
        "Produce the strategic plan for THIS campaign: the single best target angle "
        "to lead with, the brand positioning, the key messages to land, and the "
        "channel rationale. Be concrete and specific to this studio — no generic "
        "marketing boilerplate, no placeholders."
    )
    return "\n".join(parts)


def render_strategy(strategy: CampaignStrategy) -> str:
    """Render the typed plan into the text block the draft cell reads downstream."""
    lines = [
        f"Target angle: {strategy.target_angle.strip()}",
        f"Positioning: {strategy.positioning.strip()}",
        "Key messages:",
        *[f"  - {m.strip()}" for m in strategy.key_messages if m.strip()],
        f"Channel rationale: {strategy.channel_rationale.strip()}",
    ]
    return "\n".join(lines)


def build_strategy_cell(**overrides) -> Cell[CampaignStrategy]:
    """Build the campaign-strategy cell.

    Keyword overrides are passed through to :class:`~cells.base.Cell` (e.g.
    ``model`` or ``temperature``) — useful for tests and for routing the cell to a
    different model. Defaults to the pinned ``anthropic:claude-haiku-4-5``.
    """
    params = dict(
        name="strategy",
        schema=CampaignStrategy,
        instructions=_INSTRUCTIONS,
        validators=strategy_validators(),
    )
    params.update(overrides)
    return Cell(**params)
