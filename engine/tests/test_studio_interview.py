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
    OPTIONAL_FIELDS,
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


# --------------------------------------------------------------------------- #
# P1-C — ADAPTIVE follow-up sequencing (CustomerAcq-65w.3). The exec-discovery
# follow-ups BRANCH on the last answer: skip the irrelevant, probe the thin, cap
# the total — while the GATING run-gate stays deterministic (arms only on real
# answers to the core questions).
# --------------------------------------------------------------------------- #

def _answer_for(field: str):
    """A plausible, SPECIFIC answer for a given interview field so a drained interview
    advances (never a vague goal, so the goal probe resolves after one clarification)."""
    if field in ("output_count", "lead_count"):
        return 10
    if field in ("per_lead", "personalize", "deep_research", "drafts_only",
                 "use_conversation_history", "attach_artwork"):
        return "yes"
    if field == "channels":
        return "email"
    if field == "goal":
        return "win back lapsed clients from last spring"
    if field == "lead_source":
        return "provided"
    if field == "segment":
        return "warm"
    return "a specific concrete answer"


def _drain(plan, limit: int = 60):
    """Walk the interview to completion, answering each question the way the operator
    would, and return the list of ``(field, is_probe)`` asked in order."""
    asked: list[tuple[str, bool]] = []
    for _ in range(limit):
        q = next_question(plan)
        if q is None:
            return asked
        asked.append((q["field"], bool(q.get("probe"))))
        apply_fields(plan, {q["field"]: _answer_for(q["field"])})
    raise AssertionError("interview did not terminate — possible ask loop")


def test_recurring_segment_skips_the_why_no_convert_branch() -> None:
    # AC (a): answering "just my regulars" -> the cold-lead / why-didn't-they-convert
    # probe ("Why do you think they haven't booked yet?") is IRRELEVANT and skipped.
    plan = _full_plan()
    apply_fields(plan, {"segment": "just my regulars"})
    assert plan.segment == "recurring"
    asked = dict(_drain(plan))  # {field: is_probe}
    assert "no_convert_reason" not in asked
    # control: a WARM cohort still gets the why-no-convert probe within budget.
    warm = _full_plan()
    apply_fields(warm, {"segment": "warm"})
    assert "no_convert_reason" in dict(_drain(warm))


def test_source_new_skips_prior_contact_and_conversation_history() -> None:
    # Brand-new online prospects have no prior conversation with you — those follow-ups
    # are skipped when the operator chose to SOURCE new leads.
    plan = _full_plan()
    plan.lead_source = "source_new"
    asked = dict(_drain(plan))
    assert "prior_contact" not in asked
    assert "use_conversation_history" not in asked


def test_vague_goal_triggers_one_clarifying_probe_then_resolves() -> None:
    # AC (b): a thin goal ("more clients") gets exactly ONE clarifying probe; once the
    # operator gives a concrete target the probe is gone (a single clarification ends it).
    plan = _full_plan()
    plan.goal = "more clients"
    q = next_question(plan)
    assert q["field"] == "goal"
    assert q.get("probe") is True
    # a vague goal is still a *present* answer -> the deterministic gate is not fooled.
    assert is_armed(plan) is True
    apply_fields(plan, {"goal": "win back lapsed clients from last spring"})
    q2 = next_question(plan)
    assert not (q2 and q2.get("probe")), "a clarified goal must not be re-probed"


def test_specific_goal_is_not_probed() -> None:
    # AC (c): a rich goal gets NO probe — the first thing asked is a normal follow-up.
    plan = _full_plan()  # goal="win back lapsed clients" is specific
    q = next_question(plan)
    assert q is None or not q.get("probe")


def test_is_vague_goal_word_set() -> None:
    from studio.interview import _is_vague_goal
    for vague in ("more clients", "grow the business", "increase sales",
                  "more customers", "just more bookings", "get more people",
                  "marketing", "growth"):
        assert _is_vague_goal(vague) is True, vague
    for specific in ("fill quiet Tuesdays", "win back lapsed clients",
                     "promote the new flash sheet", "reach fine-line fans in Brooklyn",
                     "book out my apprentice's chair"):
        assert _is_vague_goal(specific) is False, specific


