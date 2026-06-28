"""Tests for the deterministic validator bank (cells.validators)."""

from __future__ import annotations

from pydantic import BaseModel

from cells.validators import (
    Severity,
    ValidationCtx,
    Validator,
    ValidatorBank,
    allowed_values,
    banned_phrases,
    max_items,
    no_placeholder,
    non_empty,
    word_count_between,
)


class Sample(BaseModel):
    title: str = ""
    body: str = ""
    kind: str = "a"
    tags: list[str] = []


def test_non_empty_flags_blank_string():
    res = non_empty("title").check(Sample(title="   "), ValidationCtx())
    assert not res.ok
    assert res.errors[0].validator == "non_empty"


def test_non_empty_passes_filled():
    assert non_empty("title").check(Sample(title="hi"), ValidationCtx()).ok


def test_word_count_between_bounds():
    short = word_count_between("body", 5, 20).check(Sample(body="too short"), ValidationCtx())
    assert not short.ok
    ok = word_count_between("body", 2, 20).check(Sample(body="this is fine here"), ValidationCtx())
    assert ok.ok


def test_banned_phrases_case_insensitive():
    res = banned_phrases("body").check(Sample(body="Let us DELVE INTO the data"), ValidationCtx())
    assert not res.ok
    assert "delve into" in res.errors[0].message


def test_no_placeholder_catches_brackets_and_todo():
    assert not no_placeholder("body").check(Sample(body="TODO write this"), ValidationCtx()).ok
    assert not no_placeholder("body").check(Sample(body="hello [INSERT NAME]"), ValidationCtx()).ok
    assert no_placeholder("body").check(Sample(body="a clean caption"), ValidationCtx()).ok


def test_allowed_values():
    assert not allowed_values("kind", {"a", "b"}).check(Sample(kind="z"), ValidationCtx()).ok
    assert allowed_values("kind", {"a", "b"}).check(Sample(kind="b"), ValidationCtx()).ok


def test_max_items_is_advisory_warning():
    res = max_items("tags", 2).check(Sample(tags=["1", "2", "3"]), ValidationCtx())
    assert res.ok  # WARN does not block
    assert res.warnings and res.warnings[0].severity is Severity.WARN


def test_bank_aggregates_and_blocks_on_error():
    bank = ValidatorBank(
        validators=(
            non_empty("title"),
            word_count_between("body", 5, 20),
            max_items("tags", 1),
        )
    )
    res = bank.check(Sample(title="", body="one two three four five", tags=["a", "b"]), ValidationCtx())
    # title empty -> ERROR (blocks); tags over -> WARN (advisory).
    assert not res.ok
    assert len(res.errors) == 1
    assert len(res.warnings) == 1


def test_bank_all_clear():
    bank = ValidatorBank(validators=(non_empty("title"), non_empty("body")))
    assert bank.check(Sample(title="t", body="b"), ValidationCtx()).ok


def test_builtins_satisfy_validator_protocol():
    # The FieldValidator / ValidatorBank both implement the §6.3 Validator protocol.
    assert isinstance(non_empty("title"), Validator)
    assert isinstance(ValidatorBank(), Validator)
