"""S2 misclassification guard — the Oscar Diaz regression (truth-gap fix 7).

The confirmed defect: the deterministic floor read a STATED 'payment' objection from
the customer's own words (a deposit + 'financial issues' + payment-plan ask), and the
LLM overlay reclassified the primary to 'blocked_by_prereq' on semantically-wrong but
on-vocabulary evidence (a STUDIO turn), which then imposed the wrong prompt guard.

The guard in ``_merge_llm``: when the deterministic floor found a STATED objection,
the LLM may refine WITHIN the same taxonomy family (payment -> price, …) or add
secondaries, but may NOT flip the primary to a DIFFERENT family unless it cites
CUSTOMER-quoted (not studio-quoted) evidence for the new family.

All deterministic + direct-unit over ``_merge_llm``/``_finalize`` — no model, no DB.
"""

from __future__ import annotations

from studio.psych_profile import (
    STATED,
    PsychField,
    PsychLLMOut,
    _build_corpus,
    _deterministic_profile,
    _finalize,
    _merge_llm,
    _norm,
    _present_sources,
    analyze_customer,
    objection_family,
)
from studio.reason_history import (
    OBJECTION_BLOCKED_PREREQ,
    OBJECTION_PAYMENT,
    OBJECTION_PRICE,
    OBJECTION_TIMING,
    extract_signals,
    parse_conversation_text,
)

# The synthetic Oscar Diaz echo: a deposit + 'financial issues' + payment-plan ask
# (customer-stated payment objection) with an on-vocabulary prerequisite line spoken
# by the STUDIO ('consultation first').
_OSCAR = (
    "Customer: Hey, I put down the deposit for my sleeve but I've been dealing with "
    "some financial issues lately. / "
    "Studio: Totally understand — we'd want to book a consultation first to re-scope "
    "the piece, and we can go over options. / "
    "Customer: Ok. Is there any way to split the rest into a payment plan?"
)
_STUDIO_LINE = (
    "Totally understand — we'd want to book a consultation first to re-scope the "
    "piece, and we can go over options."
)


def _facts(**kw):
    base = {
        "customer_id": "cust_oscar", "name": "Oscar Diaz", "city": "",
        "interests": [], "persona_traits": {}, "tattoo_history": [], "memories": [],
    }
    base.update(kw)
    return base


def _ctx(sample: str, facts=None):
    facts = facts or _facts()
    turns = parse_conversation_text(sample)
    signals = extract_signals(turns)
    profile = _deterministic_profile(facts, signals, None)
    corpus = _build_corpus(facts, signals, None, turns)
    present = _present_sources(facts, signals, None)
    customer_corpus = _norm(
        "\n".join(t["text"] for t in turns if t["speaker"] == "customer")
    )
    return profile, signals, corpus, present, customer_corpus


def test_deterministic_floor_reads_oscar_as_stated_payment():
    prof = analyze_customer(_facts(), parse_conversation_text(_OSCAR), use_llm=False)
    assert prof.primary_objection.value == OBJECTION_PAYMENT
    assert prof.primary_objection.signal == STATED
    # The studio's prerequisite line is present too — as a NON-primary read.
    assert OBJECTION_BLOCKED_PREREQ in [o.value for o in prof.decision_blockers]


def test_llm_cannot_flip_stated_payment_to_prereq_on_a_studio_quote():
    """The regression itself: the overlay proposes 'blocked_by_prereq' citing the
    STUDIO's line. On-vocabulary, in-corpus — but NOT customer-quoted, so the
    cross-family flip is refused: the primary stays payment-family."""
    profile, signals, corpus, present, cust = _ctx(_OSCAR)
    assert profile.primary_objection.value == OBJECTION_PAYMENT

    llm = PsychLLMOut(
        primary_objection=PsychField(
            value=OBJECTION_BLOCKED_PREREQ, signal=STATED,
            evidence=_STUDIO_LINE, evidence_source="conversation",
        )
    )
    merged = _merge_llm(profile, llm, cust)
    final = _finalize(merged, corpus, present, signals)

    assert final.primary_objection.value == OBJECTION_PAYMENT
    assert objection_family(final.primary_objection.value) == "money"
    assert final.primary_objection.signal == STATED
    # The proposal is demoted, not silently lost (still supervisor-inspectable).
    assert OBJECTION_BLOCKED_PREREQ in [o.value for o in final.secondary_objections]


def test_llm_may_refine_within_the_same_family():
    """payment -> price is a WITHIN-family refinement (the money family, per the
    taxonomy groups) — allowed without extra customer-quoted proof."""
    profile, signals, corpus, present, cust = _ctx(_OSCAR)
    customer_line = (
        "Hey, I put down the deposit for my sleeve but I've been dealing with some "
        "financial issues lately."
    )
    llm = PsychLLMOut(
        primary_objection=PsychField(
            value=OBJECTION_PRICE, signal=STATED,
            evidence=customer_line, evidence_source="conversation",
        )
    )
    merged = _merge_llm(profile, llm, cust)
    final = _finalize(merged, corpus, present, signals)
    assert final.primary_objection.value == OBJECTION_PRICE
    assert objection_family(final.primary_objection.value) == "money"


def test_cross_family_flip_is_allowed_with_customer_quoted_evidence():
    """The escape hatch must stay open: a flip to a different family IS legitimate
    when the LLM cites the customer's OWN words for it (a real signal the
    deterministic phrase floor missed)."""
    sample = (
        _OSCAR
        + " / Customer: also we're relocating cities in the coming months so it "
        "isn't the best moment."
    )
    timing_line = (
        "also we're relocating cities in the coming months so it isn't the best moment."
    )
    profile, signals, corpus, present, cust = _ctx(sample)
    assert profile.primary_objection.value == OBJECTION_PAYMENT  # floor unchanged

    llm = PsychLLMOut(
        primary_objection=PsychField(
            value=OBJECTION_TIMING, signal=STATED,
            evidence=timing_line, evidence_source="conversation",
        )
    )
    merged = _merge_llm(profile, llm, cust)
    final = _finalize(merged, corpus, present, signals)
    assert final.primary_objection.value == OBJECTION_TIMING
    assert final.primary_objection.signal == STATED


def test_flip_refused_when_no_customer_corpus_available():
    """Defensive default: with no customer corpus passed at all, a cross-family flip
    can never be justified — the stated floor stands."""
    profile, signals, corpus, present, _cust = _ctx(_OSCAR)
    llm = PsychLLMOut(
        primary_objection=PsychField(
            value=OBJECTION_BLOCKED_PREREQ, signal=STATED,
            evidence=_STUDIO_LINE, evidence_source="conversation",
        )
    )
    merged = _merge_llm(profile, llm)  # legacy call shape, no corpus
    final = _finalize(merged, corpus, present, signals)
    assert final.primary_objection.value == OBJECTION_PAYMENT
