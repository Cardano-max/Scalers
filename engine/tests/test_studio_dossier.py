"""Per-lead DOSSIER (P2-C, CustomerAcq-65w.7): every field traces to a REAL source or is
honestly empty; thin data flips ``limited_personalization`` instead of inventing specifics."""

from __future__ import annotations

from types import SimpleNamespace as NS

from studio.dossier import build_dossier


def _field(value, signal, evidence=""):
    return NS(value=value, signal=signal, evidence=evidence, evidence_source="conversation")


def _profile(objection="price"):
    return NS(
        primary_objection=_field(objection, "stated", "it's a bit out of my budget"),
        umbrella_category=_field("past-customer-reactivation", "inferred"),
        readiness_stage=_field("preference", "inferred"),
        had_conversation=True, where_customer_sits="considering, hesitant on price",
        best_reengagement_angle="warm win-back", source="deterministic",
    )


_RICH = {
    "customer_id": "cust_1", "name": "Sarah Kim", "email": "sarah.kim@gmail.com",
    "phone": "+15551234567", "ig_handle": "@sarah.ink", "city": "Austin",
    "interests": ["fine-line floral"], "customer_type": "warm lead",
    "persona_traits": {"aesthetic_lean": "delicate"}, "artist": "Mia",
    "tattoo_history": [{"style": "fine-line"}],
}


def test_rich_lead_every_field_traces_to_a_real_source():
    d = build_dossier(
        _RICH, profile=_profile("price"),
        angle={"label": "their price hesitation", "key": "addressing-price",
               "generic": False, "inferred": False},
        channel="gmail", cta_kind="reply-based", evidence_used=["name=Sarah Kim"],
        run_id="team-x",
    )
    assert d.name.value == "Sarah Kim" and d.name.source == "db:customers.name"
    assert d.email.value == "sarah.kim@gmail.com" and d.email.confidence == "high"
    assert d.phone.value == "+15551234567"
    assert d.social_handle.value == "@sarah.ink"
    assert d.customer_type.value == "warm lead"
    assert d.tattoo_interest.value == "fine-line floral"
    assert d.artist_style_match.confidence == "high"  # artist + past style on file
    # Grounded objection: high confidence + a verbatim evidence span (never invented).
    assert d.likely_objection.value == "price"
    assert d.likely_objection.confidence == "high"
    assert "budget" in d.objection_evidence
    assert d.conversation_summary.present  # had a real conversation
    assert d.best_angle.value == "their price hesitation"
    assert d.recommended_cta.value  # a concrete next step
    assert d.limited_personalization is False
    assert d.customer_id == "cust_1" and d.run_id == "team-x"


def test_thin_lead_flags_limited_personalization_never_fabricates():
    thin = {"customer_id": "cust_2", "name": "Cold Lead A", "email": "colda@gmail.com",
            "interests": [], "persona_traits": {}, "tattoo_history": []}
    d = build_dossier(
        thin, profile=None,
        angle={"label": "an honest general introduction", "key": "generic",
               "generic": True, "inferred": False},
        channel="gmail", cta_kind="reply-based",
    )
    assert d.limited_personalization is True
    assert d.personalization_note  # says so honestly
    # Nothing invented: no interest, no objection, no history.
    assert not d.tattoo_interest.present
    assert not d.likely_objection.present
    assert not d.conversation_summary.present
    assert d.name.value == "Cold Lead A"  # but real identity is still carried


def test_inferred_persona_signal_is_medium_confidence_not_high():
    facts = {"customer_id": "c3", "name": "Priya", "email": "p@x.com",
             "interests": [], "persona_traits": {"aesthetic_lean": "blackwork",
             "lifecycle_stage": "lapsing"}, "tattoo_history": []}
    d = build_dossier(facts, profile=None,
                      angle={"label": "a blackwork aesthetic lean", "key": "shared-craft",
                             "generic": False, "inferred": True})
    assert d.tattoo_interest.confidence == "medium"  # persona-inferred, not a hard CSV fact
    assert d.customer_type.confidence == "medium"    # from persona lifecycle, not a column
    assert d.best_angle.confidence == "medium"       # inferred angle
