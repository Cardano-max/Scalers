"""Tests for the reply cell (CustomerAcq-1mk.6, Tier-3).

Covers the HARD RULE two ways (routing + cell-boundary validator), the hostile
pre-screen, the deterministic bank (incl. S3 AI-flagger), the cell offline
(FunctionModel), and S2 brand-voice composition.
"""

from __future__ import annotations

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from autonomy.decision import EscKind, SafetyVerdict
from cells.reply import (
    ReplyDraft,
    ReplyIntent,
    ReplySurface,
    build_reply_cell,
    build_reply_instructions,
    reply_validators,
    route_reply,
    screen_incoming,
)
from harness.state import AutonomyMode, RouteDecision


def _draft(**over) -> ReplyDraft:
    base = dict(surface=ReplySurface.COMMENT, text="Love this. DM me to start your design.",
                intent=ReplyIntent.NONE, recommend_escalate=False)
    base.update(over)
    # keep text clean of AI tells for routing tests that don't care about text
    return ReplyDraft(**base)


# --------------------------------------------------------------------------- #
# THE HARD RULE — routing
# --------------------------------------------------------------------------- #

def test_dm_always_escalates_even_at_full_confidence():
    d = _draft(surface=ReplySurface.DM, recommend_escalate=True)
    r = route_reply(d, confidence=1.0, autonomy=AutonomyMode.AUTO)
    assert r.decision is RouteDecision.REVIEW
    assert not r.auto
    assert "human" in r.escalation.label.lower()


def test_comment_auto_within_threshold():
    r = route_reply(_draft(), confidence=0.95, threshold=0.85, autonomy=AutonomyMode.AUTO)
    assert r.decision is RouteDecision.AUTO
    assert r.escalation.kind is EscKind.NONE


def test_comment_below_threshold_reviews():
    r = route_reply(_draft(), confidence=0.50, threshold=0.85)
    assert r.decision is RouteDecision.REVIEW
    assert r.escalation.kind is EscKind.BELOW_THRESHOLD


def test_comment_safety_veto_reviews():
    r = route_reply(_draft(), confidence=0.99, safety=SafetyVerdict.VETO)
    assert r.decision is RouteDecision.REVIEW
    assert r.escalation.kind is EscKind.SAFETY


def test_comment_needs_expertise_reviews():
    r = route_reply(_draft(needs_human_expertise=True, recommend_escalate=True), confidence=0.99)
    assert r.decision is RouteDecision.REVIEW
    assert "expertise" in r.escalation.label.lower()


def test_comment_dial_review_forces_review():
    r = route_reply(_draft(), confidence=0.99, autonomy=AutonomyMode.REVIEW)
    assert r.decision is RouteDecision.REVIEW


# --------------------------------------------------------------------------- #
# hostile pre-screen
# --------------------------------------------------------------------------- #

def test_screen_flags_hostile_and_threats():
    assert screen_incoming("you are a scam, this is garbage") is SafetyVerdict.VETO
    assert screen_incoming("i'll find you") is SafetyVerdict.VETO


def test_screen_passes_benign():
    assert screen_incoming("how much for a small piece?") is SafetyVerdict.PASS
    assert screen_incoming("") is SafetyVerdict.PASS


# --------------------------------------------------------------------------- #
# validators — including the cell-boundary half of the HARD RULE
# --------------------------------------------------------------------------- #

def test_dm_without_escalate_is_an_error():
    bad = _draft(surface=ReplySurface.DM, recommend_escalate=False)
    res = reply_validators().check(bad)
    assert not res.ok
    assert any(i.validator == "dm_requires_escalation" for i in res.errors)


def test_clean_comment_passes():
    res = reply_validators().check(_draft())
    assert res.ok, res.summary()


def test_ai_tell_in_reply_is_flagged():
    bad = _draft(text="Moreover, we craft — carefully.")
    res = reply_validators().check(bad)
    assert any(i.validator == "ai_flagger" for i in res.errors)


def test_overlong_reply_is_flagged():
    bad = _draft(text=" ".join(["word"] * 60))
    res = reply_validators().check(bad)
    assert any(i.validator == "word_count_between" for i in res.errors)


def test_expertise_without_escalate_is_an_error():
    bad = _draft(needs_human_expertise=True, recommend_escalate=False)
    res = reply_validators().check(bad)
    assert any(i.validator == "expertise_requires_escalation" for i in res.errors)


# --------------------------------------------------------------------------- #
# the cell (offline)
# --------------------------------------------------------------------------- #

def _model(*payloads: dict) -> FunctionModel:
    calls = {"n": 0}

    def fn(messages, info: AgentInfo) -> ModelResponse:
        idx = min(calls["n"], len(payloads) - 1)
        calls["n"] += 1
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, payloads[idx])])

    return FunctionModel(fn)


_GOOD_COMMENT = {"surface": "comment", "text": "Thank you. DM me to start your design.",
                 "intent": "none", "recommend_escalate": False}


def test_cell_returns_typed_reply():
    cell = build_reply_cell()
    out = cell.run_sync("comment: love your work!", model=_model(_GOOD_COMMENT))
    assert isinstance(out, ReplyDraft)
    assert out.surface is ReplySurface.COMMENT


def test_cell_repairs_dm_that_forgets_to_escalate():
    # First a DM with recommend_escalate=False (HARD RULE violation) -> repaired.
    cell = build_reply_cell()
    out = cell.run_detailed_sync(
        "dm: can you do a cover-up?",
        model=_model(
            {"surface": "dm", "text": "Sure, tell me more about it.", "intent": "booking",
             "recommend_escalate": False},                                   # violates HARD RULE
            {"surface": "dm", "text": "Happy to help — tell me more about it.".replace("—", ","),
             "intent": "booking", "recommend_escalate": True},               # fixed
        ),
    )
    assert out.value.surface is ReplySurface.DM
    assert out.value.recommend_escalate is True
    assert out.repairs >= 1


def test_cell_repairs_slop_reply():
    cell = build_reply_cell()
    out = cell.run_detailed_sync(
        "comment",
        model=_model(
            {"surface": "comment", "text": "Moreover, we craft — carefully.", "intent": "none",
             "recommend_escalate": False},                                   # slop
            _GOOD_COMMENT,                                                   # clean
        ),
    )
    assert out.repairs >= 1


# --------------------------------------------------------------------------- #
# S2 brand-voice composition
# --------------------------------------------------------------------------- #

def test_instructions_compose_brand_voice_and_hard_rule():
    instr = build_reply_instructions("Positioning: quiet personal story.", ("Free consult",))
    assert "quiet personal story" in instr
    assert "Free consult" in instr
    assert "DMs ALWAYS go to a human" in instr
