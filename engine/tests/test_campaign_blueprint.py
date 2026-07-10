"""Planner / CampaignBlueprint — pure/offline tests (no key, no network, no DB).

Proves the PLANNER decomposes the interview intent into an executable blueprint whose
offer_logic is GROUNDED in the real offers doc (an objection with no real offer maps to
None, never an invented code), whose quota distributes across channels, and whose honest
provenance reads ``grounded_rules`` when no model call happened. The offers source is
monkeypatched so the grounding is proven without Postgres.
"""

from __future__ import annotations

from studio import offers as offers_mod
from studio.agui import CampaignPlan
from studio.campaign_blueprint import (
    CampaignBlueprint,
    build_blueprint,
    offer_rule_for,
    parse_explicit_count,
)

_REAL_OFFERS = [
    offers_mod.Offer(code="FLOWER15", discount="15%", applies_to=["floral"], kind="discount"),
    offers_mod.Offer(code="SPLIT3", discount="3 payments", applies_to=["sleeve"], kind="payment"),
]


def test_blueprint_is_deterministic_and_grounds_offers(monkeypatch) -> None:
    monkeypatch.setattr(offers_mod, "get_offers", lambda tid, dsn=None: list(_REAL_OFFERS))
    plan = CampaignPlan(
        goal="win back lapsed clients",
        target_category="past-customer-reactivation",
        scope="whole studio",
        channels=["sms", "email"],
        output_count=4,
    )
    bp = build_blueprint(plan, "ladies8391", None, run_id="r1", use_llm=False)

    assert isinstance(bp, CampaignBlueprint)
    # No model call happened -> honest provenance (never a fake Opus attribution).
    assert bp.planner_model == "grounded_rules"
    # Quota distributes the output_count across the two channels.
    assert bp.per_channel_quota == {"sms": 2, "email": 2}
    assert bp.stop_conditions.total_quota == 4
    # Reactivation cohort -> the plan ASSUMES price dominates (the replan hook checks it).
    assert bp.assumed_dominant_objection == "price"

    # offer_logic is REAL-ONLY: price -> a REAL discount code; payment -> the REAL payment
    # offer; an objection with no matching real offer is simply ABSENT (never a None-rule,
    # never invented).
    price = offer_rule_for(bp, "price")
    payment = offer_rule_for(bp, "payment")
    assert price is not None and price.offer_code == "FLOWER15" and price.substantiated is True
    assert payment is not None and payment.offer_code == "SPLIT3"
    assert offer_rule_for(bp, "trust") is None  # no real trust offer -> absent
    assert all(r.substantiated and r.offer_code for r in bp.offer_logic)


def test_blueprint_carries_enriched_spec_fields_grounded_in_the_plan(monkeypatch) -> None:
    # P1-D: brand-voice / research-depth / personalization / do-not-use / success-criteria
    # (and the P1-B exec-discovery fields) are read STRAIGHT from the interview plan onto
    # the executable blueprint — grounded, verbatim, never fabricated.
    monkeypatch.setattr(offers_mod, "get_offers", lambda tid, dsn=None: [])
    plan = CampaignPlan(
        goal="win back lapsed clients",
        channels=["email"],
        output_count=3,
        brand_voice="warm and plain-spoken, never salesy",
        research_depth="deep",
        personalization_rules="reference their style, not personal life",
        do_not_use="no discounts, no emojis",
        success_criteria="5 bookings",
        segment="warm",
        offer_type="booking",
        no_convert_reason="price felt steep",
        prior_contact="we DMed last month",
    )
    bp = build_blueprint(plan, "ladies8391", None, use_llm=False)
    assert bp.brand_voice == "warm and plain-spoken, never salesy"
    assert bp.research_depth == "deep"
    assert bp.personalization_rules == "reference their style, not personal life"
    assert bp.do_not_use == "no discounts, no emojis"
    assert bp.success_criteria == "5 bookings"
    assert bp.segment == "warm"
    assert bp.offer_type == "booking"
    assert bp.no_convert_reason == "price felt steep"
    assert bp.prior_contact == "we DMed last month"


