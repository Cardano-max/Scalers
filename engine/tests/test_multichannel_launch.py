"""Multi-channel campaigns: ONE launch, PER-CHANNEL isolation.

Pins the contract the studio surfaces depend on:

* ``CampaignPlan.channel_plans`` round-trips ``model_dump``/``model_validate``
  (keys are channel ids 'ig' | 'email' | 'sms'; values are per-channel overrides);
* ``effective_channel_plan`` returns a DEEP single-channel copy with that channel's
  overrides applied onto the top-level fields — base plan untouched, missing channel
  key -> plain copy, attach_images/image_style/competitor_research stay ONLY in
  ``channel_plans`` (the IG pipeline reads them from there);
* ``merge_channel_plans`` updates each named channel's dict and NEVER wholesale-
  replaces the map (one channel's edit cannot clobber another's) — the one merge
  rule behind both ``revise_plan`` and the voice ``update_plan`` handler;
* a plan naming MORE than one channel launches ONE ISOLATED CHILD RUN PER CHANNEL:
  each child executes with ``channels==[that channel]`` + its own goal, under a
  distinct parent-suffixed run id, registered on the runs registry, and posts its
  OWN completion summary (channel named) to the thread.

The launch tests fake the executor + chat-turn seams (no network, no model); the
voice POST test drives the real ``/studio/voice/plan`` route against Postgres.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace

import pytest

from studio.agui import (
    CampaignPlan,
    StudioDeps,
    effective_channel_plan,
    merge_channel_plans,
)

# --------------------------------------------------------------------------- #
# CampaignPlan.channel_plans — shape + round-trip
# --------------------------------------------------------------------------- #


def test_channel_plans_defaults_empty() -> None:
    assert CampaignPlan().channel_plans == {}


def test_channel_plans_round_trips_dump_validate() -> None:
    plan = CampaignPlan(
        goal="shared", channels=["ig", "email", "sms"],
        channel_plans={
            "ig": {"goal": "ig goal", "attach_images": True, "image_style": "bold",
                   "competitor_research": True},
            "email": {"goal": "email goal", "audience": "lapsed", "output_count": 3},
            "sms": {"goal": "sms goal", "offer": "reply BOOK", "lead_count": 5},
        },
    )
    dumped = plan.model_dump()
    restored = CampaignPlan.model_validate(dumped)
    assert restored.channel_plans == plan.channel_plans
    assert restored.channel_plans["ig"]["attach_images"] is True
    assert restored.channel_plans["email"]["output_count"] == 3


# --------------------------------------------------------------------------- #
# effective_channel_plan — the per-channel EFFECTIVE plan a child run executes
# --------------------------------------------------------------------------- #


def test_effective_channel_plan_applies_overrides_and_narrows_channel() -> None:
    plan = CampaignPlan(
        goal="shared goal", audience="shared audience", channels=["ig", "email", "sms"],
        output_count=9, lead_count=9, offer="shared offer", tone="warm",
        channel_plans={
            "ig": {"goal": "ig goal", "output_count": 2, "attach_images": True,
                   "image_style": "bold", "competitor_research": True},
        },
    )
    eff = effective_channel_plan(plan, "ig")
    assert eff.channels == ["ig"]
    assert eff.goal == "ig goal"
    assert eff.output_count == 2
    # Fields without an override inherit the shared plan.
    assert eff.audience == "shared audience"
    assert eff.offer == "shared offer" and eff.tone == "warm"
    # POSTING child: lead_count is per-lead message sizing — never leaks in.
    assert eff.lead_count == 0
    # Channel-scoped capability keys stay ONLY in channel_plans — never lifted
    # to top-level fields (the IG pipeline reads them from the dict).
    assert eff.channel_plans["ig"]["attach_images"] is True
    assert eff.channel_plans["ig"]["image_style"] == "bold"
    assert not hasattr(eff, "attach_images")
    assert not hasattr(eff, "image_style")
    assert not hasattr(eff, "competitor_research")


def test_effective_channel_plan_never_mutates_the_base_plan() -> None:
    plan = CampaignPlan(
        goal="shared goal", channels=["ig", "email"],
        channel_plans={"ig": {"goal": "ig goal"}},
    )
    eff = effective_channel_plan(plan, "ig")
    eff.channel_plans["ig"]["goal"] = "mutated"
    eff.audience = "mutated audience"
    # Deep copy: the base plan is untouched by any edit to the effective copy.
    assert plan.goal == "shared goal"
    assert plan.channels == ["ig", "email"]
    assert plan.channel_plans["ig"] == {"goal": "ig goal"}
    assert plan.audience == ""


def test_effective_channel_plan_missing_channel_key_is_plain_copy() -> None:
    plan = CampaignPlan(
        goal="g", audience="a", channels=["ig", "sms"], offer="o",
        channel_plans={"ig": {"goal": "ig goal"}},
    )
    eff = effective_channel_plan(plan, "sms")
    assert eff.channels == ["sms"]
    assert eff.goal == "g" and eff.audience == "a" and eff.offer == "o"


# --------------------------------------------------------------------------- #
# merge_channel_plans — per-channel merge, never a wholesale replace
# --------------------------------------------------------------------------- #


def test_merge_updates_one_channel_without_clobbering_others() -> None:
    plan = CampaignPlan(
        channel_plans={
            "ig": {"goal": "ig goal", "image_style": "bold"},
            "email": {"goal": "email goal"},
        }
    )
    merge_channel_plans(plan, {"ig": {"goal": "new ig goal", "output_count": 4}})
    # The edited channel merges key-by-key (untouched keys survive) …
    assert plan.channel_plans["ig"] == {
        "goal": "new ig goal", "image_style": "bold", "output_count": 4,
    }
    # … and the OTHER channel's overrides are untouched.
    assert plan.channel_plans["email"] == {"goal": "email goal"}


def test_merge_drops_none_values_and_ignores_malformed_entries() -> None:
    plan = CampaignPlan(channel_plans={"sms": {"goal": "keep"}})
    merge_channel_plans(plan, {"sms": {"goal": None, "tone": "urgent"}, "email": "not-a-dict"})
    assert plan.channel_plans["sms"] == {"goal": "keep", "tone": "urgent"}
    assert "email" not in plan.channel_plans
    merge_channel_plans(plan, None)  # no-op, never raises
    assert plan.channel_plans["sms"] == {"goal": "keep", "tone": "urgent"}


async def test_revise_plan_merges_channel_plans(monkeypatch) -> None:
    """The chat host's ``revise_plan`` threads channel_plans through the SAME merge:
    a second call for one channel never erases the first channel's overrides."""
    import studio.agui as agui_mod

    monkeypatch.setattr(agui_mod, "_persist_plan", lambda *a, **k: "plan_x")
    monkeypatch.setattr(agui_mod, "_log_turn", lambda *a, **k: None)
    plan = CampaignPlan(goal="shared")
    ctx = SimpleNamespace(deps=StudioDeps(state=plan, session_id="s-rp", dsn=None))

    await agui_mod.revise_plan(ctx, channel_plans={"ig": {"goal": "ig goal"}})
    await agui_mod.revise_plan(ctx, channel_plans={"email": {"goal": "email goal"}})
    assert plan.channel_plans == {
        "ig": {"goal": "ig goal"}, "email": {"goal": "email goal"},
    }
    assert plan.goal == "shared"  # per-channel overrides never leak to top level


