"""Style-preference learning tests (client direction, PA meeting 2026-07-11).

Pure distillation — no DB. Covers: a single edit's signals, non-change yields
nothing (never inferred), rule promotion only after repeated edits, verbatim
avoid-phrases, and the rendered brief block.
"""

from __future__ import annotations

from studio.style_memory import (
    accumulate_preferences,
    learn_style_preference,
    render_style_preferences_block,
)


def test_single_edit_extracts_deterministic_signals():
    original = (
        "🔥🔥 HUGE 20% off flash sale!! Book now!! Limited spots!! "
        "DM us today for this once-in-a-lifetime deal. #tattoo #flash #sale"
    )
    edited = "Fresh fine-line flash available this month. Message us to book a slot."
    pref = learn_style_preference(original, edited)
    sig = set(pref["signals"])
    assert "shorter" in sig
    assert "no_emoji" in sig
    assert "less_hype" in sig
    assert "drop_discounts" in sig       # the 20% off was removed
    assert "fewer_hashtags" in sig


def test_non_change_infers_nothing():
    assert learn_style_preference("same text here", "same text here") == {
        "signals": [], "removed_phrases": []
    }
    assert learn_style_preference("draft", "") == {"signals": [], "removed_phrases": []}


def test_rule_promotion_requires_repetition():
    # One edit drops emoji -> a suggestion, not yet a rule.
    once = accumulate_preferences([("Nice piece 🔥", "Nice piece.")])
    assert "no_emoji" in once["suggestions"]
    assert once["rules"] == []
    # The operator does it twice -> it becomes a firm RULE.
    twice = accumulate_preferences([
        ("Nice piece 🔥", "Nice piece."),
        ("Healed 💉 and happy 😊", "Healed and happy."),
    ])
    assert "no_emoji" in twice["rules"]
    assert twice["edit_count"] == 2


def test_avoid_phrases_captured_verbatim_after_repetition():
    # The SAME line cut across two drafts -> a firm avoid-phrase (a one-off cut
    # stays a suggestion; repetition makes it a rule).
    edits = [
        ("Book now for the deal of a lifetime. Great work here.", "Great work here."),
        ("Book now for the deal of a lifetime. Fresh ink today.", "Fresh ink today."),
    ]
    prefs = accumulate_preferences(edits)
    assert any("deal of a lifetime" in p for p in prefs["avoid_phrases"])
    # A line cut only once does not reach the avoid threshold.
    once = accumulate_preferences(
        [("One-time filler line here. Keep this.", "Keep this.")]
    )
    assert once["avoid_phrases"] == []


def test_render_block_is_empty_without_signal_and_orders_learned_voice():
    assert render_style_preferences_block(None) == ""
    assert render_style_preferences_block(
        {"rules": [], "suggestions": [], "avoid_phrases": [], "edit_count": 0}
    ) == ""
    prefs = accumulate_preferences([
        ("Huge sale!! 20% off!!", "New flash available."),
        ("Massive promo!! discount inside!!", "Fresh work this week."),
    ])
    block = render_style_preferences_block(prefs)
    assert "OPERATOR STYLE PREFERENCES" in block
    assert "RULE:" in block
    assert "generic" in block
