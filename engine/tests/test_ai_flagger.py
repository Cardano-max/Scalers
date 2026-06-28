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
from cells.validators import ValidationCtx, ValidatorBank


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