# --------------------------------------------------------------------------- #
# Multi-channel launch — one ISOLATED child run per channel
# --------------------------------------------------------------------------- #


def _fresh_app() -> SimpleNamespace:
    """A registry-only app stand-in (same shape ``_background_app`` provides),
    fresh per test so registries never bleed between tests."""
    return SimpleNamespace(state=SimpleNamespace())


def _stub_summary(plan: CampaignPlan, run_id: str | None) -> dict:
    return {
        "archetype_id": "stub_arch", "run_id": run_id, "campaign_id": "camp_stub",
        "n_queued": 1, "n_pending": 1, "channels": list(plan.channels),
        "runs_row": False, "run_status": "completed", "failure_summary": [],
        "agent_runs": [],
    }


async def _wait_for(predicate, timeout_s: float = 5.0) -> None:
    for _ in range(int(timeout_s / 0.02)):
        if predicate():
            return
        await asyncio.sleep(0.02)
    assert predicate(), "background children did not finish in time"


async def test_multichannel_launch_runs_one_isolated_child_per_channel(monkeypatch) -> None:
    import studio.agui as agui_mod

    calls: list[dict] = []
    turns: list[tuple[str, str]] = []

    def fake_exec(plan, session_id, tenant_id, dsn, run_id=None):
        calls.append({
            "run_id": run_id, "channels": list(plan.channels), "goal": plan.goal,
            "audience": plan.audience,
        })
        return _stub_summary(plan, run_id)

    monkeypatch.setattr(agui_mod, "_execute_campaign_sync", fake_exec)
    monkeypatch.setattr(
        agui_mod, "_log_turn", lambda dsn, sid, role, text, model: turns.append((role, text))
    )

    plan = CampaignPlan(
        goal="shared goal", audience="shared audience", channels=["ig", "email", "sms"],
        channel_plans={
            "ig": {"goal": "ig goal"},
            "email": {"goal": "email goal", "audience": "email audience"},
            "sms": {"goal": "sms goal"},
        },
    )
    app = _fresh_app()
    parent = f"team-camp_mc-{uuid.uuid4().hex[:12]}"
    children = agui_mod.start_registered_run(app, None, "sess-mc", "t-mc", plan, parent)

    # One child per channel, run ids parent-suffixed, all distinct.
    assert [c["channel"] for c in children] == ["ig", "email", "sms"]
    assert [c["run_id"] for c in children] == [
        f"{parent}-ig", f"{parent}-email", f"{parent}-sms",
    ]
    registry = app.state._studio_runs
    assert set(registry) == {c["run_id"] for c in children}

    await _wait_for(
        lambda: len(calls) == 3
        and all(registry[c["run_id"]]["status"] == "completed" for c in children)
    )

    # Each child executed its OWN effective plan: channels narrowed to that channel
    # and the goal taken from channel_plans (audience inherited unless overridden).
    by_run = {c["run_id"]: c for c in calls}
    assert by_run[f"{parent}-ig"]["channels"] == ["ig"]
    assert by_run[f"{parent}-ig"]["goal"] == "ig goal"
    assert by_run[f"{parent}-ig"]["audience"] == "shared audience"
    assert by_run[f"{parent}-email"]["channels"] == ["email"]
    assert by_run[f"{parent}-email"]["goal"] == "email goal"
    assert by_run[f"{parent}-email"]["audience"] == "email audience"
    assert by_run[f"{parent}-sms"]["channels"] == ["sms"]
    assert by_run[f"{parent}-sms"]["goal"] == "sms goal"

    # Each child posts its OWN honest completion summary to the thread, naming its
    # run id and its channel (the existing per-run behavior, once per child).
    host_turns = [t for role, t in turns if role == "host"]
    for c in children:
        assert any(c["run_id"] in t and c["channel"] in t for t in host_turns), c


