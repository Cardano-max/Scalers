"""Funnel-architect cell — designs the campaign's asset plan / conversion funnel.

Before the copywriter and draft cells produce anything, the funnel architect
decides *what assets to make and why*: it maps the campaign objective to a small,
coherent set of assets across the funnel stages (awareness -> consideration ->
conversion -> retention), each pinned to a channel, an asset type, and the job it
does. Its output is the brief the rest of the team executes against, so every
field is machine-checkable.

Mirrors :mod:`cells.content_brief`: a typed schema (:class:`FunnelPlan`), a
deterministic :class:`~cells.validators.ValidatorBank` (the plan must be filled,
non-trivial, and actually have a conversion step — you cannot run a funnel that
never asks for the conversion), and :func:`build_funnel_architect_cell` pinning a
real model at ``temperature=0``.

This cell plans structure; it does NOT write final copy (that is the copywriter)
and it does NOT send anything.
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
)

# Pinned, real model id (HARN-06); temp 0 like every cell.
FUNNEL_MODEL = DEFAULT_MODEL


class FunnelStage(str, Enum):
    """Where in the funnel an asset does its work."""

    AWARENESS = "awareness"
    CONSIDERATION = "consideration"
    CONVERSION = "conversion"
    RETENTION = "retention"


class Channel(str, Enum):
    """Where the asset runs (a planning channel, broader than one platform)."""

    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    EMAIL = "email"


class PlannedAsset(BaseModel):
    """One asset the team should produce, with the job it does in the funnel."""

    stage: FunnelStage = Field(description="The funnel stage this asset serves.")
    channel: Channel = Field(description="Where this asset runs.")
    asset_type: str = Field(
        description="Concrete format (e.g. reel, carousel, story, single-image, "
        "cold-email, value-email)."
    )
    purpose: str = Field(description="The specific job this asset does for the objective.")
    success_signal: str = Field(
        description="The observable signal that this asset worked (e.g. saves, "
        "profile visits, replies, bookings)."
    )


class FunnelPlan(BaseModel):
    """The structured asset plan for one campaign."""

    objective: str = Field(description="The one campaign objective this funnel serves.")
    audience: str = Field(description="Who the funnel is for, in one concrete sentence.")
    primary_conversion: str = Field(
        description="The single conversion the funnel is built to drive (e.g. 'book a "
        "consult')."
    )
    assets: list[PlannedAsset] = Field(
        description="2-6 coherent assets spanning the funnel; must include a conversion step.",
    )


# --------------------------------------------------------------------------- #
# Validators — the plan must be filled, non-trivial, and actually drive a
# conversion. A funnel with no conversion-stage asset is not a funnel.
# --------------------------------------------------------------------------- #

_MIN_ASSETS = 2
_MAX_ASSETS = 6
_ASSET_TEXT_FIELDS = ("asset_type", "purpose", "success_signal")


def assets_count(minimum: int = _MIN_ASSETS, maximum: int = _MAX_ASSETS) -> FieldValidator:
    """Need a real plan (>= minimum); beyond maximum is a WARN (too sprawling)."""

    def _fn(value) -> list[ValidationIssue]:
        n = len(getattr(value, "assets", None) or [])
        if n < minimum:
            return [ValidationIssue("assets_count", Severity.ERROR,
                                    f"{n} asset(s); a funnel needs at least {minimum}")]
        if n > maximum:
            return [ValidationIssue("assets_count", Severity.WARN,
                                    f"{n} asset(s); keep it to <= {maximum} for a focused campaign")]
        return []

    return FieldValidator("assets_count", _fn)


def assets_filled() -> FieldValidator:
    """Every asset's text fields must be non-empty and placeholder-free."""

    ne = {f: non_empty(f) for f in _ASSET_TEXT_FIELDS}
    npl = {f: no_placeholder(f) for f in _ASSET_TEXT_FIELDS}

    def _fn(value) -> list[ValidationIssue]:
        out: list[ValidationIssue] = []
        for i, asset in enumerate(getattr(value, "assets", None) or []):
            for f in _ASSET_TEXT_FIELDS:
                for r in (ne[f].check(asset).issues + npl[f].check(asset).issues):
                    out.append(ValidationIssue(r.validator, r.severity, f"assets[{i}] {r.message}"))
        return out

    return FieldValidator("assets_filled", _fn)


def has_conversion_step() -> FieldValidator:
    """At least one asset must sit at the conversion stage (ERROR otherwise)."""

    def _fn(value) -> list[ValidationIssue]:
        stages = {getattr(a, "stage", None) for a in (getattr(value, "assets", None) or [])}
        if FunnelStage.CONVERSION not in stages:
            return [ValidationIssue(
                "has_conversion_step", Severity.ERROR,
                "no conversion-stage asset — the funnel never asks for the conversion",
            )]
        return []

    return FieldValidator("has_conversion_step", _fn)


def funnel_architect_validators() -> ValidatorBank:
    """The deterministic gates a funnel plan must clear before the team executes it."""
    return ValidatorBank(validators=(
        non_empty("objective"),
        non_empty("audience"),
        non_empty("primary_conversion"),
        no_placeholder("objective"),
        no_placeholder("primary_conversion"),
        assets_count(),
        assets_filled(),
        has_conversion_step(),
    ))


_INSTRUCTIONS = (
    "You are a growth funnel architect for a single tattoo artist. Given a campaign "
    "objective and audience, design a SMALL, COHERENT set of assets (2-6) that move "
    "the audience from awareness to the conversion.\n"
    "Hard rules:\n"
    "- Map each asset to a funnel stage (awareness, consideration, conversion, "
    "retention), a channel (instagram, facebook, email), a concrete asset_type, the "
    "specific purpose it serves, and the observable success_signal.\n"
    "- The plan MUST include at least one conversion-stage asset — name the single "
    "primary_conversion the whole funnel drives.\n"
    "- Keep it focused: do not pad the plan. Each asset must earn its place toward "
    "the objective.\n"
    "- Plan STRUCTURE only. Do not write final captions/email copy here (that is the "
    "copywriter's job). No placeholders."
)


def build_funnel_architect_cell(*, model: str = FUNNEL_MODEL, **overrides) -> Cell[FunnelPlan]:
    """Build the funnel-architect cell — pinned model, temp 0, in-loop validators.

    Run it with a prompt containing the campaign objective + audience (and any
    constraints). Its :class:`FunnelPlan` is the brief the copywriter/draft cells
    then execute, one asset at a time.
    """
    params = dict(
        name="funnel_architect",
        schema=FunnelPlan,
        instructions=_INSTRUCTIONS,
        validators=funnel_architect_validators(),
        model=model,
    )
    params.update(overrides)
    return Cell(**params)
