"""Bounded graph cells: Research and Assemble (Node protocol / HARN-01).

The cells are deterministic in this Phase-1 skeleton: typed state in, typed
state out, no live model call yet. That keeps the demo run reproducible and
gives eng2's LLM-backed typed cell (HARN-02) a clean seam — replace the body of
``ResearchNode`` / ``AssembleNode`` with a typed Pydantic-AI cell at
temperature 0 and the topology, routing, and tests around it stay unchanged.

``typed_cell`` is that seam: it validates whatever a node produced against a
Pydantic schema so a malformed result fails on a code path instead of flowing
downstream as raw text.
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel, ValidationError

from cells.base import Cell
from cells.ideate import AngleSet, build_ideate_prompt
from cells.select_angle import NoViableAngleError, select_angle

from .spans import span
from .state import AssembleOutput, GraphState, ResearchOutput

_T = TypeVar("_T", bound=BaseModel)


class CellError(RuntimeError):
    """Raised when a cell's output fails schema validation."""


def typed_cell(schema: type[_T], payload: object) -> _T:
    """Validate ``payload`` against ``schema`` or raise :class:`CellError`.

    The parser-repair boundary: no raw, unvalidated output flows downstream.
    Emits a nested ``cell`` span (OBS-01) under the current node span.
    """

    with span(f"cell:{schema.__name__}", kind="cell"):
        try:
            return schema.model_validate(payload)
        except ValidationError as exc:
            raise CellError(f"{schema.__name__} validation failed: {exc}") from exc


def _confidence_for(findings: list[str]) -> float:
    """Deterministic confidence from the number of grounded findings.

    A placeholder for the Phase-5 self-consistency confidence computer; kept
    pure and monotonic so the router has a concrete signal to act on.
    """

    return min(1.0, 0.6 + 0.1 * len(findings))


class ResearchNode:
    """Research cell: gather grounded findings for the topic."""

    name = "research"

    async def __call__(self, state: GraphState) -> GraphState:
        findings = [f"grounded insight about {state.topic} #{i}" for i in range(1, 4)]
        research = typed_cell(
            ResearchOutput, {"topic": state.topic, "findings": findings}
        )
        # Returns GraphState channel updates (applied via the harness reducers).
        return {"research": research, "step_log": ["research"]}  # type: ignore[return-value]


class AssembleNode:
    """Assemble cell: turn findings into a draft and a confidence score."""

    name = "assemble"

    async def __call__(self, state: GraphState) -> GraphState:
        research = state.research
        if research is None:
            raise CellError("assemble ran before research produced findings")
        draft = f"# {research.topic}\n" + "\n".join(
            f"- {finding}" for finding in research.findings
        )
        assembled = typed_cell(
            AssembleOutput, {"topic": research.topic, "draft": draft}
        )
        return {  # type: ignore[return-value]
            "assembled": assembled,
            "confidence": _confidence_for(research.findings),
            "step_log": ["assemble"],
        }


class IdeateNode:
    """Ideate cell node (a9m.4): research_result -> candidate angles (AngleSet).

    Holds a typed Ideate ``Cell`` (temp-0, pinned model; tests inject a scripted
    model). Assembles the grounding prompt from ``state.research_result`` + optional
    practitioner-wisdom snippets and records the candidate angles to the trajectory.
    """

    name = "ideate"

    def __init__(
        self,
        cell: Cell[AngleSet],
        *,
        wisdom_fn=None,  # optional: (state) -> tuple[str, ...] from the 1mk.9 KB
    ) -> None:
        self._cell = cell
        self._wisdom_fn = wisdom_fn

    async def __call__(self, state: GraphState) -> GraphState:
        research = state.research_result
        if research is None:
            raise CellError("ideate ran before a research result was produced")
        wisdom = tuple(self._wisdom_fn(state)) if self._wisdom_fn else ()
        prompt, _low = build_ideate_prompt(research, topic=state.topic, wisdom=wisdom)
        with span("cell:ideate", kind="cell"):
            angle_set = await self._cell.run(prompt)
        return {  # type: ignore[return-value]
            "angles": angle_set,
            "step_log": [f"ideate:{len(angle_set.angles)}_candidates"],
        }


class SelectAngleNode:
    """SelectAngle node (a9m.4): pure-code deterministic pick of one angle.

    The model proposes (IdeateNode); code decides here. On no viable candidate it
    routes to review (regenerate) instead of crashing — the harness recovery layer
    never sees a raw error.
    """

    name = "select_angle"

    async def __call__(self, state: GraphState) -> GraphState:
        if state.angles is None:
            raise CellError("select_angle ran before ideate produced candidates")
        research = state.research_result
        low_grounding = research is None or not research.items or research.over_budget
        try:
            selection = select_angle(
                state.angles,
                research,
                low_grounding=low_grounding,
            )
        except NoViableAngleError as exc:
            # Defined abort path: route to human review, no crash.
            return {  # type: ignore[return-value]
                "decision": "review",
                "step_log": [f"select_angle:no_viable_angle({exc})->review"],
            }
        flag = "low_grounding" if selection.low_grounding else f"score={selection.score:.2f}"
        return {  # type: ignore[return-value]
            "angle": selection,
            "step_log": [f"select_angle:{flag}"],
        }
