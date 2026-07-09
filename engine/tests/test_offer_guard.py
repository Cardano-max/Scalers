"""Anti-fabrication offer guard (CustomerAcq-65w.14) — DB-free, hermetic.

The audit-CRITICAL cases verbatim: the fabricated ARTLOVER/15%-off draft must be
rejected, and seed-mock offers docs (doc_seed_ladies8391_offers_mock, source
'seed') must never substantiate a code on the live path.
"""

from __future__ import annotations

from cells.content_brief import Platform
from cells.offer_guard import (
    SubstantiatedOffer,
    find_offer_tokens,
    is_real_offer_source,
    no_unsubstantiated_offers,
    offer_violations,
    substantiated_codes,
)

# The audit rows, verbatim.
_ARTLOVER_DRAFT = "Fresh ink season - use code ARTLOVER to get 15% off your booking"
_SEED = SubstantiatedOffer(
    code="FLOWER15", doc_id="doc_seed_ladies8391_offers_mock", source="seed", percent_off=15
)
_REAL = SubstantiatedOffer(
    code="FLOWER15", doc_id="doc_offers_ladies8391_2026q3", source="operator", percent_off=15
)


# ── seed/mock sources never substantiate ─────────────────────────────────────


def test_seed_source_is_not_real():
    assert is_real_offer_source("seed", "doc_seed_ladies8391_offers_mock") is False
    assert is_real_offer_source("mock", None) is False
    assert is_real_offer_source(None, "doc_seed_x") is False  # id prefix alone suffices
    assert is_real_offer_source("operator", "doc_offers_real") is True


def test_seed_offer_contributes_no_codes():
    assert substantiated_codes([_SEED]) == frozenset()
    assert substantiated_codes([_SEED, _REAL]) == frozenset({"FLOWER15"})


def test_draft_citing_code_from_seed_doc_only_is_blocked():
    # Bead unit test 1: only the seed offers doc exists -> FLOWER15 is fabricated.
    v = offer_violations("Book this week with code FLOWER15", offers=[_SEED])
    assert v and "FLOWER15" in v[0]


def test_same_draft_passes_with_real_offers_doc():
    assert offer_violations("Book this week with code FLOWER15", offers=[_REAL]) == []


# ── the ARTLOVER fabrication path (hard validator, not advisory prompt) ──────


def test_artlover_draft_is_rejected_with_no_offers():
    # Bead unit test 2 — the literal audit string, no offers doc at all.
    v = offer_violations(_ARTLOVER_DRAFT)
    assert any("ARTLOVER" in x for x in v)
    assert any("15% off" in x for x in v)


def test_artlover_still_rejected_when_real_offers_exist():
    # A real FLOWER15 offer does not launder a DIFFERENT fabricated code.
    v = offer_violations(_ARTLOVER_DRAFT, offers=[_REAL])
    assert any("ARTLOVER" in x for x in v)
    # ...but the 15% figure matches the real offer, so only the code violates.
    assert not any("15% off" in x for x in v)


def test_percent_mismatch_is_rejected():
    ten = SubstantiatedOffer(code="TEN10", doc_id="doc_offers_real", source="operator", percent_off=10)
    v = offer_violations("This weekend only: 15% off all bookings", offers=[ten])
    assert v and "15%" in v[0]


def test_clean_copy_passes_and_free_consult_is_not_an_offer_token():
    # The approved "free consultation" claim must not trip the guard.
    text = "Custom designs drawn for you. Free consultation before every booking."
    assert find_offer_tokens(text) == []
    assert offer_violations(text) == []


def test_obfuscated_tokens_are_detected():
    assert "15% off" in find_offer_tokens("get 15 % off today")
    assert "15% off" in find_offer_tokens("15%off your session")
    assert "ARTLOVER" in find_offer_tokens("use code artlover at checkout")
    assert "20% off" in find_offer_tokens("20 percent off flash pieces")


def test_offer_lexicon_requires_a_real_offer():
    assert offer_violations("DM for a discount!") != []            # no offers at all
    assert offer_violations("DM for a discount!", offers=[_SEED]) != []   # seed only
    assert offer_violations("DM for a discount!", offers=[_REAL]) == []   # real exists


# ── ValidatorBank wiring (the LLM path blocks in the repair loop) ────────────


def test_draft_bank_blocks_fabricated_offer(monkeypatch):
    from cells.post_schemas import MediaKind, MediaSpec, PostDraft
    from cells.validators import ValidationCtx

    validator = no_unsubstantiated_offers("caption")  # fail-closed default: no offers
    draft = PostDraft(
        platform=Platform.INSTAGRAM,
        caption=_ARTLOVER_DRAFT,
        hashtags=["neotraditional"],
        call_to_action="Book now",
        media=MediaSpec(kind=MediaKind.IMAGE, aspect_ratio="4:5", brief="flash sheet"),
    )
    issues = validator.check(draft, ValidationCtx())
    assert issues.issues and all(i.validator == "offer_antifab" for i in issues.issues)
    assert any("ARTLOVER" in i.message for i in issues.issues)


def test_draft_validators_bank_includes_offer_guard():
    from cells.draft import draft_validators
    from kb.voice import VoiceDimensions, VoiceGrounding, GroundingCoverage

    grounding = VoiceGrounding(
        tenant_id="t", dimensions=VoiceDimensions(), exemplars=[],
        coverage=GroundingCoverage.SPARSE, low_grounding=True, exemplar_count=0,
    )
    bank = draft_validators(grounding=grounding, platform=Platform.INSTAGRAM)
    names = [v.name for v in bank.validators]
    assert names.count("offer_antifab") == 2  # caption + call_to_action

# ── qa1 adversarial re-QA regressions (65w.14 FAIL -> fix) ───────────────────


def test_qa1_evasion_strings_now_detected():
    """qa1's four bypass strings — each passed BOTH validator layers before the
    regex fix (save/get-family percents, hyphenated off, promo-without-'code')."""
    for s in (
        "save 15% on your first session",
        "save 15 percent this month",
        "15%-off flash sale",
        "use promo FLOWER15",
    ):
        assert offer_violations(s) != [], s  # no real offers -> must block


def test_adjacent_evasion_variants_detected():
    for s in ("get 20% today", "take 20 percent off", "use PROMO flower15 now"):
        assert find_offer_tokens(s) != [], s


def test_percent_false_positive_guards():
    """Bare percents with no offer shape stay clean — legit copy never blocks."""
    for s in (
        "100% custom designs drawn for you",
        "15% of clients return for a second piece",
        "we are 100% booked this month",
        "promo runs friday",
    ):
        assert find_offer_tokens(s) == [], s
