"""Interview-gate tests (P1a) — pure/offline (no model, no DB).

Proves the Agency-page gate that stops a blind run:
* a bare/partial plan is NOT armed; arming requires every gating field;
* the next question walks the gating set then the optional set, then stops;
* answers coerce to the right types (count -> int, yes/no -> bool, channels -> list,
  "drafts"/"stage" -> drafts_only bool);
* an unrecognized yes/no stays UNANSWERED (the gate never guesses the operator).
"""

from __future__ import annotations

from studio.agui import CampaignPlan
from studio.interview import (
    GATING_FIELDS,
    apply_fields,
    coerce_field,
    field_present,
    interview_state,
    is_armed,
    next_question,
)


def _full_plan() -> CampaignPlan:
    return CampaignPlan(
        goal="win back lapsed clients",
        audience="clients who haven't booked in 90 days",
        channels=["email"],
        campaign_type="win-back",
        output_count=10,
    )


def test_empty_plan_is_not_armed_and_asks_the_goal_first() -> None:
    plan = CampaignPlan()
    assert is_armed(plan) is False
    state = interview_state(plan)
    assert state["armed"] is False
    assert state["readyMessage"] is None
    # the first gating field is goal
    assert state["nextQuestion"]["field"] == "goal"
    assert set(state["missing"]) == set(GATING_FIELDS)


def test_arming_requires_every_gating_field() -> None:
    plan = _full_plan()
    assert is_armed(plan) is True
    state = interview_state(plan)
    assert state["armed"] is True
    assert state["missing"] == []
    assert "enough context" in state["readyMessage"].lower()
    # remove any single gating field -> not armed, and that field is asked next
    for f in GATING_FIELDS:
        p = _full_plan()
        setattr(p, f, [] if f == "channels" else (0 if f == "output_count" else ""))
        assert is_armed(p) is False, f
        assert next_question(p)["field"] == f


def test_output_count_zero_does_not_arm() -> None:
    plan = _full_plan()
    plan.output_count = 0
    assert is_armed(plan) is False
    assert next_question(plan)["field"] == "output_count"


def test_optional_questions_follow_gating_then_stop() -> None:
    plan = _full_plan()  # all gating answered -> next is the first OPTIONAL field
    assert is_armed(plan) is True
    assert next_question(plan)["field"] == "action_type"
    # answer every optional too -> no more questions
    plan.action_type = "outreach"
    plan.deep_research = True
    plan.lead_count = 25
    plan.tone = "warm, plain-spoken"
    plan.drafts_only = False
    assert next_question(plan) is None


def test_coercion_of_answer_types() -> None:
    assert coerce_field("output_count", "make 10 emails") == 10
    assert coerce_field("lead_count", 25) == 25
    assert coerce_field("deep_research", "yes") is True
    assert coerce_field("deep_research", "no") is False
    assert coerce_field("drafts_only", "drafts") is True
    assert coerce_field("drafts_only", "stage") is False
    assert coerce_field("channels", "email and instagram") == ["email", "instagram"]
    assert coerce_field("channels", ["Email", " IG "]) == ["Email", "IG"]
    assert coerce_field("goal", "  fill Tuesdays  ") == "fill Tuesdays"


def test_apply_fields_skips_unrecognized_yes_no_and_non_interview_keys() -> None:
    plan = CampaignPlan()
    apply_fields(plan, {"deep_research": "maybe", "goal": "x", "not_a_field": "ignored"})
    # an unrecognized yes/no leaves the bool UNANSWERED (no guess)
    assert plan.deep_research is None
    assert field_present(plan, "deep_research") is False
    assert plan.goal == "x"
    # a recognized answer sets it
    apply_fields(plan, {"deep_research": "yes"})
    assert plan.deep_research is True


def test_bool_present_once_explicitly_false() -> None:
    plan = CampaignPlan(drafts_only=False)
    assert field_present(plan, "drafts_only") is True  # an explicit choice counts
