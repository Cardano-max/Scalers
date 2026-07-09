"""Artwork TOP-4 + mid-run selection pause (engine-core item 3, real PG).

Covers:

* ``top_artwork_options`` — deterministic top-k with a grounded why per option;
* provided-leads email path with ``attach_artwork``: the run PAUSES after strategy
  (``run_status='awaiting_selection'``, durable ``artwork_selections`` row, ZERO
  staged drafts), and the RESUME (recorded choice + re-invoke) stages drafts whose
  ``context.artwork`` carries the pick + ``attachment_artifact_id`` for gmail —
  with NO duplicated planner/strategist agent_runs;
* an EMPTY library never pauses — the run proceeds with the honest note;
* the IG pipeline path pauses BEFORE the compose spine runs;
* ``GET /studio/run/{id}`` surfaces ``selectionRequest`` from the durable row and
  ``POST /studio/campaign/{id}/select-artwork`` validates the pick.

No model key needed: the strategist/critic cells fail HONESTLY offline (recorded
as failed steps) — staging still happens, which is exactly the honesty contract.
"""

from __future__ import annotations

import json
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("ENGINE_DATABASE_URL"),
    reason="requires Postgres (set ENGINE_DATABASE_URL)",
)

DSN = os.environ.get("ENGINE_DATABASE_URL", "postgresql://scalers:scalers@localhost:5432/scalers")


def _cleanup(tenant: str, run_ids: list[str]) -> None:
    import psycopg

    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("DELETE FROM context_artifacts WHERE tenant_id=%s", (tenant,))
        c.execute("DELETE FROM memories WHERE tenant_id=%s", (tenant,))
        c.execute("DELETE FROM assets WHERE campaign_id=%s", (f"portfolio:{tenant}",))
        c.execute("DELETE FROM actions WHERE tenant_id=%s", (tenant,))
        c.execute("DELETE FROM customers WHERE tenant_id=%s", (tenant,))
        for rid in run_ids:
            c.execute("DELETE FROM artwork_selections WHERE run_id=%s", (rid,))
            c.execute("DELETE FROM agent_runs WHERE run_id=%s", (rid,))
            c.execute("DELETE FROM runs WHERE run_id=%s", (rid,))
            c.execute("DELETE FROM campaign_blueprints WHERE run_id=%s", (rid,))


def _seed_artwork(tenant: str, artist: str = "Bella", n: int = 5) -> list[str]:
    from team.store import TeamStore

    store = TeamStore(DSN)
    store.setup()
    ids = []
    styles = [["fine-line", "floral"], ["blackwork"], ["script"], ["fine-line"], ["geometric"]]
    for i in range(n):
        aid = f"art_pausetest_{tenant}_{i:02d}"
        store.record_asset(
            id=aid,
            campaign_id=f"portfolio:{tenant}",
            asset_type="studio_artwork",
            content={
                "artist": artist,
                "image_ref": f"artifact://art_img_test{i}",
                "caption": f"piece {i}",
                "styles": styles[i % len(styles)],
                "motifs": ["peony"] if i == 0 else ["wave"],
                "source": "upload",
                "vlm_summary": f"vlm summary {i}",
                "artifact_id": f"art_img_test{i}",
            },
            status="library",
        )
        ids.append(aid)
    return ids


def _seed_leads(tenant: str, n: int = 2) -> list[str]:
    from studio.customer_research import ingest_leads

    rows = [
        {"name": f"Lead {i}", "email": f"lead{i}@example.com"} for i in range(n)
    ]
    return list(ingest_leads(tenant, rows, dsn=DSN)["customer_ids"])


def _plan(tenant_ids: list[str], **kw):
    from studio.agui import CampaignPlan

    base = dict(
        goal="win back lapsed clients",
        audience="past clients",
        channels=["email"],
        lead_source="provided",
        attach_artwork=True,
        artist="Bella",
        campaign_type="win-back",
        deep_research=False,
        customers={"customer_ids": tenant_ids, "rows": len(tenant_ids)},
    )
    base.update(kw)
    return CampaignPlan(**base)


