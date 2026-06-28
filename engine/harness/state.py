"""Graph state, node protocol, and routing types (systemdesign §6.2 / HARN-01).

``GraphState`` is a Pydantic model carrying ``tenant_id``, ``run_id``, the
working artifact (Research/Assemble outputs), accumulated signals
(``confidence``, ``gates``, ``jury``), and a ``step_log``. Every value that
flows between cells is typed — never raw model text.

A ``Node`` is pure: state in, state out. Code nodes and typed cells share the
type, so the harness composes them without caring which is which.
"""

from __future__ import annotations

import operator
from enum import Enum
from typing import Annotated, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class RouteDecision(str, Enum):
    """What the harness does with a produced action (systemdesign §6.2).

    The values are the contract literals ``"auto" | "review" | "regenerate"``;
    because this is a ``str`` enum, a member *is* that string. The router is the
    only thing that emits these, in pure Python — the LLM never picks the route.
    """

    AUTO = "auto"
    REVIEW = "review"
    REGENERATE = "regenerate"


class AutonomyMode(str, Enum):
    """Per-channel autonomy dial fed into the router.

    ``AUTO`` permits auto actions when confidence clears the bar; ``REVIEW``
    forces a human to sign off on anything that would otherwise auto-fire.
    """

    AUTO = "auto"
    REVIEW = "review"


class Gate(BaseModel):
    """A deterministic gate result (banned phrase, claim, length, voice).

    Produced by eng2's validator bank (systemdesign §6.3); consumed by the
    router. A failed gate is a code-detected fault, not a judgement call.
    """

    model_config = {"frozen": True}

    name: str
    passed: bool
    detail: str | None = None


class JurySignal(BaseModel):
    """A single juror's score (placeholder for the Phase-5 jury)."""

    model_config = {"frozen": True}

    juror: str
    score: float


class Decision(BaseModel):
    """A human's resume decision for a HITL-paused run (systemdesign §6.2)."""

    model_config = {"frozen": True}

    action: Literal["approve", "edit", "reject"]
    note: str | None = None


class ResearchOutput(BaseModel):
    """Typed output of the Research cell."""

    model_config = {"frozen": True}

    topic: str
    findings: list[str] = Field(default_factory=list)


class AssembleOutput(BaseModel):
    """Typed output of the Assemble cell — the working artifact."""

    model_config = {"frozen": True}

    topic: str
    draft: str


def _last_value(_existing: list, new: list) -> list:
    """Reducer: replace the channel with the latest write (non-accumulating)."""

    return new


class GraphState(BaseModel):
    """The state threaded through the fixed topology (systemdesign §6.2).

    ``step_log`` uses an append reducer (it is the run's trajectory). ``gates``
    and ``jury`` feed the router, so they are **last-value** — the router must
    see the current run's signal set, never an accumulation. (Cross-run
    accumulation on a reused ``run_id`` is separately prevented by the replay
    guard in ``CompiledGraph.run`` — CustomerAcq-fk5.)
    """

    # Extra keys (e.g. LangGraph's ``__interrupt__`` marker) are ignored on
    # validation so ``CompiledGraph.run``/``resume`` can coerce raw results.
    model_config = {"extra": "ignore"}

    tenant_id: str
    run_id: str
    topic: str

    research: ResearchOutput | None = None
    assembled: AssembleOutput | None = None

    confidence: float | None = None
    gates: Annotated[list[Gate], _last_value] = Field(default_factory=list)
    jury: Annotated[list[JurySignal], _last_value] = Field(default_factory=list)
    step_log: Annotated[list[str], operator.add] = Field(default_factory=list)

    decision: str | None = None


@runtime_checkable
class Node(Protocol):
    """A pure graph node: ``state`` in, ``state`` update out (systemdesign §6.2).

    Concrete nodes return a partial mapping of channel updates that the harness
    applies to ``GraphState`` via its reducers.
    """

    name: str

    async def __call__(self, state: GraphState) -> GraphState: ...
