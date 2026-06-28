"""End-to-end tests for the typed-cell framework via the content-brief cell.

These cover the HARN-02 acceptance criteria:
* a cell returns a validated Pydantic object,
* malformed output is repaired OR fails on a code path (never returned raw),
* first-pass + after-retry valid rates are reported.
"""

from __future__ import annotations

import copy

import pytest

from cells.base import CellError
from cells.content_brief import ContentBrief, Platform, build_content_brief_cell
from tests.conftest import VALID_BRIEF, text_model, tool_model


def _brief(**overrides):
    payload = copy.deepcopy(VALID_BRIEF)
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------- #
# Happy path: validated typed object on the first pass
# --------------------------------------------------------------------------- #


def test_returns_validated_pydantic_object():
    cell = build_content_brief_cell()
    out = cell.run_sync("Tattoo studio spring booking push", model=tool_model(_brief()))
    assert isinstance(out, ContentBrief)
    assert out.platform is Platform.INSTAGRAM
    assert out.headline == VALID_BRIEF["headline"]


def test_first_pass_metrics_reported():
    cell = build_content_brief_cell()
    res = cell.run_detailed_sync("ctx", model=tool_model(_brief()))
    assert res.first_pass_valid is True
    assert res.repairs == 0
    assert res.attempts == 1
    assert res.validation.ok
    assert cell.metrics.first_pass_rate == 1.0
    assert cell.metrics.after_retry_rate == 1.0


# --------------------------------------------------------------------------- #
# Repair path: malformed output is repaired on retry, not returned raw
# --------------------------------------------------------------------------- #


def test_schema_mistype_is_repaired_on_retry():
    # First response has an invalid enum value -> Pydantic-AI repairs -> valid.
    cell = build_content_brief_cell()
    bad = _brief(platform="tiktok")  # not a valid Platform
    res = cell.run_detailed_sync("ctx", model=tool_model(bad, _brief()))
    assert isinstance(res.value, ContentBrief)
    assert res.first_pass_valid is False
    assert res.repairs >= 1


def test_validator_failure_is_repaired_on_retry():
    # First caption is too short (validator-bank ERROR) -> repair -> valid.
    cell = build_content_brief_cell()
    short = _brief(caption="Book now.")  # 2 words, min is 5
    res = cell.run_detailed_sync("ctx", model=tool_model(short, _brief()))
    assert isinstance(res.value, ContentBrief)
    assert res.first_pass_valid is False
    assert res.repairs >= 1
    # The rescued run is reflected in the cell's reported rates.
    assert cell.metrics.after_retry_rate == 1.0
    assert cell.metrics.first_pass_rate == 0.0


def test_banned_phrase_triggers_repair():
    cell = build_content_brief_cell()
    tell = _brief(caption="In conclusion, we delve into the art of the sleeve today friends")
    res = cell.run_detailed_sync("ctx", model=tool_model(tell, _brief()))
    assert isinstance(res.value, ContentBrief)
    assert res.repairs >= 1


# --------------------------------------------------------------------------- #
# Fail-on-code-path: persistently malformed output raises, never returns raw text
# --------------------------------------------------------------------------- #


def test_persistent_validator_failure_raises_cell_error():
    cell = build_content_brief_cell(retries=2)
    short = _brief(caption="Book now.")  # always too short
    with pytest.raises(CellError) as ei:
        cell.run_sync("ctx", model=tool_model(short))  # repeats the bad payload forever
    assert ei.value.attempts == 3  # 1 initial + 2 repairs
    # The failure is recorded in the reported rates as a code-path failure.
    assert cell.metrics.failed == 1
    assert cell.metrics.after_retry_rate == 0.0


def test_failure_never_returns_raw_text():
    cell = build_content_brief_cell(retries=1)
    short = _brief(caption="No.")
    try:
        result = cell.run_sync("ctx", model=tool_model(short))
    except CellError:
        result = None
    # Either we got a typed object or an exception — never a str.
    assert result is None or isinstance(result, ContentBrief)


# --------------------------------------------------------------------------- #
# Text mode: markdown / chain-of-thought JSON is repaired into a typed object
# --------------------------------------------------------------------------- #


def test_text_mode_markdown_json_is_parsed():
    import json

    cell = build_content_brief_cell(text_output=True)
    fenced = f"Here is the brief:\n```json\n{json.dumps(VALID_BRIEF)}\n```\nLet me know!"
    out = cell.run_sync("ctx", model=text_model(fenced))
    assert isinstance(out, ContentBrief)
    assert out.caption == VALID_BRIEF["caption"]


def test_text_mode_repairs_garbage_then_succeeds():
    import json

    cell = build_content_brief_cell(text_output=True)
    good = json.dumps(VALID_BRIEF)
    # First: chain-of-thought with no JSON at all -> RepairError -> ModelRetry.
    res = cell.run_detailed_sync(
        "ctx",
        model=text_model("thinking out loud, no json yet...", good),
    )
    assert isinstance(res.value, ContentBrief)
    assert res.repairs >= 1


# --------------------------------------------------------------------------- #
# Framework-level guarantees
# --------------------------------------------------------------------------- #


def test_cell_is_temperature_zero_and_pinned_by_default():
    cell = build_content_brief_cell()
    assert cell.temperature == 0.0
    assert isinstance(cell.model, str) and cell.model  # a pinned id string


def test_aggregate_valid_rates_across_runs():
    cell = build_content_brief_cell(retries=1)
    cell.run_detailed_sync("c", model=tool_model(_brief()))                       # clean
    cell.run_detailed_sync("c", model=tool_model(_brief(caption="hi"), _brief()))  # rescued
    with pytest.raises(CellError):
        cell.run_sync("c", model=tool_model(_brief(caption="hi")))                # failed
    assert cell.metrics.total == 3
    assert cell.metrics.first_pass_valid == 1
    assert cell.metrics.after_retry_valid == 2
    assert cell.metrics.failed == 1
    assert round(cell.metrics.first_pass_rate, 3) == round(1 / 3, 3)
    assert round(cell.metrics.after_retry_rate, 3) == round(2 / 3, 3)
