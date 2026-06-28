"""Idea + Angle cell tests (bead a9m.4) — DB-free, hermetic (scripted model).

Covers the AC: Ideate returns a typed AngleSet (>=1 angle w/ rationale) grounded
in research + voice; malformed output is repaired or raises CellError (never raw);
SelectAngle picks one Angle deterministically (documented criteria) + de-dupes;
empty research -> brand-only angle w/ low_grounding; no viable angle -> route to
review (no crash); ideate + angle recorded to step_log.
"""

from __future__ import annotations

import pytest

from cells.base import CellError
from cells.ideate import Angle, AngleSet, MediaKind, build_ideate_cell, build_ideate_prompt
from cells.select_angle import AngleSelection, NoViableAngleError, select_angle
from cells.skills import Skill, compose_instructions
from harness.nodes import IdeateNode, SelectAngleNode
from research.content.items import ResearchItem, ResearchResult
from tests.conftest import tool_model


# ── fixtures ─────────────────────────────────────────────────────────────────


def _research(*texts_scores) -> ResearchResult:
    items = tuple(
        ResearchItem(source="exa", kind="signal", text=t, score=s) for t, s in texts_scores
    )
    return ResearchResult(query_intent="map_market", items=items, sources_used=("exa",))


def _angle_payload(*hooks) -> dict:
    return {
        "angles": [
            {"hook": h, "rationale": f"fits because of {h}", "format_hint": "reel"}
            for h in hooks
        ]
    }


_VALID = _angle_payload(
    "fine-line cover-ups that hide regret",
    "guest-spot dates are filling fast",
    "healed-result reveals beat flatlays",
)


# ── Ideate cell ──────────────────────────────────────────────────────────────


def test_ideate_returns_typed_angleset():
    cell = build_ideate_cell()
    out = cell.run_sync("ctx", model=tool_model(_VALID))
    assert isinstance(out, AngleSet)
    assert len(out.angles) == 3
    assert all(isinstance(a, Angle) and a.rationale for a in out.angles)
    assert out.angles[0].format_hint is MediaKind.REEL


def test_ideate_empty_angles_repairs_or_raises():
    cell = build_ideate_cell()
    # empty 'angles' fails the non_empty validator -> repair to a valid set
    res = cell.run_detailed_sync("ctx", model=tool_model({"angles": []}, _VALID))
    assert isinstance(res.value, AngleSet) and res.repairs >= 1


def test_ideate_persistently_bad_raises_cellerror_never_raw():
    cell = build_ideate_cell()
    with pytest.raises(CellError):
        cell.run_sync("ctx", model=tool_model({"angles": []}))


def test_ideate_composes_voice_skill_instructions():
    voice = Skill(ref="brand-voice/ink-studio", instructions="VOICE: warm, no clichés.")
    composed = compose_instructions("BASE", voice)
    assert composed.startswith("VOICE: warm, no clichés.")
    assert "BASE" in composed


# ── grounding prompt assembly ────────────────────────────────────────────────


def test_prompt_includes_research_and_flags_low_grounding():
    r = _research(("cover-up demand is high", 0.8), ("price-fairness questions recur", 0.7))
    prompt, low = build_ideate_prompt(r, topic="spring booking", wisdom=("show healed work",))
    assert "cover-up demand is high" in prompt and "spring booking" in prompt
    assert "show healed work" in prompt
    assert low is False

    empty_prompt, low2 = build_ideate_prompt(_research(), topic="t")
    assert low2 is True and "none available" in empty_prompt


# ── SelectAngle (deterministic) ──────────────────────────────────────────────


def test_select_angle_picks_highest_grounding():
    angles = AngleSet(angles=[
        Angle(hook="generic studio vibes", rationale="nice", format_hint=MediaKind.IMAGE),
        Angle(hook="cover-up regret transformations", rationale="addresses cover-up demand", format_hint=MediaKind.CAROUSEL),
    ])
    r = _research(("cover-up demand is high", 0.9), ("regret transformations resonate", 0.8))
    sel = select_angle(angles, r)
    assert isinstance(sel, AngleSelection)
    assert "cover-up" in sel.angle.hook and sel.score > 0 and sel.low_grounding is False


def test_select_angle_dedupes_near_duplicates():
    angles = AngleSet(angles=[
        Angle(hook="Cover-up regret!", rationale="a", format_hint=MediaKind.REEL),
        Angle(hook="cover up regret", rationale="b", format_hint=MediaKind.REEL),  # dup hook
    ])
    sel = select_angle(angles, _research(("cover up regret", 0.5)))
    assert sel.candidate_count == 1


def test_select_angle_low_grounding_brand_only():
    angles = AngleSet(angles=[
        Angle(hook="first angle", rationale="a", format_hint=MediaKind.IMAGE),
        Angle(hook="second angle", rationale="b", format_hint=MediaKind.REEL),
    ])
    sel = select_angle(angles, _research(), low_grounding=True)
    assert sel.low_grounding is True and sel.angle.hook == "first angle" and sel.score == 0.0


def test_select_angle_no_viable_raises():
    with pytest.raises(NoViableAngleError):
        select_angle(AngleSet(angles=[]), _research(("x", 0.5)))


# ── harness nodes + step_log ─────────────────────────────────────────────────


async def test_ideate_node_records_candidates_to_step_log():
    node = IdeateNode(build_ideate_cell(model=tool_model(_VALID)))
    state = _state_with(research=_research(("cover-up demand", 0.8)))
    out = await node(state)
    assert isinstance(out["angles"], AngleSet)
    assert out["step_log"] == ["ideate:3_candidates"]


async def test_select_angle_node_records_choice():
    node = SelectAngleNode()
    state = _state_with(
        research=_research(("cover-up demand", 0.9)),
        angles=AngleSet(angles=[Angle(hook="cover-up demand angle", rationale="r", format_hint=MediaKind.REEL)]),
    )
    out = await node(state)
    assert isinstance(out["angle"], AngleSelection)
    assert out["step_log"][0].startswith("select_angle:")


async def test_select_angle_node_no_viable_routes_to_review():
    node = SelectAngleNode()
    state = _state_with(research=_research(("x", 0.5)), angles=AngleSet(angles=[]))
    out = await node(state)
    assert out["decision"] == "review"
    assert "no_viable_angle" in out["step_log"][0]


def _state_with(*, research=None, angles=None):
    from harness.state import GraphState

    return GraphState(
        tenant_id="ink-studio", run_id="r1", topic="spring booking",
        research_result=research, angles=angles,
    )
