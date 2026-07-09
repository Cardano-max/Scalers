"""Studio offers wiring for 65w.14 (CustomerAcq-ju1.2): seed offers never substantiate.

Trunk landed the primitive (``cells.offer_guard``) + draft-cell wiring; this pins the
STUDIO side: ``get_offers`` stamps doc provenance onto every ``Offer``, ``select_offer``
and ``substantiate`` refuse a seed/mock-sourced offer, and the provided-leads staging
path runs ``offer_violations`` over the built copy so a fabricated code (the audit's
ARTLOVER) is skipped honestly instead of reaching the pending queue. DB-free, hermetic.
"""

from __future__ import annotations

from cells.offer_guard import SubstantiatedOffer
from studio.offers import (
    Offer,
    as_substantiated,
    get_offers,
    select_offer,
    substantiate,
)

_SEED_KW = {"source": "seed", "doc_id": "doc_seed_ladies8391_offers_mock"}
_REAL_KW = {"source": "operator", "doc_id": "doc_offers_ladies8391_2026q3"}


def _offer(code: str, kind: str = "discount", **kw) -> Offer:
    return Offer(code=code, discount="15%", kind=kind, **kw)


# ── select_offer: a seed offer is never selected ──────────────────────────────


def test_seed_offer_is_never_selected():
    # Bead wiring test 1: the ONLY objection-matching offer traces to the seed doc ->
    # select_offer returns None (never a seed-substantiated angle), not the seed offer.
    assert select_offer([_offer("FLOWER15", **_SEED_KW)], objection="price") is None


def test_real_offer_still_selected_over_seed():
    seed, real = _offer("FLOWER15", **_SEED_KW), _offer("INKED10", **_REAL_KW)
    chosen = select_offer([seed, real], objection="price")
    assert chosen is not None and chosen.code == "INKED10"


def test_unstamped_offer_stays_selectable():
    # Hand-built offers (tests, operator tooling) carry no provenance -> still real,
    # matching SubstantiatedOffer semantics (None source/doc_id == real).
    assert select_offer([_offer("HAND10")], objection="price") is not None


# ── substantiate: the hard gate refuses a seed offer ──────────────────────────


def test_substantiate_refuses_seed_offer():
    assert substantiate([_offer("FLOWER15", **_SEED_KW)], "FLOWER15") is None


def test_substantiate_still_returns_real_offer():
    got = substantiate([_offer("FLOWER15", **_REAL_KW)], "flower15")
    assert got is not None and got.code == "FLOWER15"


# ── get_offers: provenance is stamped from the doc row ────────────────────────


def test_get_offers_stamps_doc_provenance(monkeypatch):
    import studio.documents as documents

    doc_id = "doc_seed_t1_offers_mock"
    monkeypatch.setattr(
        documents, "list_documents",
        lambda tenant, active_only=True, dsn=None: [
            {"id": doc_id, "kind": "offers", "source": "seed"}
        ],
    )
    monkeypatch.setattr(
        documents, "get_document",
        lambda i, dsn=None: {"id": doc_id, "content": "- code: FLOWER15 | discount: 15%"},
    )
    offers = get_offers("t1")
    assert offers and offers[0].source == "seed" and offers[0].doc_id == doc_id
    # The stamped seed offer then fails BOTH substantiation gates.
    assert select_offer(offers, objection="price") is None
    assert substantiate(offers, "FLOWER15") is None


# ── as_substantiated: the offer_guard view used by the staging gate ───────────


def test_as_substantiated_carries_provenance_and_percent():
    view = as_substantiated([_offer("FLOWER15", **_SEED_KW), _offer("INKED10", **_REAL_KW)])
    assert all(isinstance(o, SubstantiatedOffer) for o in view)
    seed, real = view
    assert seed.is_real is False and real.is_real is True
    assert real.percent_off == 15  # "15%" -> 15, so "15% off" copy substantiates


# ── staging gate: fabricated offer copy is skipped, never staged (ARTLOVER) ───


def test_provided_leads_skips_fabricated_offer_copy(monkeypatch):
    # Bead wiring test 1 (staging site): with NO real offers on file, a draft whose copy
    # carries the audit's fabricated code must be SKIPPED with a concrete reason — it
    # never reaches record_pending_action (the pending queue the audit found it in).
    import actions.store as store_mod
    import studio.customer_research as cr
    from studio.agui import _execute_provided_leads_sync

    from tests.test_provided_leads_real_team import _plan, _wire

    _wire(monkeypatch)
    staged: list[str] = []
    monkeypatch.setattr(
        store_mod, "record_pending_action",
        lambda **kw: (staged.append(kw["idempotency_key"]) or f"act_{kw['idempotency_key']}"),
    )

    def _fabricating_draft(facts, *, goal="", **kw):
        return {
            "channel": "gmail", "target": f"{facts['customer_id']}@lead.example",
            "subject": "Fresh ink season",
            "draft": "Fresh ink season - use code ARTLOVER to get 15% off your booking",
            "grounding": [], "customer_id": facts["customer_id"],
            "copy_model": "grounded_template",
        }

    monkeypatch.setattr(cr, "build_outreach_draft", _fabricating_draft)
    summary = _execute_provided_leads_sync(_plan(), "sess1", "ladies8391", None, None)

    assert not staged, "fabricated-offer draft reached the pending queue"
    skipped = summary["output_ledger"]["skipped"]
    reasons = " | ".join(s.get("reason", "") for s in skipped)
    assert "ARTLOVER" in reasons
    assert len(skipped) == 2  # both leads' drafts carried the fabricated code
    assert summary["n_pending"] == 0
