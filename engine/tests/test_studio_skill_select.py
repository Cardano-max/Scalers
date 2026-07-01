"""Per-lead SKILL selection (P2-B, CustomerAcq-65w.6): the dossier routes to the RIGHT
first-party marketing play, deterministically, with an honest reason. No skill pack is
loaded/executed (registry-gated) — ``aligned_pack`` is a labeled pointer only."""

from __future__ import annotations

from studio.dossier import Dossier, DossierField, build_dossier
from studio.skill_select import select_skill


def _dossier(*, objection=None, obj_signal="stated", segment=None, interest=None,
            angle_generic=False):
    facts = {"customer_id": "c", "name": "Lead", "email": "l@x.com",
             "interests": [interest] if interest else [], "persona_traits": {},
             "customer_type": segment, "tattoo_history": []}
    from types import SimpleNamespace as NS
    profile = None
    if objection:
        profile = NS(primary_objection=NS(value=objection, signal=obj_signal,
                                          evidence="they said so"),
                     umbrella_category=NS(value="", signal=""),
                     had_conversation=True, where_customer_sits="considering",
                     source="deterministic")
    return build_dossier(
        facts, profile=profile,
        angle={"label": "x", "key": "addressing-price" if objection else "generic",
               "generic": angle_generic, "inferred": False},
        channel="gmail", cta_kind="reply-based",
    )


def test_price_objection_routes_to_objection_recovery():
    sel = select_skill(_dossier(objection="price"))
    assert sel.skill_id == "objection-recovery"
    assert "price" in sel.why
    assert sel.aligned_pack == "marketing_playbook"
    assert "not loaded" in sel.pack_status


def test_payment_objection_also_routes_to_objection_recovery():
    assert select_skill(_dossier(objection="payment")).skill_id == "objection-recovery"


def test_quiet_past_customer_routes_to_re_engagement():
    sel = select_skill(_dossier(segment="past customer"))
    assert sel.skill_id == "re-engagement"
    assert sel.aligned_pack == "growth_marketing_patterns"


def test_recurring_regular_routes_to_loyalty():
    assert select_skill(_dossier(segment="recurring regular")).skill_id == "loyalty-touchup"


def test_cold_lead_routes_to_warm_intro():
    assert select_skill(_dossier(segment="cold lead")).skill_id == "warm-intro"


def test_interest_only_routes_to_shared_craft():
    sel = select_skill(_dossier(interest="fine-line floral"))
    assert sel.skill_id == "shared-craft"


def test_no_signal_defaults_to_warm_intro():
    d = Dossier(name=DossierField(value="Nobody", confidence="high", source="db:customers.name"))
    assert select_skill(d).skill_id == "warm-intro"


def test_objection_beats_segment_deterministically():
    # A price objection on a recurring regular still leads with objection recovery, and the
    # result is stable across repeated calls (deterministic, keyless).
    d = _dossier(objection="price", segment="recurring regular")
    first = select_skill(d)
    assert first.skill_id == "objection-recovery"
    assert select_skill(d).model_dump() == first.model_dump()
