"""Tests for the deterministic JSON repair utility (cells.repair)."""

from __future__ import annotations

import pytest

from cells.repair import RepairError, extract_json


def test_plain_json_object():
    assert extract_json('{"a": 1, "b": "x"}') == {"a": 1, "b": "x"}


def test_markdown_fenced_json():
    text = 'Here is your brief:\n```json\n{"headline": "Bold ink"}\n```\nHope it helps!'
    assert extract_json(text) == {"headline": "Bold ink"}


def test_unlabeled_fence():
    text = "```\n{\"x\": [1, 2, 3]}\n```"
    assert extract_json(text) == {"x": [1, 2, 3]}


def test_chain_of_thought_preamble_and_signoff():
    text = (
        "Let me think about the angle first. The studio wants trust, so...\n"
        'Final answer: {"caption": "book your chair"} — done.'
    )
    assert extract_json(text) == {"caption": "book your chair"}


def test_braces_inside_strings_do_not_unbalance():
    text = 'noise {"caption": "use {curly} braces and a } here", "n": 2} trailing'
    assert extract_json(text) == {"caption": "use {curly} braces and a } here", "n": 2}


def test_json_array():
    assert extract_json("prefix [1, 2, {\"k\": \"v\"}] suffix") == [1, 2, {"k": "v"}]


def test_partial_truncated_json_raises():
    # Unterminated object -> not recoverable -> fails on a code path.
    with pytest.raises(RepairError):
        extract_json('{"headline": "Bold ink", "caption": "unterminated')


def test_no_json_at_all_raises():
    with pytest.raises(RepairError):
        extract_json("there is absolutely no json here, just prose")


def test_none_raises():
    with pytest.raises(RepairError):
        extract_json(None)  # type: ignore[arg-type]