def test_blueprint_spec_fields_are_empty_when_plan_unanswered(monkeypatch) -> None:
    # HONESTY: an unanswered enriched field stays EMPTY on the blueprint (no default made up).
    monkeypatch.setattr(offers_mod, "get_offers", lambda tid, dsn=None: [])
    bp = build_blueprint(CampaignPlan(goal="g", channels=["email"], output_count=1),
                         "t", None, use_llm=False)
    assert bp.brand_voice == "" and bp.research_depth == "" and bp.do_not_use == ""
    assert bp.success_criteria == "" and bp.segment == "" and bp.offer_type == ""


def test_blueprint_never_invents_an_offer_when_none_exist(monkeypatch) -> None:
    # No offers doc -> offer_logic is EMPTY (no None-rules, no invented codes); a lead can
    # never reference a discount.
    monkeypatch.setattr(offers_mod, "get_offers", lambda tid, dsn=None: [])
    bp = build_blueprint(
        CampaignPlan(goal="g", target_category="all", channels=["email"], output_count=3),
        "ladies8391", None, use_llm=False,
    )
    assert bp.offer_logic == []
    from studio.campaign_blueprint import offer_rule_for
    assert offer_rule_for(bp, "price") is None
    # Single channel gets the whole quota.
    assert bp.per_channel_quota == {"email": 3}


def test_blueprint_scope_rules_reflect_plan_scope(monkeypatch) -> None:
    monkeypatch.setattr(offers_mod, "get_offers", lambda tid, dsn=None: [])
    bp = build_blueprint(
        CampaignPlan(goal="g", scope="one artist", channels=["sms"]),
        "t", None, use_llm=False,
    )
    joined = " ".join(bp.artist_shop_rules).lower()
    assert "artist" in joined
    # Compliance + review rules are always present (the gates that hold).
    assert any("approve-first" in c.lower() or "held" in c.lower() for c in bp.compliance_constraints)
    assert any("exactly-once" in c.lower() for c in bp.compliance_constraints)
    # No channels quota when output_count is unset -> honest empty quota.
    assert bp.per_channel_quota == {}


def test_offer_text_never_becomes_a_channel_quota_key(monkeypatch) -> None:
    """The zero-drafts root cause: an offer/CTA leaked into ``plan.channels`` must NEVER
    become a ``per_channel_quota`` key (the team then drafts for a bogus channel and makes
    nothing). The planner keeps ONLY real channels and defaults to email when none remain,
    so a plan always drafts for a real channel — and the offer/CTA stays out of channels."""
    monkeypatch.setattr(offers_mod, "get_offers", lambda tid, dsn=None: [])
    plan = CampaignPlan(
        goal="win back", channels=["reply to book your session"], output_count=3,
        offer="reply to book your session",
    )
    bp = build_blueprint(plan, "t", None, use_llm=False)
    assert "reply to book your session" not in bp.per_channel_quota
    assert bp.per_channel_quota == {"email": 3}
    assert all(k in ("email", "instagram", "facebook", "sms", "gmail")
               for k in bp.per_channel_quota)


# --------------------------------------------------------------------------- #
# Explicit operator count (the "3-draft ask ran the whole roster" defect).
# --------------------------------------------------------------------------- #

def test_parse_explicit_count_is_conservative() -> None:
    # Number words AND digits, but ONLY when they directly modify drafts/emails/
    # messages/leads.
    assert parse_explicit_count("three drafts") == 3
    assert parse_explicit_count("make three drafts only for this campaign") == 3
    assert parse_explicit_count("5 emails") == 5
    assert parse_explicit_count("make 5 emails for the winback") == 5
    assert parse_explicit_count("send 2 messages to warm leads first") == 2
    assert parse_explicit_count("target ten leads") == 10
    # NEVER a price, a bare number, or empty text.
    assert parse_explicit_count("$1200 full-day") is None
    assert parse_explicit_count("a $1200 full-day session offer") is None
    assert parse_explicit_count("$1200 leads") is None  # money never reads as a count
    assert parse_explicit_count("no numbers") is None
    assert parse_explicit_count("") is None
    assert parse_explicit_count(None) is None


