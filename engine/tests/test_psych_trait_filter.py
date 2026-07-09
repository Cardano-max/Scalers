"""psych_profile × protected traits — the analyst can never assert a sensitive trait.

Adversarial pins for spec §7/§24: even when the (stubbed) LLM returns reads that
assert gender/age/ethnicity/financial status, the deterministic post-filter drops
them, records each drop honestly in ``profile.trait_filtered``, and the customer's
OWN stated words (a real price objection) still survive. No model, no DB, no network.
"""

from __future__ import annotations

from studio.psych_profile import (
    INFERRED,
    INSUFFICIENT,
    STATED,
    PsychField,
    PsychLLMOut,
    analyze_customer,
)
from studio.reason_history import OBJECTION_PRICE, parse_conversation_text

ADVERSARIAL = "she is probably a young latina woman who can't afford it"


def _facts(**kw):
    base = {
        "customer_id": "cust_x", "name": "Jordan Lee", "city": "Austin",
        "interests": ["fine-line"], "persona_traits": {}, "tattoo_history": [],
        "memories": [],
    }
    base.update(kw)
    return base


class _StubCell:
    model = "stub-model"

    def __init__(self, out):
        self._out = out

    def run_sync(self, prompt):
        return self._out


def _with_llm(monkeypatch, out: PsychLLMOut):
    monkeypatch.setattr(
        "studio.psych_profile._build_psych_cell", lambda: _StubCell(out)
    )


# --------------------------------------------------------------------------- #
# The canonical adversarial fixture
# --------------------------------------------------------------------------- #
def test_adversarial_llm_read_is_filtered_and_recorded(monkeypatch):
    _with_llm(monkeypatch, PsychLLMOut(
        emotional_tone=PsychField(
            value="anxious", signal=INFERRED, evidence=ADVERSARIAL,
            evidence_source="csv",
        ),
        intent_strength=PsychField(
            value="low", signal=INFERRED,
            evidence="broke student, aged 22 judging by her handle",
            evidence_source="csv",
        ),
    ))
    prof = analyze_customer(_facts(), use_llm=True)

    # Both trait-asserting reads are DROPPED (value blanked, honest note).
    assert prof.emotional_tone.signal == INSUFFICIENT
    assert prof.emotional_tone.value == ""
    assert "protected trait" in prof.emotional_tone.evidence
    assert prof.intent_strength.signal == INSUFFICIENT

    # Every drop is recorded honestly as a trait_filtered entry.
    fields = {e["field"] for e in prof.trait_filtered}
    assert {"emotional_tone", "intent_strength"} <= fields
    tone_entry = next(e for e in prof.trait_filtered if e["field"] == "emotional_tone")
    for cat in ("gender", "age", "ethnicity", "financial_status"):
        assert cat in tone_entry["categories"]
    # The audit names the matched tokens, never re-states the full inference.
    assert ADVERSARIAL not in str(prof.trait_filtered)

    # Filtered reads can never leak into the derived one-liners.
    assert "latina" not in prof.where_customer_sits.lower()
    assert "latina" not in prof.best_reengagement_angle.lower()


def test_customers_own_price_objection_survives_the_financial_pattern():
    # "can't afford" is a FINANCIAL_STATUS pattern, but here it is the customer's
    # own stated words (first-party) — a stated price objection must survive.
    conv = parse_conversation_text(
        "Customer: I love the fine-line piece but I can't afford it right now. / "
        "Studio: totally fair, we can look at smaller options."
    )
    prof = analyze_customer(_facts(), conv, use_llm=False)
    assert prof.primary_objection.value == OBJECTION_PRICE
    assert prof.primary_objection.signal == STATED
    assert "can't afford" in prof.primary_objection.evidence
    assert all(e["field"] != "primary_objection" for e in prof.trait_filtered)


# --------------------------------------------------------------------------- #
# The dob carve-out: age MAY be derived only from a customer-provided dob
# --------------------------------------------------------------------------- #
def test_age_read_allowed_with_dob_dropped_without(monkeypatch):
    out = PsychLLMOut(
        emotional_tone=PsychField(
            value="steady", signal=INFERRED,
            evidence="age group 25-34 derived from dob on file",
            evidence_source="csv",
        ),
    )
    _with_llm(monkeypatch, out)
    with_dob = analyze_customer(_facts(dob="1999-04-02"), use_llm=True)
    assert with_dob.emotional_tone.value == "steady"
    assert all(e["field"] != "emotional_tone" for e in with_dob.trait_filtered)

    _with_llm(monkeypatch, out)
    without_dob = analyze_customer(_facts(), use_llm=True)
    assert without_dob.emotional_tone.signal == INSUFFICIENT
    entry = next(e for e in without_dob.trait_filtered if e["field"] == "emotional_tone")
    assert "age" in entry["categories"]


# --------------------------------------------------------------------------- #
# Social text is scrubbed BEFORE it becomes evidence
# --------------------------------------------------------------------------- #
def test_social_lines_asserting_traits_are_scrubbed_and_recorded():
    social = (
        "Fine-line florals and script work.\n"
        "she/her — young latina artist-in-the-making.\n"
        "Based in Austin."
    )
    prof = analyze_customer(_facts(), social=social, use_llm=False)
    entries = [e for e in prof.trait_filtered if e["field"] == "social"]
    assert len(entries) == 1
    assert "gender" in entries[0]["categories"]
    assert "ethnicity" in entries[0]["categories"]


def test_fully_offending_social_degrades_to_none_source_absent(monkeypatch):
    # When every social line asserts protected traits, the social surface must not
    # count as a present evidence source — an LLM 'inferred from social' read dies.
    _with_llm(monkeypatch, PsychLLMOut(
        emotional_tone=PsychField(
            value="warm", signal=INFERRED, evidence="from their public bio",
            evidence_source="social",
        ),
    ))
    prof = analyze_customer(_facts(), social=ADVERSARIAL, use_llm=True)
    assert prof.emotional_tone.signal == INSUFFICIENT
    assert any(e["field"] == "social" for e in prof.trait_filtered)


# --------------------------------------------------------------------------- #
# The deterministic floor stays clean on real, trait-free data
# --------------------------------------------------------------------------- #
def test_clean_deterministic_profile_records_no_trait_drops():
    conv = parse_conversation_text(
        "Customer: thinking about a small fine-line piece. / Studio: happy to help. / "
        "Customer: maybe later, short on budget right now."
    )
    prof = analyze_customer(_facts(), conv, use_llm=False)
    assert prof.trait_filtered == []
    assert prof.primary_objection.value == OBJECTION_PRICE