async def test_single_channel_launch_is_unchanged(monkeypatch) -> None:
    """A one-channel plan launches under the parent run id itself — no children,
    same registry shape as before (backward-compatible with resume + pollers)."""
    import studio.agui as agui_mod

    calls: list[dict] = []

    def fake_exec(plan, session_id, tenant_id, dsn, run_id=None):
        calls.append({"run_id": run_id, "channels": list(plan.channels)})
        return _stub_summary(plan, run_id)

    monkeypatch.setattr(agui_mod, "_execute_campaign_sync", fake_exec)
    monkeypatch.setattr(agui_mod, "_log_turn", lambda *a, **k: None)

    plan = CampaignPlan(goal="g", audience="a", channels=["email"])
    app = _fresh_app()
    parent = f"team-camp_sc-{uuid.uuid4().hex[:12]}"
    children = agui_mod.start_registered_run(app, None, "sess-sc", "t-sc", plan, parent)

    assert children == []
    registry = app.state._studio_runs
    assert set(registry) == {parent}
    await _wait_for(lambda: registry[parent]["status"] == "completed")
    assert calls == [{"run_id": parent, "channels": ["email"]}]


async def test_launch_studio_run_carries_children(monkeypatch) -> None:
    """The shared launch seam (voice GO-gate + /studio/run button) surfaces the
    per-channel children so the caller can name EACH channel's own run id."""
    import studio.agui as agui_mod

    monkeypatch.setattr(
        agui_mod, "_execute_campaign_sync",
        lambda plan, session_id, tenant_id, dsn, run_id=None: _stub_summary(plan, run_id),
    )
    monkeypatch.setattr(agui_mod, "_log_turn", lambda *a, **k: None)

    plan = CampaignPlan(
        goal="g", audience="a", channels=["ig", "email"],
        channel_plans={"ig": {"goal": "ig goal"}},
    )
    info = await agui_mod.launch_studio_run(_fresh_app(), None, "sess-ls", "t-ls", plan)
    assert info["status"] == "running"
    assert [c["channel"] for c in info["children"]] == ["ig", "email"]
    assert [c["runId"] for c in info["children"]] == [
        f"{info['runId']}-ig", f"{info['runId']}-email",
    ]

    # Single-channel: no children key (the existing contract is unchanged).
    single = CampaignPlan(goal="g", audience="a", channels=["email"])
    info2 = await agui_mod.launch_studio_run(_fresh_app(), None, "sess-ls2", "t-ls2", single)
    assert "children" not in info2


