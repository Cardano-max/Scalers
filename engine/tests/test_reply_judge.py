"""CustomerAcq-tlv.2 round-3: the LLM-judge fallback for the inbound reply classifier.

The deterministic ``classify_outcome`` floor is safe on BOUNDED classes but a reply can
book an UNBOUNDED "something else" ("booking a week off work", "went with a place closer
to home", "with the artist my mate recommended not you") that no phrase list enumerates.
When a model is available the judge adjudicates with real semantic understanding; on any
failure the deterministic floor stands. These tests MOCK the cell (hermetic, no key)."""

from __future__ import annotations

import proactive.followup_source as fs


class _FakeJudge:
    def __init__(self, outcome):
        self._out = fs.ReplyJudgeOut(outcome=outcome, confidence=0.95, reason="test")

    def run_sync(self, prompt):
        return self._out


class _BoomJudge:
    def run_sync(self, prompt):
        raise RuntimeError("model unavailable")


def test_classify_reply_keyless_equals_deterministic_floor(monkeypatch):
    monkeypatch.setenv("SCALERS_INBOUND_LLM", "0")
    for t in ["book me in for friday", "i booked my honeymoon", "too pricey right now",
              "how much is it?", "I wouldn't book that", "sign me up!"]:
        assert fs.classify_reply(t) == fs.classify_outcome(t)


def test_judge_downgrades_a_floor_false_booked(monkeypatch):
    # The floor mislabels this UNBOUNDED semantic case as booked (booking elsewhere in a
    # later clause); the judge corrects it to replied.
    text = "honestly ready to book but with the artist my mate recommended not you sorry"
    assert fs.classify_outcome(text) == "booked"  # the floor's residual miss
    monkeypatch.setenv("SCALERS_INBOUND_LLM", "1")
    monkeypatch.setattr(fs, "_build_reply_judge_cell", lambda: _FakeJudge("replied"))
    assert fs.classify_reply(text) == "replied"


def test_judge_confirms_a_genuine_booking(monkeypatch):
    monkeypatch.setenv("SCALERS_INBOUND_LLM", "1")
    monkeypatch.setattr(fs, "_build_reply_judge_cell", lambda: _FakeJudge("booked"))
    assert fs.classify_reply("yes! book me in for saturday") == "booked"


def test_judge_can_recover_a_dropped_past_tense_booking(monkeypatch):
    # The floor conservatively drops bare "just booked" -> replied; a judge that
    # understands "just booked, see you friday!" is a real conversion can restore booked.
    text = "just booked, see you friday!"
    assert fs.classify_outcome(text) == "replied"  # conservative floor
    monkeypatch.setenv("SCALERS_INBOUND_LLM", "1")
    monkeypatch.setattr(fs, "_build_reply_judge_cell", lambda: _FakeJudge("booked"))
    assert fs.classify_reply(text) == "booked"


def test_judge_error_falls_back_to_deterministic_floor(monkeypatch):
    monkeypatch.setenv("SCALERS_INBOUND_LLM", "1")
    monkeypatch.setattr(fs, "_build_reply_judge_cell", lambda: _BoomJudge())
    # Floor stands on a model failure — never a crash, never a poisoned label.
    assert fs.classify_reply("book me in for friday") == "booked"
    assert fs.classify_reply("i booked my honeymoon") == "replied"


def test_off_vocabulary_judge_label_is_ignored(monkeypatch):
    monkeypatch.setenv("SCALERS_INBOUND_LLM", "1")
    monkeypatch.setattr(fs, "_build_reply_judge_cell",
                        lambda: _FakeJudge("objected:vibes"))  # not a real type
    # Invalid label -> deterministic floor stands.
    assert fs.classify_reply("too pricey right now") == "objected:price"


def test_empty_text_raises_before_the_judge(monkeypatch):
    monkeypatch.setenv("SCALERS_INBOUND_LLM", "1")
    monkeypatch.setattr(fs, "_build_reply_judge_cell", lambda: _FakeJudge("booked"))
    import pytest
    with pytest.raises(ValueError):
        fs.classify_reply("   ")
