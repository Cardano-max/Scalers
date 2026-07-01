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
    plan_summary,
    real_lead_count,
)


def _full_plan() -> CampaignPlan:
    return CampaignPlan(
        goal="win back lapsed clients",
        audience="clients who haven't booked in 90 days",
        channels=["email"],
        lead_source="provided",
        campaign_type="win-back",
        output_count=10,
        offer="reply to book your next session",
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
    assert "go ahead" in state["readyMessage"].lower()
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
    assert next_question(plan)["field"] == "per_lead"
    # answer every optional too -> no more questions
    plan.per_lead = True
    plan.personalize = True
    plan.deep_research = True
    plan.tone = "warm, plain-spoken"
    plan.action_type = "outreach"
    plan.lead_count = 25
    plan.drafts_only = False
    # P1 tattoo-pivot optional refinements (also optional; asked after the rest).
    plan.target_category = "all"
    plan.scope = "whole studio"
    plan.use_conversation_history = True
    plan.attach_artwork = False
    # P1-B/P1-D exec-discovery + enriched-spec optionals (also asked after the rest).
    plan.offer_type = "booking"
    plan.segment = "warm"
    plan.no_convert_reason = "price felt steep"
    plan.prior_contact = "no prior contact"
    plan.brand_voice = "warm and plain-spoken"
    plan.research_depth = "standard"
    plan.personalization_rules = "style only"
    plan.do_not_use = "no discounts"
    plan.success_criteria = "5 bookings"
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


def test_channels_coercion_validates_against_real_channels() -> None:
    # A NON-channel answer — an offer/CTA mistakenly given at the channels question —
    # is DROPPED, never kept as a bogus channel that would leak into per_channel_quota
    # and make the team draft for a channel that does not exist (the "0 drafts" bug).
    assert coerce_field("channels", "reply to book your session") == []
    # Real free-text channels map to canonical channels.
    assert coerce_field("channels", "text message") == ["sms"]
    assert coerce_field("channels", "instagram or facebook") == ["instagram", "facebook"]
    assert coerce_field("channels", "just email please") == ["email"]
    # A "mix / all" answer expands to a sane real spread rather than an empty answer.
    assert coerce_field("channels", "a mix of those") == ["email", "instagram"]
    # A structured picker list is trusted and passed through (only stripped).
    assert coerce_field("channels", ["sms", "email"]) == ["sms", "email"]


def test_lead_source_coercion_to_two_canonical_modes() -> None:
    for provided in ("provided", "use my CSV", "database", "existing", "uploaded", "use my leads"):
        assert coerce_field("lead_source", provided) == "provided", provided
    for new in ("new", "scrape from the web", "find new leads", "source new", "internet"):
        assert coerce_field("lead_source", new) == "source_new", new
    # unrecognized -> "" (unanswered, no guess) so the gate keeps asking
    assert coerce_field("lead_source", "hmm not sure") == ""
    # and lead_source is a GATING field the interview must ask
    assert "lead_source" in GATING_FIELDS


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


# --------------------------------------------------------------------------- #
# Exec question set + plan summary + go-ahead gate (#4/#5)
# --------------------------------------------------------------------------- #

def test_offer_is_a_gating_field_that_blocks_the_run() -> None:
    # The offer / CTA is part of the full exec question set and gates the run: a plan
    # missing only the offer is NOT armed and the supervisor asks for it before running.
    assert "offer" in GATING_FIELDS
    plan = _full_plan()
    plan.offer = ""
    assert is_armed(plan) is False
    assert next_question(plan)["field"] == "offer"
    # no plan summary (and so no go-ahead) until the gate is fully answered
    assert plan_summary(plan) is None
    assert interview_state(plan)["planSummary"] is None


def test_plan_summary_only_appears_once_armed_so_run_waits_for_go_ahead() -> None:
    # A half-answered brief has no summary -> the operator is never shown a "go ahead".
    plan = CampaignPlan(goal="fill Tuesdays")
    assert plan_summary(plan) is None
    # A fully-armed brief produces the senior-exec summary the operator approves first.
    plan = _full_plan()
    summary = plan_summary(plan)
    assert summary is not None
    assert "go ahead" in summary["confirm"].lower()
    assert interview_state(plan)["planSummary"] is not None


def test_per_lead_coercion_personalized_vs_shared() -> None:
    for one_each in ("personalized", "per lead", "each", "one per lead", "individual"):
        assert coerce_field("per_lead", one_each) is True, one_each
    for shared in ("shared", "one shared", "everyone", "same", "blast"):
        assert coerce_field("per_lead", shared) is False, shared
    # unrecognized -> None (stays unanswered, no guess)
    assert coerce_field("per_lead", "hmm") is None


def test_plan_summary_reflects_real_lead_count_and_chosen_channels() -> None:
    # Attach a REAL uploaded list of 3 rows + 2 channels. The summary must use those
    # real numbers, never a fabricated count.
    plan = _full_plan()
    plan.channels = ["email", "instagram"]
    plan.output_count = 7
    # REAL upload shape: customers.rows is the int data-row count the upload route
    # attaches, with the ingested customer_ids alongside.
    plan.customers = {"rows": 3, "customer_ids": ["c1", "c2", "c3"]}
    assert real_lead_count(plan) == 3
    summary = plan_summary(plan)
    flat = " | ".join(f"{ln['label']}: {ln['value']}" for ln in summary["lines"])
    # Target line carries the REAL uploaded lead count (3), not the output count.
    assert "3 lead" in flat
    assert summary["leadCount"] == 3
    # The real chosen channels appear, and the create line uses the real output count.
    assert summary["channels"] == ["email", "instagram"]
    assert "7" in flat
    # Always-held reassurance is stated as fact (the studio never sends without approval).
    assert any("Review Queue" in ln["value"] for ln in summary["lines"])


def test_plan_summary_shared_message_does_not_claim_per_lead() -> None:
    plan = _full_plan()
    plan.per_lead = False
    plan.customers = {"rows": 2, "customer_ids": ["c1", "c2"]}
    create = next(ln["value"] for ln in plan_summary(plan)["lines"] if ln["label"] == "Create")
    assert "shared" in create.lower()
    assert "one per lead" not in create.lower()


# --------------------------------------------------------------------------- #
# P1-B — executive discovery questions (typed CTA + segment + why-no-convert +
# prior-contact). All OPTIONAL (never break the gate) but the interview asks them
# and they flow into the plan summary.
# --------------------------------------------------------------------------- #
_EXEC_FIELDS = ("offer_type", "segment", "no_convert_reason", "prior_contact")
_SPEC_FIELDS = ("brand_voice", "research_depth", "personalization_rules",
                "do_not_use", "success_criteria")


def test_exec_and_spec_fields_are_asked_but_never_gate() -> None:
    from studio.interview import GATING_FIELDS, INTERVIEW_FIELDS
    # the new fields are part of the interview (asked) ...
    for f in (*_EXEC_FIELDS, *_SPEC_FIELDS):
        assert f in INTERVIEW_FIELDS, f
    # ... but NONE of them gate the run (arming behavior preserved).
    for f in (*_EXEC_FIELDS, *_SPEC_FIELDS):
        assert f not in GATING_FIELDS, f
    # a fully-gated plan is armed even with every new field unanswered
    assert is_armed(_full_plan()) is True


def test_offer_type_coercion_to_typed_menu() -> None:
    assert coerce_field("offer_type", "book an appointment") == "booking"
    assert coerce_field("offer_type", "consultation") == "consult"
    assert coerce_field("offer_type", "flash sheet") == "flash"
    assert coerce_field("offer_type", "a promo") == "discount"
    assert coerce_field("offer_type", "touch up") == "touch-up"
    assert coerce_field("offer_type", "spotlight") == "artist-spotlight"
    # an unmapped answer is kept verbatim (honest, and does not loop re-asking)
    assert coerce_field("offer_type", "mystery box") == "mystery box"


def test_segment_coercion_to_lifecycle_buckets() -> None:
    assert coerce_field("segment", "brand new") == "cold"
    assert coerce_field("segment", "warm leads") == "warm"
    assert coerce_field("segment", "past clients") == "past"
    assert coerce_field("segment", "my regulars") == "recurring"


def test_research_depth_coercion() -> None:
    assert coerce_field("research_depth", "a light look") == "light"
    assert coerce_field("research_depth", "standard pass") == "standard"
    assert coerce_field("research_depth", "deep research") == "deep"


def test_free_text_exec_fields_pass_through() -> None:
    assert coerce_field("no_convert_reason", "  price felt steep ") == "price felt steep"
    assert coerce_field("prior_contact", "we DMed last month") == "we DMed last month"
    assert coerce_field("do_not_use", "no discounts, no emojis") == "no discounts, no emojis"


def test_exec_and_spec_fields_appear_in_plan_summary() -> None:
    plan = _full_plan()
    apply_fields(plan, {
        "offer_type": "book an appointment",
        "segment": "warm",
        "no_convert_reason": "price felt steep",
        "prior_contact": "we DMed last month",
        "brand_voice": "warm and plain-spoken, never salesy",
        "research_depth": "deep",
        "personalization_rules": "reference their style, not personal life",
        "do_not_use": "no discounts",
        "success_criteria": "5 bookings",
    })
    summary = plan_summary(plan)
    flat = {ln["label"]: ln["value"] for ln in summary["lines"]}
    assert flat.get("Segment") == "warm"
    assert "book an appointment" in flat.get("The ask", "")
    assert flat.get("Why they haven't booked") == "price felt steep"
    assert flat.get("Prior contact") == "we DMed last month"
    assert "warm and plain-spoken" in flat.get("Brand voice", "")
    assert flat.get("Research depth") == "deep"
    assert "reference their style" in flat.get("Personalization rules", "")
    assert flat.get("Do NOT use") == "no discounts"
    assert flat.get("Success looks like") == "5 bookings"


def test_unanswered_spec_fields_are_absent_from_summary() -> None:
    # HONESTY: a field the operator didn't answer does NOT appear (no fabricated spec).
    summary = plan_summary(_full_plan())
    labels = {ln["label"] for ln in summary["lines"]}
    for absent in ("Segment", "Research depth", "Do NOT use", "Success looks like",
                   "Personalization rules", "Why they haven't booked", "Prior contact"):
        assert absent not in labels, absent