def test_follow_up_questions_are_capped() -> None:
    # AC (d): the exec-discovery follow-ups are capped so the interview never interrogates
    # — it stops asking optionals well before exhausting the whole optional set.
    from studio.interview import _MAX_FOLLOW_UPS
    plan = _full_plan()  # gated, specific goal
    asked = _drain(plan)
    optionals_asked = [f for f, _probe in asked if f in OPTIONAL_FIELDS]
    assert len(optionals_asked) <= _MAX_FOLLOW_UPS
    assert len(optionals_asked) < len(OPTIONAL_FIELDS)  # the cap actually bit


def test_should_skip_optional_branches() -> None:
    from studio.interview import _should_skip_optional
    regulars = _full_plan()
    apply_fields(regulars, {"segment": "just my regulars"})
    assert _should_skip_optional(regulars, "no_convert_reason") is True
    warm = _full_plan()
    apply_fields(warm, {"segment": "warm"})
    assert _should_skip_optional(warm, "no_convert_reason") is False
    src_new = _full_plan()
    src_new.lead_source = "source_new"
    assert _should_skip_optional(src_new, "prior_contact") is True
    assert _should_skip_optional(src_new, "use_conversation_history") is True
    no_personalize = _full_plan()
    apply_fields(no_personalize, {"personalize": "no"})
    assert _should_skip_optional(no_personalize, "personalization_rules") is True


def test_adaptive_changes_do_not_relax_the_run_gate() -> None:
    # AC (e) regression: the gate arms ONLY on the core GATING answers. Optionals and the
    # goal-probe never arm or block it, and a vague-but-present goal is a real answer.
    plan = CampaignPlan()
    assert is_armed(plan) is False
    # answer only OPTIONAL fields -> still not armed, and the next ask is a GATING field.
    apply_fields(plan, {"segment": "warm", "offer_type": "booking",
                        "brand_voice": "warm and plain-spoken"})
    assert is_armed(plan) is False
    assert next_question(plan)["field"] in GATING_FIELDS
    # a full gating set arms even if the goal is vague (deterministic, not fooled).
    full = _full_plan()
    full.goal = "more clients"
    assert is_armed(full) is True
    # and removing any one gating field disarms it (adaptive layer changed nothing here).
    for f in GATING_FIELDS:
        p = _full_plan()
        setattr(p, f, [] if f == "channels" else (0 if f == "output_count" else ""))
        assert is_armed(p) is False, f


# ── ju1.3: the canonical structured campaign-creation interview (10 questions) ──


def test_campaign_interview_has_the_ten_canonical_questions_in_order() -> None:
    from studio.interview import (
        CAMPAIGN_INTERVIEW_FIELDS,
        campaign_interview_questions,
    )

    qs = campaign_interview_questions()
    fields = [f for f, _ in qs]
    # The bead's 10, in ask order, keyed to real plan fields.
    assert fields == [
        "artist", "location", "segment", "output_count", "reference_campaign",
        "offer", "payment_plan", "attach_artwork", "drafts_only", "test_mode",
    ]
    assert fields == list(CAMPAIGN_INTERVIEW_FIELDS)
    assert len(qs) == 10
    # Every question has non-empty text, and every field is a settable plan field.
    plan = CampaignPlan()
    for f, text in qs:
        assert text.strip()
        assert hasattr(plan, f), f


def test_ten_questions_map_to_real_interview_fields() -> None:
    from studio.interview import CAMPAIGN_INTERVIEW_FIELDS, INTERVIEW_FIELDS

    for f in CAMPAIGN_INTERVIEW_FIELDS:
        assert f in INTERVIEW_FIELDS, f  # answerable via /studio/interview


def test_new_ju13_fields_coerce_correctly() -> None:
    # str fields pass through stripped; the two yes/no fields coerce to bool.
    assert coerce_field("artist", "  Angel ") == "Angel"
    assert coerce_field("location", "Spring Mountain") == "Spring Mountain"
    assert coerce_field("payment_plan", "Klarna & Affirm") == "Klarna & Affirm"
    assert coerce_field("reference_campaign", "yes") is True
    assert coerce_field("test_mode", "no") is False
    # an unrecognized yes/no stays unset (never guessed).
    assert coerce_field("test_mode", "hmmmm") is None