# --------------------------------------------------------------------------- #
# Voice update_plan handler — POST-level per-channel merge (real PG)
# --------------------------------------------------------------------------- #


@pytest.mark.integration
def test_voice_plan_post_merges_channel_plans_without_clobbering(monkeypatch) -> None:
    """POST /studio/voice/plan with channel_plans merges PER CHANNEL through the
    persisted plan: a second call for one channel never erases another channel's
    overrides, and a re-send of one channel merges key-by-key."""
    if not os.environ.get("ENGINE_DATABASE_URL"):
        pytest.skip("requires Postgres (set ENGINE_DATABASE_URL)")
    import psycopg
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from studio.voice import mount_studio_voice

    dsn = os.environ["ENGINE_DATABASE_URL"]
    tenant = "test_mcvoice_" + uuid.uuid4().hex[:8]
    session = "vs-mc-" + uuid.uuid4().hex[:8]
    monkeypatch.setenv("STUDIO_TENANT_ID", tenant)
    app = FastAPI()
    mount_studio_voice(app)
    client = TestClient(app)
    try:
        r1 = client.post("/studio/voice/plan", json={
            "sessionId": session, "goal": "shared goal",
            "channel_plans": {"ig": {"goal": "ig goal", "attach_images": True}},
        })
        assert r1.status_code == 200
        assert r1.json()["plan"]["channel_plans"] == {
            "ig": {"goal": "ig goal", "attach_images": True},
        }

        # A later email-only answer merges in WITHOUT touching the ig overrides.
        r2 = client.post("/studio/voice/plan", json={
            "sessionId": session,
            "channel_plans": {"email": {"goal": "email goal", "output_count": 3}},
        })
        cp2 = r2.json()["plan"]["channel_plans"]
        assert cp2["ig"] == {"goal": "ig goal", "attach_images": True}
        assert cp2["email"] == {"goal": "email goal", "output_count": 3}

        # Re-sending ONE channel merges key-by-key (its untouched keys survive).
        r3 = client.post("/studio/voice/plan", json={
            "sessionId": session, "channel_plans": {"ig": {"goal": "new ig goal"}},
        })
        cp3 = r3.json()["plan"]["channel_plans"]
        assert cp3["ig"] == {"goal": "new ig goal", "attach_images": True}
        assert cp3["email"] == {"goal": "email goal", "output_count": 3}
    finally:
        with psycopg.connect(dsn, autocommit=True) as c:
            c.execute("DELETE FROM campaign_plans WHERE session_id=%s", (session,))


def test_voice_update_plan_schema_declares_channel_plans() -> None:
    """The minted realtime tool schema lets the model store per-channel answers —
    an object of per-channel override objects — while the surface stays the same
    fixed tools (still no send/publish tool)."""
    from studio.voice import VOICE_TOOL_NAMES, VOICE_TOOLS

    assert VOICE_TOOL_NAMES == ("update_plan", "get_run_status", "list_conversation_leads", "request_orchestration")
    update_plan = next(t for t in VOICE_TOOLS if t["name"] == "update_plan")
    cp = update_plan["parameters"]["properties"]["channel_plans"]
    assert cp["type"] == "object"
    assert cp["additionalProperties"] == {"type": "object"}
    # The description must direct per-channel answers INTO the map, not over the
    # shared top-level fields.
    assert "own" in cp["description"].lower()
    assert "goal" in cp["description"]


def test_operator_explicit_channel_never_diverts_to_persona_preference():
    """An 'sms' child run against a lead with no SMS consent must yield an honest
    NO-channel (counted skip), never a silent instagram DM the operator did not
    ask for — a real multi-channel smoke staged exactly that."""
    from studio.customer_research import choose_channel

    facts = {
        "persona_traits": {"likely_best_channel": "instagram"},
        "preferred_channels": ["instagram"],
        "sms_opt_in": False,
        "email": "x@y.example",
        "email_opt_in": True,
    }
    assert choose_channel(facts, ["sms"]) == ""          # consent outranks operator
    assert choose_channel(facts, ["email"]) == "gmail"   # operator outranks persona
    assert choose_channel(facts, None) == "instagram"    # legacy path unchanged
