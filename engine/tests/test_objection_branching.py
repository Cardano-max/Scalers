"""Category + objection branching in the draft (P1 #3) + the notes fix.

When a grounded psych profile is present, the lead's OBJECTION leads the angle and a
discount is referenced ONLY when a REAL substantiated offer is passed — never invented.
Existing no-profile behavior is untouched (covered by test_personalization_angle).

Deterministic copy path (SCALERS_OUTREACH_LLM=0) — no model, no DB.
"""

from __future__ import annotations


from studio.customer_research import _choose_angle, build_outreach_draft
from studio.offers import parse_offers_doc, select_offer, substantiate, _SEED_OFFERS
from studio.psych_profile import analyze_customer
from studio.reason_history import parse_conversation_text

_SAMPLE = (
    "Customer: I wanted a small fine-line floral on my wrist. / Studio: fine-line or bold? / "
    "Customer: Fine-line. How much? / Studio: ~$120-$180. / "
    "Customer: I like it but maybe later, short on budget right now. / Studio: ok!"
)


def _facts(**kw):
    base = {"customer_id": "c1", "name": "Sarah", "email": "s@x.com", "email_opt_in": True,
            "persona_traits": {}, "interests": [], "tattoo_history": []}
    base.update(kw)
    return base


def test_price_objection_routes_to_real_offer_discount(monkeypatch):
    monkeypatch.setenv("SCALERS_OUTREACH_LLM", "0")
    conv = parse_conversation_text(_SAMPLE)
    profile = analyze_customer(_facts(), conv, use_llm=False)
    offers = parse_offers_doc(_SEED_OFFERS)
    chosen = select_offer(offers, objection="price", interest="fine-line floral")
    assert chosen is not None  # FLOWER15 exists

    draft = build_outreach_draft(_facts(), goal="win back", channel="gmail",
                                 profile=profile, offer=chosen)
    # The angle is objection/offer-driven, grounded, and the grounding records the real
    # objection + the real offer code (never a fabricated discount).
    assert draft["angle_key"] == "offer-discount"
    assert "objection=price" in draft["grounding"]
    assert f"offer={chosen.code}" in draft["grounding"]
    # The deterministic copy mentions the REAL code, not an invented percentage/code.
    assert chosen.code in draft["draft"]


def test_price_objection_without_offer_invents_no_discount(monkeypatch):
    monkeypatch.setenv("SCALERS_OUTREACH_LLM", "0")
    conv = parse_conversation_text(_SAMPLE)
    profile = analyze_customer(_facts(), conv, use_llm=False)

    draft = build_outreach_draft(_facts(), goal="win back", channel="gmail",
                                 profile=profile, offer=None)
    assert draft["angle_key"] == "addressing-price"
    # No offer -> no fabricated code/percentage anywhere in the copy.
    body = draft["draft"].lower()
    assert "%" not in body
    assert "code" not in body or "flower" not in body
    assert "offer=" not in " ".join(draft["grounding"])


def test_substantiation_gate_is_the_source_of_the_offer():
    """A draft's offer must come through the substantiation gate — a fabricated code
    never yields an Offer to pass in."""
    offers = parse_offers_doc(_SEED_OFFERS)
    assert substantiate(offers, "TOTALLY_FAKE") is None


def test_notes_fix_makes_csv_note_angle_live(monkeypatch):
    monkeypatch.setenv("SCALERS_OUTREACH_LLM", "0")
    # No profile -> base ranking; a lead whose only signal is a note now reaches the
    # csv-note angle (previously dead because notes was never populated).
    lead = _facts(name="Note Lead", notes="asked about a cover-up last spring")
    angle = _choose_angle(lead, [])
    assert angle["key"] == "csv-note"
    assert "cover-up" in angle["basis"]