def test_answering_the_ten_populates_the_plan() -> None:
    from studio.interview import campaign_interview_questions

    plan = CampaignPlan()
    answers = {
        "artist": "Angel", "location": "Spring Mountain", "segment": "past",
        "output_count": 200, "reference_campaign": "yes", "offer": "$1200 full-day special",
        "payment_plan": "Klarna & Affirm", "attach_artwork": "yes", "drafts_only": "stage",
        "test_mode": "yes",
    }
    # Answer exactly the canonical fields.
    for f, _ in campaign_interview_questions():
        apply_fields(plan, {f: answers[f]})
    assert plan.artist == "Angel"
    assert plan.location == "Spring Mountain"
    assert plan.segment == "past"
    assert plan.output_count == 200
    assert plan.reference_campaign is True
    assert plan.payment_plan == "Klarna & Affirm"
    assert plan.attach_artwork is True
    assert plan.drafts_only is False  # "stage" -> stage in queue (not drafts-only)
    assert plan.test_mode is True


def test_test_mode_field_is_display_only_not_a_send_gate() -> None:
    # The interview field records the operator's stated preference; it must NEVER be the
    # thing that decides sends (that is ju1.1's server-side tenant gate). Here we simply
    # pin that the field exists and defaults to unset — the send gate lives elsewhere.
    plan = CampaignPlan()
    assert plan.test_mode is None
    apply_fields(plan, {"test_mode": "no"})
    assert plan.test_mode is False  # operator answered; the SERVER gate is unaffected


def test_campaign_interview_prompt_contains_all_ten_questions() -> None:
    from studio.interview import campaign_interview_prompt, campaign_interview_questions

    prompt = campaign_interview_prompt()
    for _f, q in campaign_interview_questions():
        assert q in prompt, q
    # Numbered 1..10 and mentions the server-side test-mode guarantee.
    for i in range(1, 11):
        assert f"{i}." in prompt
    assert "TEST MODE" in prompt and "held in the Review Queue" in prompt


# --------------------------------------------------------------------------- #
# PER-CHANNEL interview blocks (multi-channel campaigns). A campaign spanning
# ig + email + sms gets a SEPARATE channel-specific block per chosen channel,
# asked ONE CHANNEL AT A TIME after the base gating fields. Field ids are
# namespaced channel_plans.{ch}.{field}; answers land under plan.channel_plans.
# The blocks NEVER gate arming, and a single-channel plan is unchanged.
# --------------------------------------------------------------------------- #

def _multi_channel_plan() -> CampaignPlan:
    plan = _full_plan()
    plan.channels = ["instagram", "email"]
    return plan


def _channel_answer(field: str):
    leaf = field.rsplit(".", 1)[-1]
    if leaf == "output_count":
        return 3
    if leaf in ("attach_images", "competitor_research"):
        return "yes"
    return "a concrete channel answer"


def test_multi_channel_plan_walks_ig_block_after_base_gating() -> None:
    from studio.interview import CHANNEL_QUESTIONS

    plan = _multi_channel_plan()
    # base gating fields all answered -> the FIRST channel block starts (ig first,
    # the operator's channel order), before any optional follow-up.
    q = next_question(plan)
    assert q["field"] == "channel_plans.ig.goal"
    assert q["channel"] == "ig"
    # walk the channel questions to exhaustion: the whole ig block in order, THEN
    # the whole email block — one channel at a time, never interleaved.
    asked: list[str] = []
    for _ in range(30):
        q = next_question(plan)
        if q is None or not q["field"].startswith("channel_plans."):
            break
        asked.append(q["field"])
        apply_fields(plan, {q["field"]: _channel_answer(q["field"])})
    expected = [f for f, _ in CHANNEL_QUESTIONS["ig"]] + [f for f, _ in CHANNEL_QUESTIONS["email"]]
    assert asked == expected
    # after the channel blocks the interview falls through to the optional walk.
    assert next_question(plan)["field"] == "per_lead"


