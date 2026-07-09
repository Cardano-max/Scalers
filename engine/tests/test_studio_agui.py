"""P3.1 AG-UI Studio tests — hermetic (FunctionModel, no network).

Proves the wiring that the live drive exercises end-to-end:
* the campaign-plan SHARED STATE round-trips through ``campaign_plans``;
* the relaxed chat store logs LABELED role turns;
* ``revise_plan`` mutates state + PERSISTS it + logs a turn;
* the approval gate: a ``requires_approval`` send tool surfaces as a
  ``DeferredToolRequests`` and does NOT execute (no action row written).
"""

from __future__ import annotations

import uuid

import pytest
from pydantic_ai import DeferredToolRequests
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from studio.agui import CampaignPlan, StudioDeps, studio_agent
from studio.campaign_plan_store import latest_plans, setup as plan_setup, upsert_plan
from studio.chat_store import PostgresChatStore
from obsapi.db import get_dsn


def _sid() -> str:
    return "test-agui-" + uuid.uuid4().hex[:10]


def test_campaign_plan_store_roundtrip() -> None:
    sid = _sid()
    plan_setup()
    pid = upsert_plan(sid, CampaignPlan(goal="book consults", channels=["instagram"]).model_dump())
    rows = latest_plans(1, session_id=sid)
    assert rows and rows[0]["id"] == pid
    assert rows[0]["state"]["goal"] == "book consults"
    # update bumps the same row (one live plan per session)
    upsert_plan(sid, CampaignPlan(goal="fill May", channels=["email"]).model_dump())
    rows = latest_plans(1, session_id=sid)
    assert rows[0]["state"]["goal"] == "fill May"


def test_notes_round_trip_and_surface_in_plan_context() -> None:
    """Operator brand/strategy notes (the uploaded-notes route writes them onto the
    plan) persist with the plan AND surface to the Host on every turn via
    ``_plan_context`` — real planning context, not a badge. Empty notes add nothing."""
    from types import SimpleNamespace

    from studio.agui import _plan_context

    sid = _sid()
    plan_setup()
    notes = "Warm, plain-spoken. No emoji. Fine-line specialist; never over-promise."
    upsert_plan(sid, CampaignPlan(goal="fill May", notes=notes).model_dump())
    rows = latest_plans(1, session_id=sid)
    assert rows and rows[0]["state"]["notes"] == notes
    # reloads back into the typed plan
    assert CampaignPlan.model_validate(rows[0]["state"]).notes == notes

    # surfaced to the host
    ctx = SimpleNamespace(deps=SimpleNamespace(state=CampaignPlan(goal="fill May", notes=notes)))
    rendered = _plan_context(ctx)  # type: ignore[arg-type]
    assert "BRAND / STRATEGY NOTES" in rendered
    assert "No emoji" in rendered

    # empty notes → no notes block (no fabrication / clutter)
    ctx_empty = SimpleNamespace(deps=SimpleNamespace(state=CampaignPlan(goal="fill May")))
    assert "BRAND / STRATEGY NOTES" not in _plan_context(ctx_empty)  # type: ignore[arg-type]

    # and the deterministic run brief carries the notes too (cells, not just the host)
    from studio.agui import _brief_from_plan

    assert "No emoji" in _brief_from_plan(CampaignPlan(goal="fill May", notes=notes))
    assert "Brand / strategy notes" not in _brief_from_plan(CampaignPlan(goal="fill May"))


def test_chat_store_allows_labeled_role_turns() -> None:
    sid = _sid()
    store = PostgresChatStore(get_dsn())
    store.setup()
    for role, model in [
        ("operator", None),
        ("funnel_architect", "anthropic:claude-sonnet-4-6"),
        ("critic", "anthropic:claude-sonnet-4-6"),
        ("jury", "anthropic:claude-opus-4-8"),
    ]:
        rec = store.append_turn(sid, role, f"[{role}] hi", model)
        assert rec.role == role and rec.model == model
    assert [t.role for t in store.history(sid)] == [
        "operator", "funnel_architect", "critic", "jury"
    ]


@pytest.mark.anyio
async def test_revise_plan_persists_shared_state() -> None:
    sid = _sid()
    calls = {"n": 0}

    def model_fn(messages, info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(parts=[ToolCallPart(
                tool_name="revise_plan",
                args={"goal": "fill Tuesdays", "audience": "local fine-line fans",
                      "channels": ["instagram", "email"]},
            )])
        return ModelResponse(parts=[TextPart("Plan updated. What's your budget?")])

    deps = StudioDeps(state=CampaignPlan(), session_id=sid)
    result = await studio_agent.run("I want to fill Tuesdays", model=FunctionModel(model_fn), deps=deps)
    assert isinstance(result.output, str)
    # shared state mutated in place
    assert deps.state.goal == "fill Tuesdays"
    assert deps.state.channels == ["instagram", "email"]
    # persisted to campaign_plans
    rows = latest_plans(1, session_id=sid)
    assert rows and rows[0]["state"]["goal"] == "fill Tuesdays"
    # a labeled host turn was logged — HUMAN text, never the old "[plan] …" internals
    # (tlv.3: the client transcript must not carry bracketed tags or repr fields)
    hist = PostgresChatStore(get_dsn()).history(sid)
    assert any(
        t.role == "host" and "Updated the plan" in t.text and "fill Tuesdays" in t.text
        for t in hist
    )
    assert not any("[plan]" in t.text for t in hist)


@pytest.mark.anyio
async def test_stage_publish_is_approval_gated_and_does_not_fire() -> None:
    sid = _sid()
    from actions.store import ensure_schema, list_actions

    ensure_schema()
    before = len(list_actions("demo", status="pending"))

    def model_fn(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(
            tool_name="stage_publish",
            args={"channel": "instagram", "draft": "Booking May now"},
        )])

    deps = StudioDeps(state=CampaignPlan(goal="x"), session_id=sid)
    # The AGUIAdapter appends DeferredToolRequests to the output types automatically
    # (UIAdapter.run_stream_native); for a direct run we add it the same way.
    result = await studio_agent.run(
        "post it", model=FunctionModel(model_fn), deps=deps,
        output_type=[str, DeferredToolRequests],
    )
    # The send tool NEVER auto-fires: it surfaces as a deferred approval request.
    assert isinstance(result.output, DeferredToolRequests)
    assert result.output.approvals, "expected an approval-required deferred call"
    # Nothing was staged because the tool body never ran (no approval yet).
    after = len(list_actions("demo", status="pending"))
    assert after == before


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
