"""wwy.8: N requested drafts are N DISTINCT drafts.

Two mechanisms, tested offline (no real model, no DB): a per-worker VARIATION
directive that makes same-channel prompts differ deterministically, and a
fan-in DEDUPE backstop in ``_route_node`` that skips a repeated caption with a
concrete reason before it can become a second PENDING row.
"""

from __future__ import annotations

import re

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

import archetypes.compose as compose
from archetypes.compose import (
    CampaignState,
    _draft_fanout,
    _extract_key_messages,
    _make_draft_one_node,
    _make_route_node,
)
from cells.content_brief import build_content_brief_cell
from cells.copywriter import _normalize


def _prompt_text(messages) -> str:
    chunks = []
    for msg in messages:
        for part in getattr(msg, "parts", []):
            content = getattr(part, "content", None)
            if isinstance(content, str):
                chunks.append(content)
    return "\n".join(chunks)


def _brief_payload(caption: str) -> dict:
    return {
        "headline": "scarcity hook",
        "platform": "instagram",
        "angle": "limited spots this month",
        "caption": caption,
        "hashtags": [],
        "call_to_action": "Message us to book your chair",
    }


def _variant_model() -> FunctionModel:
    """A model whose caption DEPENDS on the variant directive in the prompt, so
    distinct prompts yield distinct captions (the honest 'input-dependent' case)."""

    def fn(messages, info):
        text = _prompt_text(messages)
        m = re.search(r"VARIANT (\d+) OF (\d+) for channel '([^']+)'", text)
        if m:
            k, _n, ch = m.group(1), m.group(2), m.group(3)
            caption = (
                f"Number {k} for {ch}: book your bold blackwork sleeve at Skin Design "
                "before the July slots fill and message us today to claim your chair."
            )
        else:
            caption = (
                "Book your bold blackwork sleeve at Skin Design before the July slots "
                "fill and message us today to claim your chair."
            )
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, _brief_payload(caption))])

    return FunctionModel(fn)


def _constant_model() -> FunctionModel:
    """A model that IGNORES the variant directive and repeats one caption — the
    worst case the fan-in dedupe backstop must catch."""

    def fn(messages, info):
        caption = (
            "Book your bold blackwork sleeve at Skin Design before the July slots fill "
            "and message us today to claim your chair."
        )
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, _brief_payload(caption))])

    return FunctionModel(fn)


def _wire_offline(monkeypatch, model: FunctionModel) -> None:
    # Hermetic: fixed voice/docs blocks (so the ONLY per-variant prompt difference
    # is the variation directive) + a scripted content-brief cell.
    monkeypatch.setattr(compose, "_brand_voice_block", lambda tenant_id: "")
    monkeypatch.setattr(compose, "_documents_block", lambda *a, **k: ("", []))
    scripted = build_content_brief_cell(model=model)
    import cells.content_brief as cb

    monkeypatch.setattr(cb, "build_content_brief_cell", lambda **kw: scripted)


def _draft_all(monkeypatch, model: FunctionModel, *, archetype="win_back", output_count=6):
    state = CampaignState(
        campaign_id="c1", run_id="r1", tenant_id="demo", archetype_id=archetype,
        strategy_text="Plan:\n  - scarcity\n  - loyalty reward\n  - new artist",
        output_count=output_count,
    )
    _wire_offline(monkeypatch, model)
    node = _make_draft_one_node(team_store=None, dsn=None)
    assets = []
    for send in _draft_fanout(state):
        out = node(send.arg)  # Send.arg is the raw per-worker payload
        assets.extend(out["assets"])
    return state, assets


def _route(monkeypatch, state, assets):
    staged: list[dict] = []

    def _fake_record(**kw):
        staged.append(kw)
        return f"act_{len(staged)}"

    import actions.store as astore

    monkeypatch.setattr(astore, "record_pending_action", _fake_record)
    routed_state = state.model_copy(update={"assets": assets, "critiques": []})
    out = _make_route_node(team_store=None, dsn=None)(routed_state)
    return out, staged


# ── the variation directive numbers each same-channel draft ──────────────────


def test_fanout_assigns_distinct_variant_slots_per_channel():
    state = CampaignState(
        campaign_id="c1", run_id="r1", tenant_id="demo", archetype_id="win_back",
        strategy_text="Plan:\n  - scarcity\n  - loyalty\n  - new artist",
        output_count=6,
    )
    sends = _draft_fanout(state)
    assert len(sends) == 6
    by_channel: dict[str, list[int]] = {}
    for s in sends:
        arg = s.arg
        by_channel.setdefault(arg["channel"], []).append(arg["variant_index"])
        assert arg["variant_total"] == 3  # win_back = sms+email, 6 round-robin -> 3 each
    # each channel's drafts carry distinct 0..k-1 indices
    for ch, idxs in by_channel.items():
        assert sorted(idxs) == [0, 1, 2], ch
    # angles are drawn from the strategy key-messages
    assert any(s.arg["variant_angle"] for s in sends)


def test_extract_key_messages_parses_strategy_bullets():
    msgs = _extract_key_messages("Objective: X\n  - first angle\n  - second angle\n")
    assert msgs == ["first angle", "second angle"]
    assert _extract_key_messages("no bullets here") == []


# ── AC (a): output_count=6 -> 6 staged, pairwise-distinct captions ───────────


def test_six_requested_drafts_are_six_distinct_staged(monkeypatch):
    state, assets = _draft_all(monkeypatch, _variant_model(), output_count=6)
    assert len(assets) == 6
    out, staged = _route(monkeypatch, state, assets)

    assert len(staged) == 6  # all six staged
    assert len(out["pending_action_ids"]) == 6
    captions = [_normalize(s["draft"]) for s in staged]
    assert len(set(captions)) == 6  # pairwise-distinct normalized captions
    assert "duplicate" not in out["step_log"][0]


# ── AC (b): a repeating model -> dedupe skips with concrete reasons ──────────


def test_duplicate_captions_are_skipped_with_concrete_reasons(monkeypatch):
    state, assets = _draft_all(monkeypatch, _constant_model(), output_count=6)
    assert len(assets) == 6  # the model produced six identical captions
    out, staged = _route(monkeypatch, state, assets)

    assert len(staged) == 1  # only the first distinct caption is staged
    assert len(out["pending_action_ids"]) == 1
    skip_lines = [ln for ln in out["step_log"] if "SKIPPED duplicate" in ln]
    assert len(skip_lines) == 5
    assert all("normalized caption matches asset" in ln for ln in skip_lines)


def test_directive_absent_for_single_draft(monkeypatch):
    # A single-draft channel gets NO variant directive (variant_total==1).
    state, assets = _draft_all(monkeypatch, _variant_model(), archetype="win_back", output_count=1)
    assert len(assets) == 1
    # variant_total is 1 -> the '_variant_model' falls back to the no-marker caption.
    assert "Number" not in assets[0]["content"]["caption"]
