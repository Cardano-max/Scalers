"""Offers source + SUBSTANTIATION GATE (P1 #6).

Pins the no-fabricated-discount guarantee: an offer is referenced ONLY when it exists in
the offers source; an invented code fails the gate; and objection-driven selection picks
a REAL matching offer or None (never invents one).

Pure parse/selection tests (no DB); the seed doc is parsed directly.
"""

from __future__ import annotations

from studio.offers import (
    KIND_DISCOUNT,
    KIND_PAYMENT,
    _SEED_OFFERS,
    parse_offers_doc,
    select_offer,
    substantiate,
)


def test_parses_seed_offers_doc():
    offers = parse_offers_doc(_SEED_OFFERS)
    codes = {o.code for o in offers}
    assert {"FLOWER15", "FLASHFRIDAY", "SPLIT3", "TOUCHUP1", "WELCOMEBACK"} <= codes
    flower = next(o for o in offers if o.code == "FLOWER15")
    assert flower.discount == "15%" and flower.kind == KIND_DISCOUNT
    assert "floral" in flower.applies_to


def test_substantiation_gate_blocks_fabricated_allows_real():
    offers = parse_offers_doc(_SEED_OFFERS)
    # An invented code fails closed — a draft may NOT reference it.
    assert substantiate(offers, "FAKE50") is None
    assert substantiate(offers, "") is None
    # A real seeded code substantiates (case-insensitive).
    real = substantiate(offers, "flower15")
    assert real is not None and real.code == "FLOWER15"


def test_price_objection_selects_real_discount_matching_interest():
    offers = parse_offers_doc(_SEED_OFFERS)
    chosen = select_offer(offers, objection="price", interest="fine-line floral")
    assert chosen is not None and chosen.code == "FLOWER15"
    # Payment objection routes to the real payment-split offer, not a discount.
    pay = select_offer(offers, objection="payment", interest="sleeve")
    assert pay is not None and pay.kind == KIND_PAYMENT and pay.code == "SPLIT3"


def test_no_offers_means_no_offer_never_invented():
    assert select_offer([], objection="price", interest="floral") is None
    assert select_offer(parse_offers_doc(_SEED_OFFERS), objection=None) is None
    # An objection with no matching real offer returns None (fallback to non-offer angle).
    assert select_offer(
        [o for o in parse_offers_doc(_SEED_OFFERS) if o.kind == KIND_PAYMENT],
        objection="trust",
    ) is None
