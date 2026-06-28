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

from .state import AssembleOutput, GraphState, ResearchOutput

_T = TypeVar("_T", bound=BaseModel)


class CellError(RuntimeError):
    """Raised when a cell's output fails schema validation."""


def typed_cell(schema: type[_T], payload: object) -> _T:
    """Validate ``payload`` against ``schema`` or raise :class:`CellError`.

    The parser-repair boundary: no raw, unvalidated output flows downstream.
    """

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