def test_ig_block_asks_competitor_research_images_and_style() -> None:
    from studio.interview import CHANNEL_QUESTIONS

    ig_fields = [f for f, _ in CHANNEL_QUESTIONS["ig"]]
    assert "channel_plans.ig.competitor_research" in ig_fields
    assert "channel_plans.ig.attach_images" in ig_fields
    assert "channel_plans.ig.image_style" in ig_fields
    questions = dict(CHANNEL_QUESTIONS["ig"])
    assert "competitor posts" in questions["channel_plans.ig.competitor_research"]
    assert "images" in questions["channel_plans.ig.attach_images"]
    # email and sms have their OWN blocks — no image questions there.
    for ch in ("email", "sms"):
        for f, _q in CHANNEL_QUESTIONS[ch]:
            assert "image" not in f, f


def test_field_present_resolves_namespaced_channel_fields() -> None:
    plan = _multi_channel_plan()
    assert field_present(plan, "channel_plans.ig.attach_images") is False
    apply_fields(plan, {"channel_plans.ig.attach_images": "yes"})
    assert plan.channel_plans["ig"]["attach_images"] is True
    assert field_present(plan, "channel_plans.ig.attach_images") is True
    # an explicit NO is also a real answer (the operator made a choice)
    apply_fields(plan, {"channel_plans.ig.competitor_research": "no"})
    assert plan.channel_plans["ig"]["competitor_research"] is False
    assert field_present(plan, "channel_plans.ig.competitor_research") is True
    # ints coerce from free text; 0 stays unanswered
    apply_fields(plan, {"channel_plans.ig.output_count": "make 3 posts"})
    assert plan.channel_plans["ig"]["output_count"] == 3
    assert field_present(plan, "channel_plans.ig.output_count") is True
    assert field_present(plan, "channel_plans.email.output_count") is False


def test_apply_fields_ignores_unknown_channel_or_leaf() -> None:
    plan = _multi_channel_plan()
    apply_fields(plan, {
        "channel_plans.tiktok.goal": "x",     # no such channel block
        "channel_plans.ig.bogus_leaf": "x",   # no such leaf in the contract
        "channel_plans.ig.attach_images": "hmm",  # unrecognized yes/no -> no guess
    })
    assert plan.channel_plans == {}


def test_single_channel_plan_interview_unchanged() -> None:
    # REGRESSION: a plain single-channel plan never sees a channel block — the flat
    # interview walks straight from gating to the optional follow-ups.
    plan = _full_plan()  # channels=["email"]
    assert next_question(plan)["field"] == "per_lead"
    state = interview_state(plan)
    assert state["channelSections"] == []
    summary = plan_summary(plan)
    assert not any("·" in ln["label"] for ln in summary["lines"])


def test_channel_blocks_never_gate_arming() -> None:
    # The gate is the flat GATING set only: a fully-gated multi-channel plan is armed
    # even with EVERY channel question unanswered (and disarms only on gating fields).
    plan = _multi_channel_plan()
    assert is_armed(plan) is True
    assert interview_state(plan)["missing"] == []


def test_channel_sections_render_state_and_plan_summary_lines() -> None:
    plan = _multi_channel_plan()
    apply_fields(plan, {
        "channel_plans.ig.goal": "show off fresh fine-line work",
        "channel_plans.ig.attach_images": "yes",
    })
    state = interview_state(plan)
    sections = state["channelSections"]
    assert [s["channel"] for s in sections] == ["ig", "email"]
    assert sections[0]["label"] == "Instagram"
    ig_fields = {f["field"]: f for f in sections[0]["fields"]}
    assert ig_fields["channel_plans.ig.goal"]["answered"] is True
    assert ig_fields["channel_plans.ig.goal"]["value"] == "show off fresh fine-line work"
    assert ig_fields["channel_plans.ig.image_style"]["answered"] is False
    assert ig_fields["channel_plans.ig.image_style"]["value"] is None
    # the plan summary carries ONLY the answered channel fields ("Instagram · goal")
    flat = {ln["label"]: ln["value"] for ln in plan_summary(plan)["lines"]}
    assert flat.get("Instagram · goal") == "show off fresh fine-line work"
    assert flat.get("Instagram · attach images") == "yes"
    assert "Instagram · image style" not in flat  # unanswered -> absent (honest)
    assert not any(k.startswith("Email ·") for k in flat)


def test_sms_channel_block_stays_short() -> None:
    from studio.interview import CHANNEL_QUESTIONS

    sms_fields = [f.rsplit(".", 1)[-1] for f, _ in CHANNEL_QUESTIONS["sms"]]
    assert sms_fields == ["goal", "audience", "output_count"]
