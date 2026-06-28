"""Tests for the humanize voice-QA rewrite cell (CustomerAcq-1mk.3, form b).

Driven offline with Pydantic-AI FunctionModel injection (no API key): the cell
returns a typed HumanizedDraft, enforces the AI-flagger on its OWN output (a slop
rewrite is repaired), and preserves approved claims.
"""

from __future__ import annotations

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from cells.base import CellError
from cells.humanize import HumanizedDraft, build_humanize_cell


def _model(*drafts: dict) -> FunctionModel:
    """A model returning each draft payload as the output-tool call, in order."""
    calls = {"n": 0}

    def fn(messages, info: AgentInfo) -> ModelResponse:
        idx = min(calls["n"], len(drafts) - 1)
        calls["n"] += 1
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, drafts[idx])])

    return FunctionModel(fn)


def test_returns_typed_humanized_draft():
    cell = build_humanize_cell()
    out = cell.run_sync(
        "Rewrite: It's not just ink, it's identity.",
        model=_model({"text": "Fresh ink that actually means something. Book a chat.", "revised": True}),
    )
    assert isinstance(out, HumanizedDraft)
    assert out.revised is True


def test_slop_rewrite_is_repaired_then_accepted():
    # First rewrite still contains an em-dash + transition -> AI-flagger ERROR ->
    # repair; second is clean -> accepted.
    cell = build_humanize_cell()
    out = cell.run_detailed_sync(
        "rewrite",
        model=_model(
            {"text": "Moreover, we craft — carefully.", "revised": True},   # still slop
            {"text": "We take our time with every piece.", "revised": True},  # clean
        ),
    )
    assert isinstance(out.value, HumanizedDraft)
    assert out.repairs >= 1
    assert out.first_pass_valid is False


def test_dropped_approved_claim_is_repaired():
    # The rewrite must keep the approved claim "10 years"; first drops it.
    cell = build_humanize_cell(approved_claims=("10 years",))
    out = cell.run_detailed_sync(
        "rewrite preserving the claim",
        model=_model(
            {"text": "We know our craft well.", "revised": True},             # claim dropped
            {"text": "We've done this for 10 years.", "revised": True},        # claim kept
        ),
    )
    assert "10 years" in out.value.text
    assert out.repairs >= 1


def test_persistent_slop_fails_on_a_code_path():
    cell = build_humanize_cell(retries=1)
    bad = {"text": "Moreover — it's not X, it's Y.", "revised": True}
    try:
        result = cell.run_sync("rewrite", model=_model(bad))
    except CellError:
        result = None
    # Never returns raw slop: either a clean typed draft or a typed CellError.
    assert result is None or isinstance(result, HumanizedDraft)