def test_explicit_count_in_goal_text_hard_caps_the_quota(monkeypatch) -> None:
    """THE 81-LEAD DEFECT: the operator asked for THREE drafts in the goal text while
    the host LLM was down (revise_plan never landed a numeric plan field) — so the
    uploaded roster must NOT size the run. The explicit count HARD-CAPS total_quota,
    every per-channel quota, and estimated_size, and the cap is recorded honestly on
    the blueprint (requested_count / capped_by) for the Requested reconciliation."""
    monkeypatch.setattr(offers_mod, "get_offers", lambda tid, dsn=None: [])
    plan = CampaignPlan(
        goal="make three drafts only to win back lapsed clients",
        lead_source="provided", channels=["email"],
        customers={"rows": 81, "customer_ids": [f"c{i}" for i in range(81)]},
    )
    bp = build_blueprint(plan, "t", None, use_llm=False)
    assert bp.stop_conditions.total_quota == 3
    assert bp.targets.estimated_size == 3
    assert bp.per_channel_quota == {"email": 3}
    assert sum(bp.per_channel_quota.values()) <= 3
    # Honest record of the cap, serialized with the blueprint.
    assert bp.requested_count == 3
    assert bp.capped_by and "3" in bp.capped_by
    assert bp.capped_by in bp.stop_conditions.notes


def test_numeric_lead_count_field_outranks_free_text(monkeypatch) -> None:
    # Priority (a): the numeric plan field the host sets when its tooling works wins
    # over a count parsed from free text.
    monkeypatch.setattr(offers_mod, "get_offers", lambda tid, dsn=None: [])
    plan = CampaignPlan(
        goal="five emails for the winback", lead_count=2,
        lead_source="provided", channels=["email"],
        customers={"rows": 10, "customer_ids": [f"c{i}" for i in range(10)]},
    )
    bp = build_blueprint(plan, "t", None, use_llm=False)
    assert bp.requested_count == 2
    assert bp.stop_conditions.total_quota == 2
    assert bp.per_channel_quota == {"email": 2}


def test_price_text_never_caps_the_quota(monkeypatch) -> None:
    # "$1200 full-day" is a price, not a requested count — the roster sizing stands.
    monkeypatch.setattr(offers_mod, "get_offers", lambda tid, dsn=None: [])
    plan = CampaignPlan(
        goal="win back lapsed clients with the $1200 full-day offer",
        lead_source="provided", channels=["email"],
        customers={"rows": 10, "customer_ids": [f"c{i}" for i in range(10)]},
    )
    bp = build_blueprint(plan, "t", None, use_llm=False)
    assert bp.requested_count is None
    assert bp.capped_by == ""
    assert bp.stop_conditions.total_quota == 10


def test_explicit_count_above_supply_never_inflates_quota(monkeypatch) -> None:
    # Requested 10 but only 3 uploaded rows: the cap only ever LOWERS the quota
    # (total <= requested), it never invents leads.
    monkeypatch.setattr(offers_mod, "get_offers", lambda tid, dsn=None: [])
    plan = CampaignPlan(
        goal="10 drafts please", lead_source="provided", channels=["email"],
        customers={"rows": 3, "customer_ids": ["c1", "c2", "c3"]},
    )
    bp = build_blueprint(plan, "t", None, use_llm=False)
    assert bp.requested_count == 10
    assert bp.capped_by == ""  # nothing was capped — supply was already below the ask
    assert bp.stop_conditions.total_quota == 3


def test_provided_leads_quota_covers_whole_uploaded_list(monkeypatch) -> None:
    """A provided-leads run drafts one message per uploaded lead: the blueprint quota is
    sized to the REAL uploaded row count, never a smaller stated output_count that would
    clip the fan-out to a stale whole-studio guess (the 3-of-10 bug)."""
    monkeypatch.setattr(offers_mod, "get_offers", lambda tid, dsn=None: [])
    plan = CampaignPlan(
        goal="g", lead_source="provided", channels=["email"], output_count=3,
        customers={"rows": 10, "customer_ids": [f"c{i}" for i in range(10)]},
    )
    bp = build_blueprint(plan, "t", None, use_llm=False)
    assert bp.stop_conditions.total_quota == 10
    assert bp.targets.estimated_size == 10
    assert bp.per_channel_quota == {"email": 10}
