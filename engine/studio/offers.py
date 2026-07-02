"""Offers source + SUBSTANTIATION GATE — the only place a real discount can come from.

A tattoo campaign that answers a price objection with "here's 15% off" must reference a
REAL offer, never an invented discount/code/percentage (FTC substantiation; the
project's no-fabrication gate). This module is that guarantee:

  * Offers live in a tenant doc of ``kind="offers"`` in the persistent doc store (reuse
    of :mod:`studio.documents`), so the operator uploads a real offers doc later that
    REPLACES the seeded mock — the workflow is identical.
  * :func:`parse_offers_doc` reads that doc into structured :class:`Offer` records.
  * :func:`select_offer` picks the best REAL offer for an objection + interest, or
    returns ``None`` (never fabricates one). :func:`substantiate` is the hard gate a
    draft passes any offer reference through — an unknown code fails.

If no offers doc exists, every read is honestly empty and the strategy falls back to a
non-offer angle or a reply-based CTA — it does NOT invent a discount.

65w.14 (anti-fabrication, wired by CustomerAcq-ju1.2): a SEED/MOCK offers doc never
substantiates. Every :class:`Offer` carries its doc provenance; :func:`select_offer`
and :func:`substantiate` refuse an offer whose source fails
:func:`cells.offer_guard.is_real_offer_source` — so with only the seeded mock on file,
selection and the hard gate both return ``None`` (fail-closed), exactly like the trunk
draft cell.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from cells.offer_guard import SubstantiatedOffer, is_real_offer_source

# Offer kinds (mirrors ADR §4.2 offers.kind). ``discount`` and ``payment`` are the two
# an objection-driven angle can substantiate; the rest are surfaced as-is.
KIND_DISCOUNT = "discount"
KIND_PAYMENT = "payment"
KIND_FLASH = "flash"
KIND_TOUCH_UP = "touch_up"
KIND_LOYALTY = "loyalty"
KIND_SLOTS = "slots"

OFFERS_DOC_KIND = "offers"


@dataclass
class Offer:
    """One substantiated offer — the real thing a draft is allowed to reference."""

    code: str
    description: str = ""
    discount: str | None = None
    valid_until: str | None = None
    applies_to: list[str] = field(default_factory=list)
    kind: str = KIND_DISCOUNT
    # Doc provenance (65w.14): which offers doc this came from. ``source='seed'`` (or a
    # doc_seed_* id) makes the offer non-substantiating; None (hand-built) counts as real.
    source: str | None = None
    doc_id: str | None = None

    @property
    def is_real(self) -> bool:
        """True iff this offer traces to a REAL, operator-provided offers doc."""
        return is_real_offer_source(self.source, self.doc_id)

    def as_evidence(self) -> str:
        """A compact, verifiable one-liner the grounding audit + evidence panel show."""
        bits = [self.code]
        if self.discount:
            bits.append(self.discount)
        if self.applies_to:
            bits.append("for " + ", ".join(self.applies_to))
        if self.valid_until:
            bits.append(f"valid until {self.valid_until}")
        return " — ".join([bits[0], " ".join(bits[1:]).strip()]).strip(" —") if len(bits) > 1 else bits[0]


# --------------------------------------------------------------------------- #
# Parsing — a tolerant, structured bullet format (also the seed's shape):
#   - code: FLOWER15 | description: ... | discount: 15% | valid_until: 2026-08-31 |
#     applies_to: fine-line, floral | kind: discount
# --------------------------------------------------------------------------- #
_FIELD_ALIASES = {
    "code": "code", "offer": "code", "coupon": "code",
    "description": "description", "desc": "description", "details": "description",
    "discount": "discount", "amount": "discount", "value": "discount",
    "valid_until": "valid_until", "valid until": "valid_until", "expires": "valid_until",
    "applies_to": "applies_to", "applies to": "applies_to", "for": "applies_to",
    "kind": "kind", "type": "kind",
}


def parse_offers_doc(
    content: str, *, source: str | None = None, doc_id: str | None = None
) -> list[Offer]:
    """Parse an offers doc into :class:`Offer` records. Tolerant of markdown bullets and
    blank lines; a line with no ``code`` is skipped (an offer with no code cannot be
    substantiated, so it is not surfaced). ``source``/``doc_id`` stamp the originating
    doc's provenance onto every offer (65w.14). Pure — no I/O, unit-testable."""
    offers: list[Offer] = []
    for raw_line in (content or "").splitlines():
        line = raw_line.strip().lstrip("-*").strip()
        if not line or "|" not in line and ":" not in line:
            continue
        fields: dict[str, Any] = {}
        for part in line.split("|"):
            if ":" not in part:
                continue
            k, _, v = part.partition(":")
            key = _FIELD_ALIASES.get(k.strip().lower())
            val = v.strip()
            if not key or not val:
                continue
            if key == "applies_to":
                fields[key] = [s.strip() for s in val.replace(",", ";").split(";") if s.strip()]
            else:
                fields[key] = val
        code = (fields.get("code") or "").strip()
        if not code:
            continue
        offers.append(Offer(
            code=code,
            description=fields.get("description", ""),
            discount=fields.get("discount"),
            valid_until=fields.get("valid_until"),
            applies_to=fields.get("applies_to", []),
            kind=(fields.get("kind") or KIND_DISCOUNT).strip().lower(),
            source=source,
            doc_id=doc_id,
        ))
    return offers


