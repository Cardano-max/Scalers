"""Consumer-shaped per-lead research + the research enable gate (research_depth).

Pins two fixes:
  * the research gate honors the interview's ``research_depth == "deep"`` answer
    in addition to the existing ``deep_research`` opt-in, and an explicit
    ``deep_research=False`` opt-out ALWAYS wins (no surprise paid egress);
  * ``build_outreach_draft`` plumbs ``research_depth`` through to the gate.

No model, no network — the copywriter cell and the research call are stubbed.
"""

from __future__ import annotations

import pytest

from studio.customer_research import _research_enabled, build_outreach_draft


@pytest.fixture(autouse=True)
def _no_env_gate(monkeypatch):
    monkeypatch.delenv("STUDIO_DEEP_RESEARCH", raising=False)


# --------------------------------------------------------------------------- #
# Gate semantics
# --------------------------------------------------------------------------- #
def test_gate_backward_compat_single_arg():
    assert _research_enabled(True) is True
    assert _research_enabled(False) is False
    assert _research_enabled(None) is False


def test_gate_research_depth_deep_enables():
    assert _research_enabled(None, "deep") is True
    assert _research_enabled(None, "Deep ") is True
    assert _research_enabled(None, "standard") is False
    assert _research_enabled(None, "light") is False
    assert _research_enabled(None, "") is False
    assert _research_enabled(None, None) is False


def test_gate_explicit_opt_out_beats_depth():
    # The operator said "no deeper web research" — a later depth answer never
    # overrides a stated opt-out into live egress.
    assert _research_enabled(False, "deep") is False
    # And an explicit yes stays yes whatever the depth.
    assert _research_enabled(True, "light") is True


def test_gate_env_flag_still_works(monkeypatch):
    monkeypatch.setenv("STUDIO_DEEP_RESEARCH", "1")
    assert _research_enabled(None) is True
    assert _research_enabled(None, "light") is True
    # Explicit opt-out still wins over the env flag (existing behavior).
    assert _research_enabled(False) is False


# --------------------------------------------------------------------------- #
# build_outreach_draft plumbs research_depth into the gate
# --------------------------------------------------------------------------- #
def _consumer_facts():
    return {
        "customer_id": "cust_1", "name": "Sarah Kim", "email": "sarah@example.com",
        "email_opt_in": True, "city": "Austin", "interests": ["fine-line"],
        "preferred_channels": ["email"], "persona_traits": {}, "tattoo_history": [],
        "memories": [], "notes": None, "customer_type": None,
    }


def _capture_research(monkeypatch):
    calls: list[bool] = []

    def fake_research(facts, *, enabled):
        calls.append(enabled)
        return []

    monkeypatch.setattr("studio.customer_research.research_studio", fake_research)
    # Force the LLM-copy branch (where research is resolved) but make the cell
    # builder fail fast so the deterministic fallback writes the copy — no network.
    monkeypatch.setenv("SCALERS_OUTREACH_LLM", "1")

    def boom(**kw):
        raise RuntimeError("no cell in tests")

    monkeypatch.setattr("cells.copywriter.build_copywriter_email_cell", boom)
    return calls


def test_draft_research_enabled_by_interview_depth(monkeypatch):
    calls = _capture_research(monkeypatch)
    out = build_outreach_draft(
        _consumer_facts(), goal="say hello", channel="gmail", research_depth="deep",
    )
    assert calls and calls[0] is True
    assert out["draft"]  # deterministic fallback still produced an honest draft


def test_draft_research_stays_off_without_opt_in(monkeypatch):
    calls = _capture_research(monkeypatch)
    build_outreach_draft(
        _consumer_facts(), goal="say hello", channel="gmail", research_depth="standard",
    )
    assert calls and calls[0] is False


def test_draft_explicit_opt_out_beats_depth(monkeypatch):
    calls = _capture_research(monkeypatch)
    build_outreach_draft(
        _consumer_facts(), goal="say hello", channel="gmail",
        deep_research=False, research_depth="deep",
    )
    assert calls and calls[0] is False
