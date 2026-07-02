"""HONEST-TRACE (CustomerAcq-65w.15) — DB-free, hermetic.

A trace must never claim a model deliberated when pure code decided. The audit
found the studio deterministic jury recorded as ``anthropic:claude-opus-4-8``;
the trunk-side dishonesty was the stub jury writing model-id-shaped judge labels
into ``autonomy_jury``. Both are label-only fixes — routing/gate logic untouched.
"""

from __future__ import annotations

import re

from autonomy.judges import DEFAULT_PANEL
from autonomy.jury import (
    DETERMINISTIC_JURY_MODEL,
    JURY_PANEL,
    deterministic_model_label,
    stub_jury,
)
from harness.config import model_allowed

# Anything model-ID-shaped: a family name followed by a versioned tail (e.g.
# claude-haiku-4-5, gpt-5.5, gemini-3-pro, deepseek-v3, llama3.1), or a provider
# route prefix. A family WORD alone ("stub:deepseek-seat") is not a model id.
_MODEL_ID_RE = re.compile(
    r"(?:claude|gpt|gemini|deepseek|llama)[-_.]?[\w.-]*\d|anthropic:|openai:|ollama:",
    re.IGNORECASE,
)


def test_deterministic_jury_label_is_honest():
    # The bead's literal assertion: never the Opus id; an explicit deterministic tag.
    assert DETERMINISTIC_JURY_MODEL != "anthropic:claude-opus-4-8"
    assert DETERMINISTIC_JURY_MODEL == "deterministic:staged-count-gate"
    assert deterministic_model_label("threshold-check") == "deterministic:threshold-check"
    assert not _MODEL_ID_RE.search(DETERMINISTIC_JURY_MODEL)


def test_stub_jury_votes_carry_no_model_id():
    """The stub calls NO model — its recorded judge labels must say so plainly,
    never a model id (pre-8sk they claimed opus-4-8; post-8sk haiku-4-5)."""
    for vote in stub_jury(0.9):
        assert not _MODEL_ID_RE.search(vote.judge), vote.judge
        assert vote.judge.startswith("stub:")


def test_stub_panel_keeps_cross_family_shape():
    # Label-only change: the cross-family auditability of the panel SHAPE stays.
    families = {family for _, family in JURY_PANEL}
    assert families == {"anthropic", "openai", "google", "deepseek"}


def test_real_jury_keeps_real_model_attribution():
    """Edge case pinned by the bead: when the real cross-family jury DOES run,
    its seats carry the true (policy-allowed) model ids — honest in the other
    direction."""
    for seat in DEFAULT_PANEL:
        assert model_allowed(seat.model), seat
        # A real seat's model is a real id, not a deterministic tag.
        assert not seat.model.startswith("deterministic:")
        if seat.family == "anthropic":
            assert "claude-haiku-4-5" in seat.model  # the model actually called (8sk)