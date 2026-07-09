"""Role registry for the autonomous marketing TEAM (P1).

One place that names every role in the team and points each at the cell (or
package) that implements it. The orchestrator (``team.orchestrator``) reads this
registry to decide which builder to call for a given step — it never hard-codes a
cell import.

Honesty about provenance (this matters — see the repo skill/honesty gate):

* Some roles are **NEW** real cells added on this branch (``funnel_architect``,
  ``critic``).
* Some roles are **EXISTING** real cells/packages already on the base demo branch
  (``strategist`` = content-brief, ``copywriter``, ``draft``, ``outreach``).
  This registry REFERENCES them; it does not redefine or fake them.
* Some roles are **P0 cells that live on another branch** (``researcher`` and the
  canonical ``strategist``/``draft`` spine). Where the base branch already carries
  a usable implementation we point at it; where it does not, the builder is
  ``None`` and the spec carries an explicit integration TODO. Calling ``build()``
  for such a role raises ``RoleNotWired`` with that TODO — it never returns a stub.

No row here invents capability. A role with ``builder=None`` is honestly "not
wired on this branch yet", not a silent no-op.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from cells.base import Cell
# NEW cells (this branch):
from cells.critic import CRITIC_MODEL, build_critic_cell
from cells.funnel_architect import FUNNEL_MODEL, build_funnel_architect_cell
# EXISTING cells already on the base branch (referenced, NOT redefined):
from cells.base import DEFAULT_MODEL
from cells.content_brief import build_content_brief_cell
from cells.copywriter import build_copywriter_cell, build_copywriter_email_cell


class RoleNotWired(NotImplementedError):
    """Raised when ``build()`` is called for a role whose builder is not wired on
    this branch. Carries the integration TODO so the caller sees exactly what is
    missing — used instead of returning a fake/stub cell."""


class Provenance(str, Enum):
    """Where a role's implementation comes from (audit honesty)."""

    NEW = "new"                       # added on this branch, real cell
    EXISTING = "existing"             # real cell/package already on the base branch
    P0_OTHER_BRANCH = "p0-other-branch"  # canonical P0 cell lives on another branch


class Role(str, Enum):
    """The roles in the production marketing team, in pipeline order."""

    RESEARCHER = "researcher"
    STRATEGIST = "strategist"
    FUNNEL_ARCHITECT = "funnel_architect"
    COPYWRITER = "copywriter"
    DRAFT = "draft"
    OUTREACH = "outreach"
    CRITIC = "critic"


@dataclass(frozen=True)
class RoleSpec:
    """How one role is implemented.

    ``builder`` is a callable returning a :class:`~cells.base.Cell` (or ``None``
    when not wired on this branch). ``needs`` lists required kwargs the builder
    must be given (e.g. the draft cell needs grounding + platform), so the
    orchestrator knows when it must assemble context before it can build.
    """

    role: Role
    cell_name: str
    provenance: Provenance
    model: str
    builder: Optional[Callable[..., Cell]] = None
    needs: tuple[str, ...] = ()
    note: str = ""

    @property
    def wired(self) -> bool:
        """True if this role can be built on this branch (has a builder + no
        unsatisfied required context that the registry cannot supply)."""
        return self.builder is not None