# --------------------------------------------------------------------------- #
# Reads over the doc store.
# --------------------------------------------------------------------------- #
def get_offers(tenant_id: str, *, dsn: str | None = None) -> list[Offer]:
    """All substantiated offers for the tenant, parsed from active ``kind='offers'`` docs.

    Honest-empty (``[]``) when there is no offers doc — the caller then falls back to a
    non-offer angle rather than inventing a discount. Best-effort: a store hiccup yields
    ``[]``, never a fabricated offer."""
    try:
        from studio.documents import get_document, list_documents

        docs = [d for d in list_documents(tenant_id, active_only=True, dsn=dsn)
                if (d.get("kind") or "").lower() == OFFERS_DOC_KIND]
        offers: list[Offer] = []
        for d in docs:
            full = get_document(d["id"], dsn=dsn)
            if full and full.get("content"):
                offers.extend(parse_offers_doc(
                    full["content"], source=d.get("source"), doc_id=d.get("id"),
                ))
        return offers
    except Exception:
        return []


# --------------------------------------------------------------------------- #
# Selection + the SUBSTANTIATION GATE.
# --------------------------------------------------------------------------- #
# Which offer kinds legitimately answer which objection (only a REAL match is used).
_OBJECTION_KINDS = {
    "price": (KIND_DISCOUNT, KIND_FLASH),
    "payment": (KIND_PAYMENT,),
    "timing": (KIND_FLASH, KIND_SLOTS),
    "recurring": (KIND_TOUCH_UP, KIND_LOYALTY),
    "reactivation": (KIND_DISCOUNT, KIND_LOYALTY),
}


def _interest_match(offer: Offer, interest: str | None) -> bool:
    """True when the offer either applies to everything (no applies_to) or overlaps the
    lead's interest/style — so a floral lead is not handed a 'sleeve day' promo."""
    if not offer.applies_to:
        return True
    if not interest:
        return True
    low = interest.lower()
    return any(low in a.lower() or a.lower() in low for a in offer.applies_to)


def select_offer(
    offers: list[Offer], *, objection: str | None, interest: str | None = None,
) -> Offer | None:
    """The best REAL offer for this objection + interest, or ``None`` (never invented).

    Prefers an offer whose kind answers the objection AND whose ``applies_to`` matches the
    lead's interest; falls back to any objection-appropriate offer; returns ``None`` when
    no real offer fits — the caller must then NOT reference a discount. A seed/mock-doc
    offer is never a candidate (65w.14: seed sources never substantiate)."""
    if not offers or not objection:
        return None
    kinds = _OBJECTION_KINDS.get(objection.strip().lower())
    if not kinds:
        return None
    candidates = [o for o in offers if o.kind in kinds and o.is_real]
    if not candidates:
        return None
    matched = [o for o in candidates if _interest_match(o, interest)]
    return (matched or candidates)[0]


