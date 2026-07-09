"""Supervisor LIVE-STATE tools + voice live re-read (engine-core item 4, real PG).

* studio.live_state reads the DB FRESH on every call (no caching) — finalized
  leads, per-agent activity, file/image snapshot with VLM summaries, per-artist
  artworks + memories;
* the chat host registers the five live-state tools;
* the voice seams (/studio/voice/plan, /studio/voice/orchestrate) carry a fresh
  ``liveState`` snapshot on every response (mint-time context no longer frozen).

Throwaway tenants + cleanup; honest-empty asserted for a bare tenant.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("ENGINE_DATABASE_URL"),
    reason="requires Postgres (set ENGINE_DATABASE_URL)",
)

DSN = os.environ.get("ENGINE_DATABASE_URL", "postgresql://scalers:scalers@localhost:5432/scalers")


def _cleanup(tenant: str, run_ids: list[str] | None = None) -> None:
    import psycopg

    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("DELETE FROM context_artifacts WHERE tenant_id=%s", (tenant,))
        c.execute("DELETE FROM memories WHERE tenant_id=%s", (tenant,))
        c.execute("DELETE FROM artists WHERE tenant_id=%s", (tenant,))
        c.execute("DELETE FROM actions WHERE tenant_id=%s", (tenant,))
        c.execute("DELETE FROM assets WHERE campaign_id=%s", (f"portfolio:{tenant}",))
        for rid in run_ids or []:
            c.execute("DELETE FROM agent_runs WHERE run_id=%s", (rid,))
            c.execute("DELETE FROM artwork_selections WHERE run_id=%s", (rid,))


def test_chat_host_registers_live_state_tools():
    from studio.agui import studio_agent

    names = set(studio_agent._function_toolset.tools.keys())
    assert {
        "get_run_leads", "get_agent_activity", "get_uploaded_files",
        "get_artist_artworks", "get_artist_memory",
    } <= names


def test_live_state_reads_fresh_every_call(monkeypatch):
    from studio import live_state
    from studio.artifacts import register_artifact

    tenant = "test_live_" + uuid.uuid4().hex[:8]
    monkeypatch.setenv("SCALERS_EMBEDDER", "deterministic")
    try:
        # Bare tenant: honest empties.
        snap0 = live_state.files_snapshot(tenant, dsn=DSN)
        assert snap0["total"] == 0 and snap0["images"] == 0 and snap0["newest"] == []
        assert live_state.agent_activity(tenant, dsn=DSN)["runId"] is None
        leads0 = live_state.finalized_leads(tenant, dsn=DSN)
        assert leads0["runId"] is None and leads0["staged"] == []

        # A new upload is visible on the very next call — fresh read, no cache.
        register_artifact(
            tenant, "new-design.png", "artwork", media_type="image/png",
            summary="PNG image, 1,024 bytes — fine-line; motif: koi",
            meta={"vlm_status": "ok", "artist_slug": "bella"}, dsn=DSN,
        )
        snap1 = live_state.files_snapshot(tenant, dsn=DSN)
        assert snap1["total"] == 1 and snap1["images"] == 1
        newest = snap1["newest"][0]
        assert newest["name"] == "new-design.png"
        assert "fine-line" in newest["vlmSummary"]  # the real VLM one-liner
        assert newest["artist"] == "bella" and newest["vlmStatus"] == "ok"
    finally:
        _cleanup(tenant)


def test_finalized_leads_and_activity_from_real_rows(monkeypatch):
    import json as _json

    import psycopg

    from studio import live_state
    from team.store import TeamStore

    tenant = "test_liverun_" + uuid.uuid4().hex[:8]
    campaign_id = f"camp_{uuid.uuid4().hex[:12]}"
    run_id = f"team-{campaign_id}-{uuid.uuid4().hex[:12]}"
    try:
        from actions.store import ensure_schema, record_pending_action

        ensure_schema(DSN)
        record_pending_action(
            tenant_id=tenant, decision_id=None, type="outreach", channel="gmail",
            worker="t", target="nadia@example.com", draft="hello",
            context=_json.dumps({"dossier": {"name": {"value": "Nadia"}}}),
            conf=None, threshold=None, esc_kind="approval_required", esc_label="t",
            idempotency_key=f"{run_id}:c1", run_id=run_id, dsn=DSN,
        )
        ts = TeamStore(DSN)
        ts.setup()
        ts.record_agent_run(
            id=f"ar_t_{uuid.uuid4().hex[:12]}", campaign_id=campaign_id, run_id=run_id,
            role="strategist", model="anthropic:claude-sonnet-4-5",
            input={"goal": "g"}, output={"target_angle": "the angle"},
        )
        ts.record_agent_run(
            id=f"ar_t_{uuid.uuid4().hex[:12]}", campaign_id=campaign_id, run_id=run_id,
            role="jury", model="deterministic",
            input={}, output={"output_ledger": {
                "expected": 2, "drafted": 1,
                "skipped": [{"lead": "Bob", "reason": "no email address", "row": 2}],
            }},
        )

        leads = live_state.finalized_leads(tenant, run_id, dsn=DSN)
        assert leads["runId"] == run_id
        assert leads["staged"] == [{
            "name": "Nadia", "target": "nadia@example.com", "channel": "gmail",
            "status": "pending", "actionId": leads["staged"][0]["actionId"],
        }]
        assert leads["skipped"] == [{"lead": "Bob", "reason": "no email address", "row": 2}]

        activity = live_state.agent_activity(tenant, dsn=DSN)
        assert activity["runId"] == run_id
        assert "strategist" in activity["agents"] and "jury" in activity["agents"]
        assert activity["agents"]["strategist"]["model"] == "anthropic:claude-sonnet-4-5"

        # A pending artwork selection surfaces as awaiting_selection.
        from studio.artwork_flow import request_selection

        request_selection(
            run_id, tenant, "s", question="pick one",
            options=[{"assetId": "a", "artifactId": None, "styles": [], "motifs": [],
                      "why": "w"}],
            dsn=DSN,
        )
        activity2 = live_state.agent_activity(tenant, dsn=DSN)
        assert activity2["status"] == "awaiting_selection"
        assert activity2["selectionPending"]["question"] == "pick one"
    finally:
        _cleanup(tenant, [run_id])
        with psycopg.connect(DSN, autocommit=True) as c:
            c.execute("DELETE FROM actions WHERE run_id=%s", (run_id,))


def test_voice_seams_carry_fresh_live_state(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from studio.agui import mount_studio_agui
    from studio.voice import mount_studio_voice

    tenant = "test_livevoice_" + uuid.uuid4().hex[:8]
    monkeypatch.setenv("STUDIO_TENANT_ID", tenant)
    monkeypatch.setenv("ENGINE_DATABASE_URL", DSN)
    monkeypatch.setenv("SCALERS_EMBEDDER", "deterministic")
    app = FastAPI()
    mount_studio_agui(app)
    mount_studio_voice(app)
    client = TestClient(app)
    try:
        # /studio/voice/plan: contract unchanged + a FRESH liveState key.
        r1 = client.post("/studio/voice/plan", json={"sessionId": "vs", "goal": "grow"})
        assert r1.status_code == 200
        body = r1.json()
        assert {"ok", "plan", "awaitingGo", "runnable", "readback"} <= set(body.keys())
        assert body["liveState"]["activeRun"]["runId"] is None  # honest: no run yet
        assert body["liveState"]["files"]["total"] == 0

        # State written BETWEEN calls appears on the NEXT response — live, not frozen.
        from studio.artifacts import register_artifact

        register_artifact(tenant, "x.png", "image", media_type="image/png",
                          summary="PNG image", dsn=DSN)
        r2 = client.post("/studio/voice/plan", json={"sessionId": "vs", "goal": "grow"})
        assert r2.json()["liveState"]["files"]["total"] == 1

        # /studio/voice/orchestrate (gate refusal branch) also carries liveState.
        r3 = client.post(
            "/studio/voice/orchestrate",
            json={"sessionId": "vs", "transcript": "add instagram"},
        )
        assert r3.status_code == 200
        b3 = r3.json()
        assert b3["launched"] is False and "liveState" in b3
    finally:
        _cleanup(tenant)
