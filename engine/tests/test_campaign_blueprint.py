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
