"""psych_profile — the deep, evidence-grounded customer-psychology analyst (P1 #1).

These pin the OPERATOR'S HEADLINE GATE: every psychological read traces to a real span
of the customer's own data, a lead with no signal yields ``insufficient-signal`` (never
a fabricated objection/motive), and the analyst goes genuinely DEEP (many dimensions,
each separately grounded) rather than 5 flat buckets.

All deterministic (``use_llm=False``) — no model, no DB.
"""

from __future__ import annotations

from studio.psych_profile import (
    CAT_ARTIST,
    CAT_OPEN,
    CAT_REACTIVATION,
    INSUFFICIENT,
    STATED,
    analyze_customer,
)
from studio.reason_history import (
    OBJECTION_PRICE,
    OBJECTION_TIMING,
    parse_conversation_text,
)

_SAMPLE = (
    "Customer: Hi, I wanted to ask about a small floral tattoo on my wrist. / "
    "Studio: fine-line or bold? / Customer: Fine-line, simple. How much? / "
    "Studio: ~$120-$180. / Customer: I like it but maybe later, short on budget right now. / "
    "Studio: We can let you know about flash designs / small-piece offers. / "
    "Customer: Yes please message me if there's a discount. / "
    "Studio: We'll keep you updated."
)


def _facts(**kw):
    base = {
        "customer_id": "cust_sarah", "name": "Sarah", "city": "Austin",
        "interests": [], "persona_traits": {}, "tattoo_history": [], "memories": [],
    }
    base.update(kw)
    return base


def test_sarah_deep_profile_grounded_price_and_timing():
    conv = parse_conversation_text(_SAMPLE)
    prof = analyze_customer(_facts(artist="Maya"), conv, use_llm=False)

    # Category: artist-specific (real artist field).
    assert prof.umbrella_category.value == CAT_ARTIST
    # Primary objection = price, STATED, grounded on the real budget span.
    assert prof.primary_objection.value == OBJECTION_PRICE
    assert prof.primary_objection.signal == STATED
    assert "short on budget" in prof.primary_objection.evidence.lower()
    # Timing surfaces as a secondary objection (also grounded).
    assert OBJECTION_TIMING in [o.value for o in prof.secondary_objections]
    # Price sensitivity high, grounded on the same real span.
    assert prof.price_sensitivity.value == "high"
    assert prof.price_sensitivity.signal == STATED
    # Readiness: named a concrete piece + liked it -> preference stage (buyer-readiness).
    assert prof.readiness_stage.value == "preference"
    # It goes DEEP: several dimensions grounded, and the derived one-liners are populated.
    assert prof.grounded_fields >= 4
    assert prof.best_reengagement_angle and prof.where_customer_sits
    assert prof.had_conversation is True


def test_every_stated_read_traces_to_a_real_span():
    """The #1 anti-fabrication gate: no 'stated' read exists whose evidence is not
    literally present in the customer's own conversation/facts."""
    conv = parse_conversation_text(_SAMPLE)
    corpus = " ".join(t["text"] for t in conv).lower() + " maya austin"
    prof = analyze_customer(_facts(artist="Maya"), conv, use_llm=False)
    for _name, f in prof.scalar_fields():
        if f.signal == STATED:
            assert f.evidence.lower() in corpus, f"ungrounded stated read: {f}"
    for o in prof.secondary_objections + prof.decision_blockers:
        if o.signal == STATED:
            assert o.evidence.lower() in corpus


def test_no_conversation_yields_insufficient_never_fabricated():
    """A lead with facts but NO conversation must NOT get an invented objection or a
    fabricated psychology — conversation-derived dimensions read insufficient-signal."""
    prof = analyze_customer(_facts(name="Blank Lead"), conversation=None, use_llm=False)
    assert prof.primary_objection.signal == INSUFFICIENT
    assert prof.primary_objection.value == ""  # no invented objection
    assert prof.urgency.signal == INSUFFICIENT
    assert prof.emotional_tone.signal == INSUFFICIENT
    assert prof.decision_blockers == []
    assert prof.had_conversation is False
    # An open lead category is still allowed (derived from the absence of signals), and
    # the narrative degrades honestly rather than inventing a motive.
    assert prof.umbrella_category.value in (CAT_OPEN,)
    assert "warm" in prof.where_customer_sits.lower() or prof.where_customer_sits


def test_reactivation_category_from_lifecycle_only():
    prof = analyze_customer(
        _facts(persona_traits={"lifecycle_stage": "lapsing", "win_back_candidate": True}),
        conversation=None, use_llm=False,
    )
    assert prof.umbrella_category.value == CAT_REACTIVATION
    # Grounded on the real persona field, marked inferred (not stated), and its evidence
    # references that field — it survives the gate because persona data is present.
    assert prof.umbrella_category.signal != INSUFFICIENT
    assert "lifecycle" in prof.umbrella_category.evidence.lower()


def test_evidence_match_is_normalized_but_still_rejects_absent_quotes():
    """A verbatim quote survives trivial case/whitespace/punctuation differences; a quote
    that is genuinely absent from the source is still downgraded (errs toward safe)."""
    from studio.psych_profile import (
        PsychField,
        SRC_CONVERSATION,
        _validate_field,
    )

    corpus = "i like it but maybe later short on budget right now"  # normalized form
    present = {SRC_CONVERSATION}
    # Same words, different case + punctuation + spacing -> still grounded (survives).
    ok = _validate_field(
        PsychField(value="price", signal=STATED,
                   evidence="  Short on BUDGET, right now!! ", evidence_source=SRC_CONVERSATION),
        corpus, present,
    )
    assert ok.signal == STATED and ok.value == "price"
    # A quote the customer never said -> downgraded, never a fabricated read.
    bad = _validate_field(
        PsychField(value="trust", signal=STATED,
                   evidence="I don't trust this shop", evidence_source=SRC_CONVERSATION),
        corpus, present,
    )
    assert bad.signal == INSUFFICIENT and bad.value == ""


def test_trust_objection_routes_trust_level_low_grounded():
    conv = parse_conversation_text(
        "Customer: It's my first tattoo and I'm a bit nervous, can I see healed work? / "
        "Studio: Of course."
    )
    prof = analyze_customer(_facts(), conv, use_llm=False)
    assert prof.trust_level.value == "low"
    assert prof.trust_level.signal == STATED
    assert "nervous" in prof.trust_level.evidence.lower() or "first tattoo" in prof.trust_level.evidence.lower()
    assert "trust" in [o.value for o in prof.decision_blockers]
