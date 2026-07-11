"""Brief -> archetype classifier (§3.2) — the model's ONLY structural output.

A small, fast, temperature-0 cell (Haiku 4.5) that maps an operator BRIEF to a
REGISTERED archetype id. The output type embeds the dynamic :data:`registry.ArchetypeId`
Enum, so the model's answer is parse-validated against the registry: it can pick a
registered route, it can NEVER invent one (an unregistered string fails schema
validation and triggers a bounded repair, never a fabricated type).

This is the whole "model picks a label, not a topology" contract: the classifier
emits a bounded Enum; ``route_archetype`` turns that label into a route among fixed,
pre-declared nodes. The model never touches graph shape.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from archetypes import registry
from archetypes.registry import ArchetypeId
from cells.base import Cell

# Pinned classifier model (operator-named): Haiku 4.5, cheap + fast for a label.
CLASSIFY_MODEL = "anthropic:claude-haiku-4-5-20251001"


class ArchetypeChoice(BaseModel):
    """The classifier's typed output: a REGISTERED id + a short why.

    ``archetype_id`` is the dynamic Enum of registered ids — pydantic rejects any
    value that is not a current registry key, so the model cannot return an
    unregistered/invented type.
    """

    archetype_id: ArchetypeId = Field(  # type: ignore[valid-type]
        description="The single best-fitting registered campaign archetype id."
    )
    rationale: str = Field(
        description="One concrete sentence: why this archetype fits the brief."
    )

    @property
    def id(self) -> str:
        """The plain string id (Enum value)."""
        v = self.archetype_id
        return v.value if hasattr(v, "value") else str(v)


def _instructions(descriptor: str) -> str:
    """Render the system instructions, enumerating ONLY the registered types so the
    model is anchored to the live registry (no stale hard-coded menu).

    ``descriptor`` is the REQUIRED, honest account-identity line from
    :func:`config.loader.describe_tenant` — it replaces the old hardcoded
    ``"a women-led tattoo studio"`` literal so the router is never anchored to a
    fabricated identity. Callers resolve it with ``describe_tenant(tenant_id)``.
    """
    menu_lines = []
    for spec in registry.REGISTRY.values():
        menu_lines.append(
            f"  - {spec.id}: trigger={spec.trigger.value}, channels="
            f"{', '.join(c.value for c in spec.channels)}; goal={spec.success_metric}"
        )
    menu = "\n".join(menu_lines)
    return (
        f"You are a marketing-campaign router for {descriptor}. Given an "
        "operator's free-text campaign brief, choose the SINGLE best-fitting campaign "
        "archetype from the fixed library below. You MUST pick exactly one of these "
        "registered ids — you may not invent a new type, and you may not return a type "
        "that is not listed.\n\n"
        f"Registered archetypes:\n{menu}\n\n"
        "Guidance: a new artist / new style capability to promote -> artist_spotlight; "
        "a calendar date / holiday / observance tie-in -> holiday; bringing back lapsed "
        "or inactive past customers -> win_back; an explicit Facebook page post / "
        "Facebook campaign ask -> facebook_post. Choose by the PRIMARY intent of the "
        "brief. Give one concrete sentence of rationale grounded in the brief's words."
    )


def build_classifier_cell(
    descriptor: str, *, model: str = CLASSIFY_MODEL, **overrides
) -> Cell[ArchetypeChoice]:
    """Build the brief->archetype classifier cell (Haiku 4.5, temp 0).

    ``descriptor`` is the REQUIRED, honest account-identity line from
    :func:`config.loader.describe_tenant` (never a fabricated niche).

    Run with ``cell.run_sync(brief_text)``. The returned :class:`ArchetypeChoice`
    is guaranteed to carry a REGISTERED id (schema-validated)."""
    params = dict(
        name="archetype_classify",
        schema=ArchetypeChoice,
        instructions=_instructions(descriptor),
        model=model,
    )
    params.update(overrides)
    return Cell(**params)


def classify_brief(brief: str, descriptor: str, *, model: str = CLASSIFY_MODEL) -> ArchetypeChoice:
    """Classify one brief -> a registered :class:`ArchetypeChoice` (real model call).

    ``descriptor`` is the REQUIRED honest account-identity from
    :func:`config.loader.describe_tenant` (never a fabricated niche)."""
    return build_classifier_cell(descriptor, model=model).run_sync(brief)