def test_top_artwork_options_deterministic_top4():
    from studio.artwork_flow import top_artwork_options

    tenant = "test_awopts_" + uuid.uuid4().hex[:8]
    _seed_artwork(tenant)
    try:
        opts = top_artwork_options(
            tenant, artist="Bella", theme_terms=["fine-line", "peony"], k=4, dsn=DSN
        )
        assert len(opts) == 4
        assert all(set(o.keys()) == {"assetId", "artifactId", "styles", "motifs", "why"}
                   for o in opts)
        # Rank 1 = the piece both style- and motif-matched, with a grounded why.
        assert opts[0]["assetId"] == f"art_pausetest_{tenant}_00"
        assert "fine-line" in opts[0]["why"] and opts[0]["artifactId"] == "art_img_test0"
        # Deterministic: a second call ranks identically.
        assert [o["assetId"] for o in opts] == [
            o["assetId"]
            for o in top_artwork_options(
                tenant, artist="Bella", theme_terms=["fine-line", "peony"], k=4, dsn=DSN
            )
        ]
        # Honest-empty for an artist with no pieces.
        assert top_artwork_options(tenant, artist="Ghost", k=4, dsn=DSN) == []
    finally:
        _cleanup(tenant, [])


def test_provided_leads_pause_then_resume_with_artwork(monkeypatch):
    from studio.agui import _execute_campaign_sync
    from studio.artwork_flow import get_selection, record_choice

    monkeypatch.setenv("SCALERS_EMBEDDER", "deterministic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    tenant = "test_awpause_" + uuid.uuid4().hex[:8]
    campaign_id = f"camp_{uuid.uuid4().hex[:12]}"
    run_id = f"team-{campaign_id}-{uuid.uuid4().hex[:12]}"
    try:
        _seed_artwork(tenant)
        ids = _seed_leads(tenant)
        plan = _plan(ids)

        # 1) First drive: PAUSES after strategy, before drafting.
        summary = _execute_campaign_sync(plan, "sess-aw", tenant, DSN, run_id)
        assert summary["run_status"] == "awaiting_selection"
        req = summary["selection_request"]
        assert req["kind"] == "artwork" and len(req["options"]) == 4
        assert "which should I use?" in req["question"]
        sel = get_selection(run_id, dsn=DSN)
        assert sel is not None and sel["status"] == "awaiting"

        # NOTHING drafted or staged while paused.
        from actions.store import list_actions_for_run

        assert list_actions_for_run(run_id, dsn=DSN) == []

        # 2) Operator picks; the resume stages drafts with the artwork on context.
        pick = req["options"][0]
        assert record_choice(run_id, pick["assetId"], artifact_id=pick["artifactId"], dsn=DSN)
        summary2 = _execute_campaign_sync(plan, "sess-aw", tenant, DSN, run_id)
        assert summary2["run_status"] != "awaiting_selection"
        assert summary2["artwork"]["assetId"] == pick["assetId"]
        actions = list_actions_for_run(run_id, dsn=DSN)
        assert len(actions) == len(ids)  # one HELD draft per provided lead
        for a in actions:
            ctx = json.loads(a.context or "{}")
            assert ctx["artwork"]["assetId"] == pick["assetId"]
            assert ctx["artwork"]["artifactId"] == pick["artifactId"]
            assert ctx["artwork"]["vlmSummary"] == "vlm summary 0"
            if a.channel in ("gmail", "email"):
                assert ctx["attachment_artifact_id"] == pick["artifactId"]
            assert a.status == "pending"  # HELD, approve-first

        # 3) No duplicated one-shot steps across pause + resume.
        import psycopg

        with psycopg.connect(DSN, autocommit=True) as c:
            counts = dict(
                c.execute(
                    "SELECT role, count(*) FROM agent_runs WHERE run_id=%s "
                    "AND role IN ('planner','strategist') GROUP BY role",
                    (run_id,),
                ).fetchall()
            )
        assert counts.get("planner") == 1 and counts.get("strategist") == 1
    finally:
        _cleanup(tenant, [run_id])


def test_empty_library_never_pauses(monkeypatch):
    from studio.agui import _execute_campaign_sync

    monkeypatch.setenv("SCALERS_EMBEDDER", "deterministic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    tenant = "test_awnone_" + uuid.uuid4().hex[:8]
    campaign_id = f"camp_{uuid.uuid4().hex[:12]}"
    run_id = f"team-{campaign_id}-{uuid.uuid4().hex[:12]}"
    try:
        ids = _seed_leads(tenant, n=1)
        summary = _execute_campaign_sync(_plan(ids), "sess-none", tenant, DSN, run_id)
        assert summary["run_status"] != "awaiting_selection"
        assert "no matching artwork in the library" in (summary.get("artwork_note") or "")
        assert any("no matching artwork" in n for n in summary.get("step_notes", []))
        from actions.store import list_actions_for_run

        acts = list_actions_for_run(run_id, dsn=DSN)
        assert len(acts) == 1  # the run proceeded WITHOUT artwork
        assert "artwork" not in json.loads(acts[0].context or "{}")
    finally:
        _cleanup(tenant, [run_id])


def test_instagram_path_pauses_before_compose(monkeypatch):
    from studio import campaign_runner
    from studio.agui import CampaignPlan, _execute_campaign_sync
    from studio.artwork_flow import record_choice

    monkeypatch.setenv("SCALERS_EMBEDDER", "deterministic")
    tenant = "test_awig_" + uuid.uuid4().hex[:8]
    campaign_id = f"camp_{uuid.uuid4().hex[:12]}"
    run_id = f"team-{campaign_id}-{uuid.uuid4().hex[:12]}"
    calls: list[dict] = []

    def _fake_run_and_trace(**kw):
        calls.append(kw)
        return {
            "run_id": kw.get("run_id"), "campaign_id": campaign_id,
            "archetype_id": kw.get("archetype_id"), "agent_runs": [],
            "n_pending": 0, "n_queued": 0, "channels": ["instagram"],
            "step_notes": [], "runs_row": False,
            "run_status": "completed", "failure_summary": [],
        }

    monkeypatch.setattr(campaign_runner, "run_and_trace", _fake_run_and_trace)
    try:
        _seed_artwork(tenant)
        plan = CampaignPlan(
            goal="create an Instagram post for Bella", audience="followers",
            channels=["instagram"], artist="Bella",
        )
        # PAUSE happens BEFORE the compose spine ever runs.
        summary = _execute_campaign_sync(plan, "sess-ig", tenant, DSN, run_id)
        assert summary["run_status"] == "awaiting_selection"
        assert calls == []  # compose spine untouched while paused
        req = summary["selection_request"]

        # Resume: compose runs once, with the pick attached to the summary.
        assert record_choice(run_id, req["options"][0]["assetId"],
                             artifact_id=req["options"][0]["artifactId"], dsn=DSN)
        summary2 = _execute_campaign_sync(plan, "sess-ig", tenant, DSN, run_id)
        assert len(calls) == 1 and calls[0]["run_id"] == run_id
        assert summary2["artwork"]["assetId"] == req["options"][0]["assetId"]
        assert summary2["routed_channel"] == "instagram"
    finally:
        _cleanup(tenant, [run_id])


def test_run_route_surfaces_selection_and_select_route_validates(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from studio.agui import mount_studio_agui
    from studio.artwork_flow import request_selection

    tenant = "test_awroute_" + uuid.uuid4().hex[:8]
    run_id = f"team-camp_{uuid.uuid4().hex[:8]}-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("STUDIO_TENANT_ID", tenant)
    monkeypatch.setenv("ENGINE_DATABASE_URL", DSN)
    app = FastAPI()
    mount_studio_agui(app)
    client = TestClient(app)
    try:
        options = [{"assetId": "a1", "artifactId": "f1", "styles": [], "motifs": [],
                    "why": "w"}]
        request_selection(
            run_id, tenant, "sess-r",
            question="I found 1 matching piece for this campaign — which should I use?",
            options=options, dsn=DSN,
        )
        # The poller reads the pause from the DURABLE row (registry-independent).
        state = client.get(f"/studio/run/{run_id}").json()
        assert state["status"] == "awaiting_selection"
        assert state["selectionRequest"]["kind"] == "artwork"
        assert state["selectionRequest"]["options"] == options

        # Bad picks are refused; nothing resumes.
        assert client.post(
            f"/studio/campaign/{run_id}/select-artwork", json={}
        ).status_code == 400
        assert client.post(
            f"/studio/campaign/{run_id}/select-artwork", json={"assetId": "ghost"}
        ).status_code == 400
        assert client.post(
            "/studio/campaign/team-camp_none-x/select-artwork", json={"assetId": "a1"}
        ).status_code == 404
    finally:
        _cleanup(tenant, [run_id])
