"""Tests for the deterministic AI-tell flagger (CustomerAcq-1mk.3, form a).

Covers each detector, threshold/allowlist false-positive control, the safe
deterministic strip, bank integration, and a LABELED EVAL SET demonstrating the
flagger separates AI slop from human copy (the eval-gate evidence).
"""

from __future__ import annotations

from cells.ai_flagger import (
    AiTellKind,
    FlaggerConfig,
    ai_flagger,
    detect_ai_tells,
    normalize_ai_tells,
)
from cells.validators import Severity, ValidationCtx, ValidatorBank


def _kinds(text, config=FlaggerConfig(max_triads=0)):
    return {t.kind for t in detect_ai_tells(text, config)}


# -- per-detector ----------------------------------------------------------- #


def test_detects_em_dash_and_double_hyphen():
    assert AiTellKind.EM_DASH in _kinds("We listen — then we create.")
    assert AiTellKind.EM_DASH in _kinds("We listen--then we create.")


def test_detects_contrast_framing():
    assert AiTellKind.CONTRAST_FRAMING in _kinds("It's not just a tattoo, it's a story.")
    assert AiTellKind.CONTRAST_FRAMING in _kinds("Not just art but rather a lifestyle.")


def test_detects_rule_of_three():
    assert AiTellKind.RULE_OF_THREE in _kinds("We bring skill, passion, and precision.")
    # Single comma "X and Y" is NOT a triad (false-positive guard).
    assert AiTellKind.RULE_OF_THREE not in _kinds("New sleeve, healed and glowing.")


def test_detects_generic_transitions():
    assert AiTellKind.GENERIC_TRANSITION in _kinds("Moreover, the design speaks for itself.")
    assert AiTellKind.GENERIC_TRANSITION in _kinds("In conclusion, book today.")


def test_af04_extended_transition_wordlist():
    assert AiTellKind.GENERIC_TRANSITION in _kinds("Last but not least, book early.")
    assert AiTellKind.GENERIC_TRANSITION in _kinds("First and foremost, we listen.")


# -- AF-05 banned slop lexicon ---------------------------------------------- #


def test_af05_detects_banned_slop():
    assert AiTellKind.BANNED_SLOP in _kinds("Ready to unleash your story?")
    assert AiTellKind.BANNED_SLOP in _kinds("Elevate your look today.")
    # Clean human copy is not flagged.
    assert AiTellKind.BANNED_SLOP not in _kinds("Booked out this week, come by Thursday.")


def test_af05_default_severity_is_error():
    from pydantic import BaseModel

    class D(BaseModel):
        caption: str

    res = ValidatorBank(validators=(ai_flagger("caption"),)).check(
        D(caption="Time to level up your ink."), ValidationCtx()
    )
    assert not res.ok and any("banned slop" in i.message for i in res.errors)


# -- AF-06 hedging ---------------------------------------------------------- #


def test_af06_detects_hedging():
    assert AiTellKind.HEDGING in _kinds("Arguably the best placement for this.")
    assert AiTellKind.HEDGING in _kinds("It's worth noting we book fast.")


def test_af06_hedging_severity_defaults_warn_and_is_tunable_to_error():
    from pydantic import BaseModel

    class D(BaseModel):
        text: str

    sample = D(text="This piece is somewhat large.")
    warn = ValidatorBank(validators=(ai_flagger("text"),)).check(sample, ValidationCtx())
    assert warn.ok and any(i.severity is Severity.WARN for i in warn.warnings)  # advisory
    err = ValidatorBank(
        validators=(ai_flagger("text", FlaggerConfig(hedge_severity=Severity.ERROR)),)
    ).check(sample, ValidationCtx())
    assert not err.ok  # ERROR knob (hooks/headlines) blocks


# -- AF-07 listicle cadence ------------------------------------------------- #


def test_af07_detects_listicle_opener_and_bullets():
    assert AiTellKind.LISTICLE in _kinds("Here are 3 reasons to book:")
    assert AiTellKind.LISTICLE in _kinds("Steps:\n- pick a design\n- book a slot\n- show up")
    # A 2-item list does NOT trip (>=3 threshold; opener requires a digit).
    assert AiTellKind.LISTICLE not in _kinds("Steps:\n- pick\n- book")


# -- AF-08 emoji-bullet lines (AUTO-FIX) ------------------------------------ #


