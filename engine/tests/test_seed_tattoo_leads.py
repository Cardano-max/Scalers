"""The seeded mock warm leads are varied + demonstrable (P1 #7).

Runs each seed lead's REAL conversation through the analyst (deterministic) and pins that
the intended objection/category is what the grounded read actually produces — so the demo
data exercises classification + branching, and every read still traces to the lead's own
words (no pre-labeling leaks into the analysis). No DB.
"""

from __future__ import annotations

from studio.psych_profile import (
    CAT_ARTIST,
    CAT_RECURRING,
    CAT_REACTIVATION,
    analyze_customer,
)
from studio.seed_tattoo_leads import SEED_LEADS


def _lead_by_email(email: str) -> dict:
    return next(l for l in SEED_LEADS if l["email"] == email)


def _facts(lead: dict) -> dict:
    return {
        "customer_id": "cust_" + lead["email"].split("@")[0],
        "name": lead["name"], "email": lead["email"],
        "interests": lead.get("interests", []),
        "artist": lead.get("artist"), "customer_type": lead.get("customer_type"),
        "persona_traits": lead.get("persona_traits", {}),
        "tattoo_history": lead.get("tattoo_history", []),
    }


def test_seed_has_varied_objections_and_categories():
    reads = {}
    for lead in SEED_LEADS:
        prof = analyze_customer(_facts(lead), lead["turns"], use_llm=False)
        reads[lead["email"]] = (prof.umbrella_category.value, prof.primary_objection.value)
    objections = {v[1] for v in reads.values()}
    # The demo exercises several distinct objection reads, not one repeated bucket.
    assert {"price", "trust", "timing", "payment"} <= objections


def test_sarah_reads_price_grounded():
    lead = _lead_by_email("sarah.kim@example.com")
    prof = analyze_customer(_facts(lead), lead["turns"], use_llm=False)
    assert prof.umbrella_category.value == CAT_ARTIST
    assert prof.primary_objection.value == "price"
    assert "short on budget" in prof.primary_objection.evidence.lower()


def test_priya_reads_trust_grounded():
    lead = _lead_by_email("priya.anand@example.com")
    prof = analyze_customer(_facts(lead), lead["turns"], use_llm=False)
    assert prof.primary_objection.value == "trust"
    assert prof.trust_level.value == "low"


def test_aisha_reads_payment_grounded():
    lead = _lead_by_email("aisha.bello@example.com")
    prof = analyze_customer(_facts(lead), lead["turns"], use_llm=False)
    assert prof.primary_objection.value == "payment"


def test_recurring_and_reactivation_categories():
    mel = analyze_customer(_facts(_lead_by_email("mel.carter@example.com")),
                           _lead_by_email("mel.carter@example.com")["turns"], use_llm=False)
    assert mel.umbrella_category.value == CAT_RECURRING
    jess = analyze_customer(_facts(_lead_by_email("jess.lowe@example.com")),
                            _lead_by_email("jess.lowe@example.com")["turns"], use_llm=False)
    assert jess.umbrella_category.value == CAT_REACTIVATION