ROLE_REGISTRY: dict[Role, RoleSpec] = {
    # ---- existing P0 spine (do NOT redefine here) ------------------------- #
    Role.RESEARCHER: RoleSpec(
        role=Role.RESEARCHER,
        cell_name="research-pipeline",
        provenance=Provenance.P0_OTHER_BRANCH,
        model=DEFAULT_MODEL,
        builder=None,
        note=(
            "INTEGRATION TODO: researcher is the P0 research pipeline "
            "(engine/research/: router.py + adapter.py + providers/, plus the "
            "research-grounding cells on the P0 branch). It is NOT a single "
            "build_*_cell() and is not re-implemented here. Wire the orchestrator's "
            "research node to research.router/adapter once the P0 branch merges."
        ),
    ),
    Role.STRATEGIST: RoleSpec(
        role=Role.STRATEGIST,
        cell_name="content_brief",
        provenance=Provenance.EXISTING,
        model=DEFAULT_MODEL,
        builder=build_content_brief_cell,
        note=(
            "Strategist == the content-brief cell (cells/content_brief.py) already "
            "on the base branch. The canonical P0 strategist may supersede this; "
            "INTEGRATION TODO: confirm which is authoritative when the P0 branch merges."
        ),
    ),
    Role.DRAFT: RoleSpec(
        role=Role.DRAFT,
        cell_name="draft",
        provenance=Provenance.EXISTING,
        model=DEFAULT_MODEL,
        builder=None,  # build_draft_cell exists but REQUIRES grounding + platform
        needs=("grounding", "platform"),
        note=(
            "Draft == cells/draft.py build_draft_cell, which REQUIRES a VoiceGrounding "
            "and a Platform (brand-voice context assembled per tenant). The registry "
            "cannot synthesize those, so builder is left None here. "
            "INTEGRATION TODO: orchestrator assembles grounding+platform then calls "
            "cells.draft.build_draft_cell(grounding=..., platform=...)."
        ),
    ),
    Role.COPYWRITER: RoleSpec(
        role=Role.COPYWRITER,
        cell_name="copywriter",
        provenance=Provenance.EXISTING,
        model=DEFAULT_MODEL,
        builder=build_copywriter_cell,
        note=(
            "Copywriter == cells/copywriter.py build_copywriter_cell (already on the "
            "base branch). Referenced, not redefined. brand_voice_context/"
            "approved_claims are assembled per tenant at run start."
        ),
    ),
    Role.OUTREACH: RoleSpec(
        role=Role.OUTREACH,
        cell_name="copywriter_email",
        provenance=Provenance.EXISTING,
        model=DEFAULT_MODEL,
        builder=build_copywriter_email_cell,
        note=(
            "Outreach copy == cells/copywriter.py build_copywriter_email_cell (cold "
            "email {subject, body}). The outreach SEQUENCE/policy is the deterministic "
            "engine/outreach/ package (growth-owned, suppression-first, NEVER auto-send). "
            "INTEGRATION TODO: orchestrator composes outreach.OutreachPolicy to build the "
            "per-touch briefs, then this cell fills each touch's copy."
        ),
    ),
    # ---- NEW cells on this branch ---------------------------------------- #
    Role.FUNNEL_ARCHITECT: RoleSpec(
        role=Role.FUNNEL_ARCHITECT,
        cell_name="funnel_architect",
        provenance=Provenance.NEW,
        model=FUNNEL_MODEL,
        builder=build_funnel_architect_cell,
        note="NEW (this branch): designs the campaign asset plan / conversion funnel.",
    ),
    Role.CRITIC: RoleSpec(
        role=Role.CRITIC,
        cell_name="critic",
        provenance=Provenance.NEW,
        model=CRITIC_MODEL,
        builder=build_critic_cell,
        note=(
            "NEW (this branch): independent, single-pass critique of one asset. "
            "Never a staged debate. Route to a different model family via model= "
            "for cross-family independence."
        ),
    ),
}


def get_spec(role: Role) -> RoleSpec:
    """The :class:`RoleSpec` for a role."""
    return ROLE_REGISTRY[role]


def build(role: Role, **kwargs) -> Cell:
    """Build the cell for ``role``.

    Raises :class:`RoleNotWired` (with the spec's integration TODO) for roles whose
    builder is ``None`` on this branch — it never returns a stub. Raises
    ``TypeError`` if a required context kwarg (``spec.needs``) is missing.
    """
    spec = get_spec(role)
    if spec.builder is None:
        raise RoleNotWired(f"role {role.value!r} is not wired on this branch. {spec.note}")
    missing = [n for n in spec.needs if n not in kwargs]
    if missing:
        raise TypeError(
            f"role {role.value!r} needs context kwargs {missing} before it can be built "
            f"(see registry note: {spec.note})"
        )
    return spec.builder(**kwargs)


# The intended team execution order (mirrors the orchestrator's graph edges).
PIPELINE_ORDER: tuple[Role, ...] = (
    Role.RESEARCHER,
    Role.STRATEGIST,
    Role.FUNNEL_ARCHITECT,
    Role.COPYWRITER,
    Role.DRAFT,
    Role.CRITIC,
)