def test_af08_detects_emoji_bullets_and_autofix_strips_them():
    assert AiTellKind.EMOJI_BULLET in _kinds("✅ custom design\n✅ free consult")
    # A single decorative/content emoji is not a bullet list.
    assert AiTellKind.EMOJI_BULLET not in _kinds("Healed and settled 🖤")
    # AUTO-FIX: strip the leading bullet emoji, preserving the line text + breaks.
    assert normalize_ai_tells("✅ custom design\n✅ free consult") == "custom design\nfree consult"


# -- composition: AF-01..08 run together ------------------------------------ #


def test_all_eight_rules_compose_in_one_pass():
    text = "Unleash your vibe — moreover, arguably the best.\n✅ a\n✅ b"
    kinds = _kinds(text)
    assert {
        AiTellKind.EM_DASH,
        AiTellKind.BANNED_SLOP,
        AiTellKind.GENERIC_TRANSITION,
        AiTellKind.HEDGING,
        AiTellKind.EMOJI_BULLET,
    } <= kinds


# -- thresholds + allowlist (false-positive control) ------------------------ #


def test_em_dash_threshold_is_tunable():
    text = "a — b — c"  # two em-dashes
    assert len(detect_ai_tells(text, FlaggerConfig(max_em_dashes=0))) == 2
    assert detect_ai_tells(text, FlaggerConfig(max_em_dashes=2)) == []


def test_one_triad_allowed_by_default():
    # Default max_triads=1: a single triad is fine; a second one flags.
    assert detect_ai_tells("skill, passion, and precision.") == []
    two = "skill, passion, and precision; bold, clean, and sharp."
    assert any(t.kind is AiTellKind.RULE_OF_THREE for t in detect_ai_tells(two))


def test_allowlist_suppresses_false_positive():
    text = "Moreover, we care."
    assert detect_ai_tells(text, FlaggerConfig()) != []
    assert detect_ai_tells(text, FlaggerConfig(allowlist=("Moreover",))) == []


def test_non_english_skips_wordlist_detectors():
    # Spanish 'no es X, es Y' must not trip the English contrast detector.
    text = "No es solo un tatuaje, es una historia que perdura para siempre amigo."
    assert AiTellKind.CONTRAST_FRAMING not in _kinds(text, FlaggerConfig())


# -- safe deterministic strip ----------------------------------------------- #


def test_normalize_strips_em_dash_preserving_words():
    assert normalize_ai_tells("We listen — then create.") == "We listen, then create."
    # Idempotent and leaves semantic tells alone.
    once = normalize_ai_tells("It's not X, it's Y — really.")
    assert normalize_ai_tells(once) == once
    assert "it's" in once.lower()  # contrast framing NOT auto-rewritten


# -- bank integration ------------------------------------------------------- #


def test_ai_flagger_validator_blocks_in_bank():
    from pydantic import BaseModel

    class Draft(BaseModel):
        caption: str

    bank = ValidatorBank(validators=(ai_flagger("caption"),))
    res = bank.check(Draft(caption="Moreover, it's not X, it's Y."), ValidationCtx())
    assert not res.ok
    assert any(i.validator == "ai_flagger" for i in res.errors)
    # Clean copy passes.
    assert bank.check(Draft(caption="Booked out this week. Come by Thursday."), ValidationCtx()).ok


# -- LABELED EVAL SET (eval-gate evidence) ---------------------------------- #

SLOP = [
    "Our craft is simple — listen, design, deliver.",
    "It's not just ink, it's identity.",
    "Moreover, every line tells a story.",
    "We bring skill, passion, and precision to every piece.",
    "In conclusion, book your session today.",
    "It's not about the needle, it's about the art.",
]

HUMAN = [
    "Booked three sessions this week. Come see the new flash sheet.",
    "Spring slots are filling. Grab yours before they're gone.",
    "Watch the linework come together in this reel.",
    "New blackwork sleeve, healed and glowing. Tap to book.",
    "Studio's open late Thursdays now. Walk-ins welcome.",
]


def test_labeled_set_separates_slop_from_human():
    cfg = FlaggerConfig(max_triads=0)  # detection-oriented for the eval
    flagged_slop = [s for s in SLOP if detect_ai_tells(s, cfg)]
    flagged_human = [h for h in HUMAN if detect_ai_tells(h, cfg)]

    recall = len(flagged_slop) / len(SLOP)
    false_positive_rate = len(flagged_human) / len(HUMAN)

    assert recall == 1.0, f"missed slop: {set(SLOP) - set(flagged_slop)}"
    assert false_positive_rate == 0.0, f"false positives: {flagged_human}"
