"""a9m.9 e2e content/voice fixtures (writer-owned, BUILD-READY for the slice test).

The on-voice trigger inputs + expected validator behavior + expected routing
(HELD -> review, NEVER auto) for the Phase-3 slice
``trigger -> idea -> angle -> draft -> validate -> score -> route``.

`SCENARIOS` is the importable contract a9m.9's full e2e test drives through the
graph (once eng3's a9m.7 route + eng4's a9m.8 publish land). This file ALSO
self-validates the content/voice half NOW against the real `draft_validators`
(a9m.5) and the real pure-code `route()` (Phase 1): each scenario's expected gate
outcome and routing decision are asserted, so a9m.9 inherits proven fixtures.

439 invariant proven here: under a HELD channel, even a max-confidence, all-gates-ok
draft routes to `review` — never `auto`.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from cells.draft import draft_validators, render_angle_prompt
from cells.post_schemas import MediaKind, Platform, PostDraft
from harness.router import DEFAULT_THRESHOLD, route
from harness.state import AutonomyMode, Gate, RouteDecision
from kb.voice import Exemplar, GroundingCoverage, VoiceDimensions, VoiceGrounding, Vocabulary

# ── tenant grounding (real a9m.3 contract; ladies8391 test tenant) ───────────

_VOCAB = Vocabulary(
    prefer=["made for you", "your story", "cover-up", "reclaim"],
    ban=["unleash", "slay", "queen", "best", "#1", "painless", "transform your look", "glow-up"],
    approved_claims=[
        "Woman-owned, appointment-only studio in Austin, TX.",
        "9 years tattooing, specializing in neo-traditional color and floral work.",
        "Free consultation before every booking.",
        "Custom designs drawn for you, no flash copies.",
    ],
    emoji_policy="0-2 per caption, only 🌸 🌷 🤍",
    hashtag_policy="3-6, lowercase, specific",
)
_DIMS = VoiceDimensions(
    tone=["warm, direct; first-person 'I' (Rae); soft-invite CTA"],
    structure=["short, one idea per line", "open on the client's story"],
    vocabulary=_VOCAB,
)
_EXEMPLARS = [
    Exemplar(
        content="She brought in her grandmother's garden and we drew flowers that grew there. Healed now.",
        metrics={"on_voice": True},
        similarity=0.91,
    ),
]


def _grounding(coverage=GroundingCoverage.FULL) -> VoiceGrounding:
    ex = [] if coverage is GroundingCoverage.SPARSE else _EXEMPLARS
    return VoiceGrounding(
        tenant_id="ladies8391",
        dimensions=_DIMS,
        exemplars=ex,
        coverage=coverage,
        low_grounding=coverage is GroundingCoverage.SPARSE,
        exemplar_count=len(ex),
    )


def _media(kind="image", ar="4:5", dur=None, brief="floral forearm, natural light"):
    return {"kind": kind, "aspect_ratio": ar, "duration_s": dur, "brief": brief}


def _draft(caption, *, hashtags=None, cta="DM me to start your design.", media=None):
    return {
        "platform": "instagram",
        "caption": caption,
        "hashtags": ["neotraditionaltattoo", "floraltattoo", "austintattoo"]
        if hashtags is None
        else hashtags,
        "call_to_action": cta,
        "media": media or _media(),
    }


# ── the e2e scenarios (the importable contract for a9m.9) ─────────────────────


@dataclass(frozen=True)
class E2EScenario:
    """One slice run: a trigger + the on-voice draft the cells yield + expectations."""

    name: str
    trigger: dict  # what starts the run (schedule/command)
    angle: dict  # selected Angle fields (a9m.4)
    draft: dict  # the PostDraft payload the draft cell yields
    coverage: GroundingCoverage  # grounding band for the run
    expect_gates_ok: bool  # does the deterministic bank pass?
    expect_route: RouteDecision  # HELD outcome — NEVER AUTO
    confidence: float  # deterministic placeholder (a9m.7 computes the real one)
    note: str = ""


def _trigger(topic):
    return {
        "tenant_id": "ladies8391",
        "channel": "instagram",
        "command": "schedule_post",
        "topic": topic,
    }


SCENARIOS: list[E2EScenario] = [
    E2EScenario(
        name="happy_on_voice_held_review",
        trigger=_trigger("floral cover-up reclaim"),
        angle={
            "hook": "reclaim a scar with a floral piece",
            "rationale": "reclaim pillar + client story",
            "format_hint": "image",
        },
        draft=_draft(
            "She brought in her mom's garden and we drew three flowers that actually grew "
            "there. Healed and settled now. 🌸"
        ),
        coverage=GroundingCoverage.FULL,
        expect_gates_ok=True,
        expect_route=RouteDecision.REVIEW,
        confidence=0.95,
        note="Clean on-voice draft; 439 HELD -> review (NOT auto) even at high confidence.",
    ),
    E2EScenario(
        name="held_high_confidence_never_auto",
        trigger=_trigger("first-timer welcome"),
        angle={
            "hook": "reassure the nervous first-timer",
            "rationale": "first-timer pillar",
            "format_hint": "image",
        },
        draft=_draft(
            "First tattoo? We can go slow. I'll walk you through every step. 🤍",
            hashtags=["womentattooartist", "austintattoo", "neotraditionaltattoo"],
        ),
        coverage=GroundingCoverage.FULL,
        expect_gates_ok=True,
        expect_route=RouteDecision.REVIEW,
        confidence=1.0,
        note="THE 439 PROOF: max confidence + all gates ok + HELD -> still review.",
    ),
    E2EScenario(
        name="out_of_spec_media_regenerate",
        trigger=_trigger("reel teaser"),
        angle={
            "hook": "process peek reel",
            "rationale": "show the linework",
            "format_hint": "reel",
        },
        draft=_draft(
            "A quiet look at how a custom floral piece comes together. 🌷",
            media=_media("reel", "9:16", None),
        ),  # reel missing duration -> media gate fails
        coverage=GroundingCoverage.FULL,
        expect_gates_ok=False,
        expect_route=RouteDecision.REGENERATE,
        confidence=0.9,
        note="Out-of-spec creative (POST-02) -> regenerate; never approvable as-is.",
    ),
    E2EScenario(
        name="banned_off_voice_regenerate",
        trigger=_trigger("promo"),
        angle={"hook": "hype the cover-up", "rationale": "(off-strategy)", "format_hint": "image"},
        draft=_draft("Unleash your inner queen 🌸 such a glow-up, book now."),  # banned lexicon
        coverage=GroundingCoverage.FULL,
        expect_gates_ok=False,
        expect_route=RouteDecision.REGENERATE,
        confidence=0.4,
        note="Off-voice/banned -> gate fails -> regenerate; never published.",
    ),
    E2EScenario(
        name="unapproved_claim_regenerate",
        trigger=_trigger("credentials"),
        angle={"hook": "build trust", "rationale": "trust pillar", "format_hint": "image"},
        draft=_draft("100% painless and guaranteed. 12 years and counting."),  # claim + ban
        coverage=GroundingCoverage.FULL,
        expect_gates_ok=False,
        expect_route=RouteDecision.REGENERATE,
        confidence=0.5,
        note="Claim not in approved_claims -> claim gate fails -> regenerate-then-escalate.",
    ),
    E2EScenario(
        name="empty_research_sparse_review",
        trigger=_trigger("new tenant, empty KB"),
        angle={
            "hook": "introduce the studio",
            "rationale": "brand-only (no research)",
            "format_hint": "image",
        },
        draft=_draft(
            "This room is women-first on purpose. No rush, no dumb questions. 🤍",
            hashtags=["womentattooartist", "austintattoo", "neotraditionaltattoo"],
        ),
        coverage=GroundingCoverage.SPARSE,
        expect_gates_ok=True,
        expect_route=RouteDecision.REVIEW,
        confidence=0.6,
        note="Empty research -> dimensions-only draft (low_grounding); lower confidence -> review.",
    ),
]


# ── self-validation: prove each scenario against the REAL bank + router ───────


def _gates_for(scenario: E2EScenario) -> tuple[bool, list[Gate]]:
    bank = draft_validators(grounding=_grounding(scenario.coverage), platform=Platform.INSTAGRAM)
    res = bank.check(PostDraft.model_validate(scenario.draft))
    # one summary gate drives the router (router rule 1: any failed gate -> regenerate)
    return res.ok, [Gate(name="content_bank", passed=res.ok)]


def _route_held(confidence: float, gates: list[Gate]) -> RouteDecision:
    # 439 HELD modeled as the approve-first dial (AutonomyMode.REVIEW) — never auto.
    return route(confidence, DEFAULT_THRESHOLD, gates, AutonomyMode.REVIEW)


@pytest.mark.parametrize("sc", SCENARIOS, ids=[s.name for s in SCENARIOS])
def test_scenario_gate_outcome_matches(sc: E2EScenario):
    ok, _ = _gates_for(sc)
    assert ok == sc.expect_gates_ok, (
        f"{sc.name}: gate outcome {ok} != expected {sc.expect_gates_ok}"
    )


@pytest.mark.parametrize("sc", SCENARIOS, ids=[s.name for s in SCENARIOS])
def test_scenario_routes_as_expected_and_never_auto(sc: E2EScenario):
    ok, gates = _gates_for(sc)
    decision = _route_held(sc.confidence, gates)
    assert decision is not RouteDecision.AUTO, f"{sc.name}: routed AUTO under HELD (439 violation)"
    assert decision is sc.expect_route, (
        f"{sc.name}: routed {decision} != expected {sc.expect_route}"
    )


def test_439_proof_max_confidence_held_is_review():
    sc = next(s for s in SCENARIOS if s.name == "held_high_confidence_never_auto")
    ok, gates = _gates_for(sc)
    assert ok is True
    assert _route_held(1.0, gates) is RouteDecision.REVIEW  # max conf + held -> review, not auto


def test_angle_prompt_renders_for_each_scenario():
    for sc in SCENARIOS:
        p = render_angle_prompt(
            hook=sc.angle["hook"],
            rationale=sc.angle["rationale"],
            format_hint=MediaKind(sc.angle["format_hint"]),
        )
        assert "WINNING ANGLE" in p and sc.angle["hook"] in p


def test_scenarios_cover_required_e2e_cases():
    names = {s.name for s in SCENARIOS}
    assert {
        "happy_on_voice_held_review",
        "held_high_confidence_never_auto",
        "out_of_spec_media_regenerate",
        "banned_off_voice_regenerate",
        "unapproved_claim_regenerate",
        "empty_research_sparse_review",
    } <= names
    # no scenario expects AUTO (439 hold spans the whole slice)
    assert all(s.expect_route is not RouteDecision.AUTO for s in SCENARIOS)