def substantiate(offers: list[Offer], code: str | None) -> Offer | None:
    """The HARD GATE: return the real :class:`Offer` for ``code`` iff it exists in the
    offers source, else ``None``. A draft may reference an offer ONLY when this returns a
    non-None offer — an invented/unknown code fails closed and must not reach a draft.
    A seed/mock-doc offer never substantiates (65w.14), even by exact code match."""
    if not code:
        return None
    want = code.strip().lower()
    for o in offers:
        if o.code.strip().lower() == want and o.is_real:
            return o
    return None


_DISCOUNT_PCT_RE = re.compile(r"(\d{1,3})\s*%")


def as_substantiated(offers: list[Offer]) -> list[SubstantiatedOffer]:
    """The :mod:`cells.offer_guard` view of these offers, provenance included, so a
    studio call site can run :func:`cells.offer_guard.offer_violations` over built copy
    in one line. ``percent_off`` is parsed from ``discount`` (e.g. ``"15%"`` → 15) so a
    real offer's "15% off" copy substantiates; seed offers contribute nothing there."""
    view: list[SubstantiatedOffer] = []
    for o in offers:
        m = _DISCOUNT_PCT_RE.search(o.discount or "")
        view.append(SubstantiatedOffer(
            code=o.code, doc_id=o.doc_id, source=o.source,
            percent_off=int(m.group(1)) if m else None,
        ))
    return view


# --------------------------------------------------------------------------- #
# Seed — a realistic MOCK offers doc so the workflow runs end-to-end now. The operator
# uploads a REAL offers doc later (same kind='offers') that REPLACES this.
# --------------------------------------------------------------------------- #
_SEED_OFFERS = """# Ladies First — Active Offers (MOCK; replace with your real offers doc)

- code: FLOWER15 | description: 15% off a fine-line or floral piece for returning inquiries | discount: 15% | valid_until: 2026-09-30 | applies_to: fine-line, floral, small piece | kind: discount
- code: FLASHFRIDAY | description: Walk-in flash-day pricing on pre-drawn small designs | discount: flash-day pricing | valid_until: 2026-08-31 | applies_to: flash, small piece | kind: flash
- code: SPLIT3 | description: Split a larger piece into 3 interest-free payments | discount: 3 interest-free payments | valid_until: 2026-12-31 | applies_to: sleeve, back piece, large | kind: payment
- code: TOUCHUP1 | description: One complimentary touch-up within 6 months for returning clients | discount: free touch-up | valid_until: 2026-12-31 | applies_to: any | kind: touch_up
- code: WELCOMEBACK | description: 10% welcome-back for clients we have not seen in over a year | discount: 10% | valid_until: 2026-12-31 | applies_to: any | kind: loyalty
"""

_SEED_OFFERS_NAME = "Ladies First — Active Offers (mock)"


def _seed_doc_id(tenant_id: str) -> str:
    return f"doc_seed_{tenant_id}_offers_mock"


def seed_offers_doc(tenant_id: str, *, dsn: str | None = None) -> str | None:
    """Best-effort seed of the MOCK offers doc as an active ``kind='offers'`` document.

    Idempotent (deterministic id + ``ON CONFLICT DO NOTHING``). Returns the doc id, or
    ``None`` if the doc store is unavailable (honest — never fabricates)."""
    try:
        from studio.documents import add_document

        return add_document(
            tenant_id, _SEED_OFFERS_NAME, _SEED_OFFERS,
            kind=OFFERS_DOC_KIND, source="seed", doc_id=_seed_doc_id(tenant_id), dsn=dsn,
        )
    except Exception:
        return None
