"""a9m.9 Phase-3 e2e slice SCAFFOLD (POST-01..02 + routing + mock-publish).

Full slice: trigger -> idea -> angle -> draft -> validate -> score -> route(review)
-> approve -> mock-publish. This is the exit-proof harness for the content engine.

STATUS: scaffold. The stages that exist on main + a9m.5 are WIRED LIVE here
(draft via cells.draft + injected model; validate via the draft validator bank).
The two unlanded stages are TODO STUBS with the exact wire-point to fill the
moment they land:
  * a9m.7 (eng3): score + pure-code route() under HELD (439) + persist POST Action
  * a9m.8 (eng4): MockPublisher behind the side-effect boundary (exactly-once)

`SCENARIOS` (from test_a9m9_content_fixtures) drives the slice; each carries the
expected validator behavior + expected routing (HELD -> review/regenerate, NEVER
auto). The live portion is asserted now; the full approve->publish e2e is skipped
until a9m.7/.8 land (flip the skip + fill the two stubs).
"""

from __future__ import annotations

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from cells.draft import build_draft_cell, persist_draft, render_angle_prompt
from cells.post_schemas import PostDraft
from harness.router import DEFAULT_THRESHOLD, route
from harness.state import AutonomyMode, Gate, GraphState, RouteDecision

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))  # allow same-dir fixture-module import
from test_a9m9_content_fixtures import SCENARIOS, _grounding  # noqa: E402,F401  (fixture reuse)


# ── injected model: returns a scenario's draft payload as the output-tool call ─
def _model(payload: dict) -> FunctionModel:
    def fn(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, payload)])

    return FunctionModel(fn)


# ── LIVE stages (exist on main + a9m.5) ──────────────────────────────────────
def stage_trigger(scenario) -> GraphState:
    t = scenario.trigger
    return GraphState(tenant_id=t["tenant_id"], run_id=f"e2e-{scenario.name}", topic=t["topic"])


def stage_draft(scenario) -> tuple[PostDraft | None, bool]:
    """idea->angle->draft->validate, condensed: render the (pre-selected) angle, run
    the draft cell with the scenario's injected draft, return (draft_or_None, gates_ok).

    A persistently invalid draft raises CellError inside the cell -> we treat that as
    'no approvable draft' (the slice will route to regenerate/review)."""
    grounding = _grounding(scenario.coverage)
    cell = build_draft_cell(grounding=grounding, platform=Platform_of(scenario))
    prompt = render_angle_prompt(
        hook=scenario.angle["hook"],
        rationale=scenario.angle["rationale"],
        format_hint=scenario.angle["format_hint"],
    )
    from cells.base import CellError

    try:
        # in-spec drafts validate first-pass; out-of-spec ones would loop->CellError,
        # so for the scaffold we run the bank directly to get a deterministic verdict.
        draft = cell.run_sync(prompt, model=_model(scenario.draft))
        return draft, True
    except CellError:
        return None, False


def Platform_of(scenario):
    from cells.post_schemas import Platform

    return Platform(scenario.trigger["channel"])


# ── TODO STUBS — fill when a9m.7 / a9m.8 land ────────────────────────────────
def stage_score_and_route(
    state: GraphState,
    draft: PostDraft | None,
    gates_ok: bool,
    *,
    autonomy: AutonomyMode = AutonomyMode.REVIEW,
) -> RouteDecision:
    """TODO(a9m.7, eng3): replace with the real Check&Score node:
        confidence = score(validator-bank + grounding signals)   # deterministic, Phase-3
        decision   = autonomy.produce.produce_and_record_decision(...) -> route(...)
        persist POST Action (PENDING) to the console read model + step_log; set HITL interrupt.
    Scaffold stand-in: route() under HELD with a 1-gate summary — proves HELD->review/regenerate,
    NEVER auto. Confidence is a placeholder until a9m.7 computes the real one."""
    gate = Gate(name="content_bank", passed=gates_ok)
    confidence = 0.95 if gates_ok else 0.4
    return route(confidence, DEFAULT_THRESHOLD, [gate], autonomy)


def stage_approve_and_publish(state: GraphState, decision: RouteDecision) -> dict:
    """TODO(a9m.8, eng4): replace with the manual-approve resume -> MockPublisher behind
    the exactly-once side-effect boundary (idempotency key + unique constraint + outbox):
        resume(run_id, Decision(approve)) -> SideEffectBoundary.enqueue(...) -> MockPublisher
        -> published-ledger row + feed event; exactly-once across crash/retry (call-count=1).
    Only reachable on the approve path (439 holds auto). Scaffold raises until wired."""
    raise NotImplementedError("a9m.8 MockPublisher wire-point (approve->exactly-once mock-publish)")


# ── LIVE assertions (the portion that exists today) ──────────────────────────
@pytest.mark.parametrize("sc", SCENARIOS, ids=[s.name for s in SCENARIOS])
def test_slice_live_through_route(sc):
    """trigger -> draft -> validate -> score+route(stub): the routing decision matches
    the scenario expectation and is NEVER auto under HELD (439)."""
    state = stage_trigger(sc)
    draft, gates_ok = stage_draft(sc)
    assert gates_ok == sc.expect_gates_ok, f"{sc.name}: gate outcome mismatch"
    if draft is not None:
        state = persist_draft(state, draft)
        assert state.draft is not None
    decision = stage_score_and_route(state, draft, gates_ok)
    assert decision is not RouteDecision.AUTO, f"{sc.name}: AUTO under HELD (439 violation)"
    assert decision is sc.expect_route, f"{sc.name}: routed {decision} != {sc.expect_route}"


@pytest.mark.skip(
    reason="awaits a9m.7 (route+Action persist) + a9m.8 (mock-publish); "
    "fill the two stage stubs + flip this skip"
)
def test_slice_full_e2e_approve_publish():
    """FULL e2e: trigger->...->route(review)->approve->mock-publish exactly once.
    Drop-in once a9m.7/.8 land — uses the same SCENARIOS + the two filled stubs."""
    happy = next(s for s in SCENARIOS if s.name == "happy_on_voice_held_review")
    state = stage_trigger(happy)
    draft, gates_ok = stage_draft(happy)
    state = persist_draft(state, draft)
    decision = stage_score_and_route(state, draft, gates_ok)
    assert decision is RouteDecision.REVIEW  # 439 held -> review, never auto
    result = stage_approve_and_publish(state, decision)  # TODO(a9m.8)
    assert result["publish_count"] == 1  # exactly-once
