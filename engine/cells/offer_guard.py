"""Anti-fabrication offer guard (CustomerAcq-65w.14, audit-2026-07-02, customer-safety).

The flagship promise: a draft may only ever cite an offer/discount the operator
actually authorized. The 2026-07-02 audit found it defeated twice on the live
path: a FABRICATED code ("use code ARTLOVER to get 15% off") reached the pending
queue without any offers source, and every "substantiated" code traced to a
SEEDED MOCK offers doc (``source='seed'``, id ``doc_seed_*``). Two deterministic
controls close both holes:

* :func:`is_real_offer_source` — a seed/mock/demo offers doc NEVER substantiates
  a live draft (the same posture the publish path takes for mock connectors). A
  draft may only cite an offer tracing to a non-seed, operator-provided doc.
* :func:`find_offer_tokens` + :func:`no_unsubstantiated_offers` /
  :func:`offer_violations` — a HARD post-generation validator: any discount-code
  / percent-off / promo token in draft copy must match a substantiated REAL
  offer or the draft blocks. This is a Gate-class ERROR enforced in the cell's
  repair loop (LLM path) and exposed as a pure check for Check&Score / the
  deterministic path — not advisory prompt text, which is what ARTLOVER walked
  straight past.

Fail-closed default: with NO real offers doc for the tenant, drafts must contain
no offer/discount language at all (not a mock one).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from cells.validators import FieldValidator, Severity, ValidationIssue

# Offers-doc sources that must NEVER substantiate a live draft.
SEED_SOURCE_VALUES = frozenset({"seed", "mock", "fixture", "demo"})
SEED_DOC_PREFIXES = ("doc_seed_", "doc_mock_", "seed_", "mock_")


def is_real_offer_source(source: str | None, doc_id: str | None = None) -> bool:
    """True iff an offers doc is a REAL, operator-provided source.

    ``source='seed'`` (or mock/fixture/demo) and ``doc_seed_*``-style ids are
    non-substantiating for live drafting — they exist so the pipeline runs before
    onboarding, never so a client-facing draft can cite them."""
    if source is not None and source.strip().lower() in SEED_SOURCE_VALUES:
        return False
    if doc_id is not None and doc_id.strip().lower().startswith(SEED_DOC_PREFIXES):
        return False
    return True


@dataclass(frozen=True)
class SubstantiatedOffer:
    """One operator-authorized offer as loaded from an offers doc."""

    code: str                    # e.g. "FLOWER15" (matched case-insensitively)
    doc_id: str | None = None    # provenance: which offers doc it came from
    source: str | None = None    # provenance: doc source ('seed' -> never real)
    percent_off: int | None = None

    @property
    def is_real(self) -> bool:
        return is_real_offer_source(self.source, self.doc_id)


def substantiated_codes(offers: Iterable[SubstantiatedOffer]) -> frozenset[str]:
    """Uppercased codes of the REAL offers only — seed/mock offers contribute nothing."""
    return frozenset(o.code.strip().upper() for o in offers if o.is_real and o.code.strip())


def substantiated_percents(offers: Iterable[SubstantiatedOffer]) -> frozenset[int]:
    return frozenset(o.percent_off for o in offers if o.is_real and o.percent_off is not None)


# ── token detection ───────────────────────────────────────────────────────────

# Percent-off in BOTH shapes (qa1 adversarial re-QA fix):
#   (a) percent followed by off/discount, admitting space/hyphen/nothing:
#       "15% off", "15 % off", "15%off", "15%-off", "15 percent discount";
#   (b) a promo verb BEFORE a bare percent — the save/get family carries the same
#       fabrication with no trailing "off": "save 15% on your first session",
#       "get 20% today", "save 15 percent this month".
# A bare percent with neither shape ("100% custom designs") stays clean.
_PERCENT_RE = re.compile(
    r"\b(\d{1,3})\s*(?:%|percent)[\s-]*(?:off|discount)\b"
    r"|\b(?:save|get|enjoy|take|claim|unlock|score)\s+(\d{1,3})\s*(?:%|percent\b)",
    re.IGNORECASE,
)
# Code tokens, two arms (qa1 fix):
#   (a) the literal word "code" before the token, any case ("use code artlover");
#   (b) promo/coupon/voucher + a CODE-SHAPED token WITHOUT the word "code"
#       ("use promo FLOWER15"). Code-shaped = contains a digit or is ALL-CAPS >=3,
#       so "promo runs friday" never false-positives on a common word.
_CODE_RE = re.compile(
    r"\b(?i:(?:promo\s+|discount\s+|offer\s+|coupon\s+|voucher\s+)?code)\s+[\"']?([A-Za-z0-9]{3,20})[\"']?"
    r"|\b(?i:promo|coupon|voucher)\s+[\"']?([A-Za-z]*\d[A-Za-z0-9]{0,19}|[A-Z]{3,20})\b[\"']?",
)
# Offer-implying lexicon: naming these implies an authorized offer exists at all.
# Deliberately NOT including bare "free" (e.g. the approved "free consultation"
# claim is not an offer token) — pricing talk in general is the per-tenant
# ban_lexicon's job; this guard is specifically about offers/discounts.
_LEXICON_RE = re.compile(
    r"\b(promo\s+code|coupon|voucher|discount(?:ed|s)?)\b", re.IGNORECASE
)


def find_offer_tokens(text: str) -> list[str]:
    """Every offer/discount token in ``text``, normalized.

    Codes come back uppercased (``ARTLOVER``); percent-off as ``15% off``;
    lexicon hits lowercased (``discount``). Order of appearance, deduped."""
    tokens: list[str] = []
    for m in _CODE_RE.finditer(text):
        tok = m.group(1) or m.group(2)
        if tok:
            tokens.append(tok.upper())
    for m in _PERCENT_RE.finditer(text):
        pct = m.group(1) or m.group(2)
        tokens.append(f"{int(pct)}% off")
    for m in _LEXICON_RE.finditer(text):
        tokens.append(m.group(1).lower())
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def offer_violations(text: str, offers: Sequence[SubstantiatedOffer] = ()) -> list[str]:
    """The HARD check, pure: every offer token must match a substantiated REAL offer.

    * code token  -> must be in :func:`substantiated_codes` (seed offers never count);
    * percent-off -> must equal a real offer's ``percent_off``;
    * lexicon hit (coupon/discount/...) -> allowed only if at least one real offer
      exists at all (naming discounts with no authorized offer implies one).

    Returns human-readable violations; empty == clean. With no real offers, ANY
    token violates (fail-closed: no offers doc -> no offer language at all)."""
    codes = substantiated_codes(offers)
    percents = substantiated_percents(offers)
    any_real = bool(codes or percents)
    violations: list[str] = []
    for token in find_offer_tokens(text):
        if token.endswith("% off"):
            pct = int(token.split("%")[0])
            if pct not in percents:
                violations.append(
                    f"unsubstantiated discount {token!r} (no operator-authorized offer at {pct}%)"
                )
        elif token.isupper():  # a captured code
            if token not in codes:
                violations.append(
                    f"fabricated offer code {token!r} (matches no operator-authorized offer)"
                )
        elif not any_real:
            violations.append(
                f"offer language {token!r} with no operator-authorized offer on file"
            )
    return violations


def no_unsubstantiated_offers(
    field_name: str, offers: Sequence[SubstantiatedOffer] = ()
) -> FieldValidator:
    """ValidatorBank member (LLM path): the field must contain no offer/discount
    token that fails :func:`offer_violations`. ERROR severity — blocks + repairs;
    an unrepaired draft fails the cell (typed-or-raise), it never ships."""

    def _fn(value: Any) -> list[ValidationIssue]:
        text = getattr(value, field_name, None)
        if not isinstance(text, str):
            return []
        return [
            ValidationIssue("offer_antifab", Severity.ERROR, f"{field_name!r}: {v}")
            for v in offer_violations(text, offers)
        ]

    return FieldValidator("offer_antifab", _fn)
