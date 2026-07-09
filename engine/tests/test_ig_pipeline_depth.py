"""IG pipeline depth (engine-core item 6, real PG): artist-memory + trend grounding
in the compose brief, channel-crew agent_runs, and post-staging context enrichment.

* ``build_ig_brief_block`` injects REAL artist memory (roster + real past campaigns
  + artwork tags + memories) and LIVE trend research into the brief — honest-empty
  in both directions (unknown artist / no research sources) and records
  ``artist_memory``/``trend_research`` agent_runs with deterministic ids (resume-safe);
* the routed Instagram run passes the grounded brief into the compose spine and
  emits the channel-specific crew rows;
* ``enrich_post_actions`` lands artist + selected artwork + grounded hashtags/CTA
  on every staged post action's context.

Trend research is monkeypatched at the provider seam — no live Firecrawl calls.
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
        c.execute("DELETE FROM artists WHERE tenant_id=%s", (tenant,))
        c.execute("DELETE FROM campaign_examples WHERE tenant_id=%s", (tenant,))
        c.execute("DELETE FROM assets WHERE campaign_id=%s", (f"portfolio:{tenant}",))
        c.execute("DELETE FROM actions WHERE tenant_id=%s", (tenant,))
        for rid in run_ids:
            c.execute("DELETE FROM agent_runs WHERE run_id=%s", (rid,))
            c.execute("DELETE FROM artwork_selections WHERE run_id=%s", (rid,))
            c.execute("DELETE FROM actions WHERE run_id=%s", (rid,))


def _seed_artist_world(tenant: str) -> None:
    import psycopg

    from team.store import TeamStore

    with psycopg.connect(DSN, autocommit=True) as c:
        aid = f"art_test_{uuid.uuid4().hex[:12]}"
        c.execute(
            "INSERT INTO artists (id, tenant_id, name, email) VALUES (%s,%s,%s,%s)",
            (aid, tenant, "Bella", "bella@example.com"),
        )
        c.execute(
            "INSERT INTO artist_studios (artist_id, studio_name) VALUES (%s,%s)",
            (aid, "Skin Design Tattoo Test"),
        )
        c.execute(
            "INSERT INTO campaign_examples (id, tenant_id, campaign_name, artist_name, "
            "offer_price_usd, message_copy, cta, sent_at, delivered_count) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (f"cex_test_{uuid.uuid4().hex[:10]}", tenant, "06.29 BELLA", "Bella",
             1200, "Bella has one opening this week", "Reply YES", "06.29 2:15 pm", 41),
        )
    store = TeamStore(DSN)
    store.setup()
    store.record_asset(
        id=f"art_igdepth_{tenant}_00",
        campaign_id=f"portfolio:{tenant}",
        asset_type="studio_artwork",
        content={
            "artist": "Bella", "image_ref": "artifact://art_img_x",
            "caption": "fine-line peony", "styles": ["fine-line"],
            "motifs": ["peony"], "source": "upload",
            "vlm_summary": "fine-line; motif: peony", "artifact_id": "art_img_x",
        },
        status="library",
    )


class _Hit:
    def __init__(self, title, snippet, url):
        self.title, self.snippet, self.url = title, snippet, url


def _arm_fake_firecrawl(monkeypatch, hits):
    class _Provider:
        enabled = True

        def search(self, query, limit=5):
            return list(hits)

    monkeypatch.setattr(
        "research.pipeline.live_registry", lambda *a, **k: {"firecrawl": _Provider()}
    )


def test_brief_block_grounds_artist_memory_and_cited_trends(monkeypatch):
    from studio.agui import CampaignPlan
    from studio.ig_pipeline import build_ig_brief_block

    tenant = "test_igdepth_" + uuid.uuid4().hex[:8]
    campaign_id = f"camp_{uuid.uuid4().hex[:12]}"
    run_id = f"team-{campaign_id}-{uuid.uuid4().hex[:12]}"
    monkeypatch.setenv("SCALERS_EMBEDDER", "deterministic")
    _arm_fake_firecrawl(monkeypatch, [
        _Hit("Fine-line boom", "fine-line tattoos trending on IG reels", "https://ex.com/t1"),
    ])
    try:
        _seed_artist_world(tenant)
        plan = CampaignPlan(goal="instagram post", artist="Bella")
        block = build_ig_brief_block(
            plan, tenant, run_id=run_id, campaign_id=campaign_id, dsn=DSN
        )
        # REAL artist memory, clearly marked.
        assert "ARTIST MEMORY — REAL DATA for Bella" in block
        assert "Skin Design Tattoo Test" in block
        assert "06.29 BELLA" in block and "$1200" in block
        assert "fine-line" in block
        # Cited trend snippets WITH URLs — the deep pass groups three angles
        # (instagram-trends / reddit-community / hooks-and-formats).
        assert "SOCIAL RESEARCH — LIVE cited sources across three angles" in block
        assert "[reddit-community]" in block
        assert "https://ex.com/t1" in block
        # Proven brand patterns block is present (real seeded campaign).
        assert "PROVEN BRAND PATTERNS" in block

        # Channel-crew agent_runs recorded with REAL outputs + deterministic ids.
        import psycopg

        with psycopg.connect(DSN, autocommit=True) as c:
            rows = {
                r[0]: (r[1], r[2]) for r in c.execute(
                    "SELECT role, model, output FROM agent_runs WHERE run_id=%s",
                    (run_id,),
                ).fetchall()
            }
        assert rows["artist_memory"][0] == "db"
        assert rows["artist_memory"][1]["artist"] == "Bella"
        assert rows["trend_research"][0] == "firecrawl"
        assert rows["trend_research"][1]["cited"] == 1
        assert rows["trend_research"][1]["sources"][0]["url"] == "https://ex.com/t1"
        # The deep pass adds hook_research (reddit + formats angles) and the
        # brand_patterns read as their own visible crew steps.
        assert rows["hook_research"][0] == "firecrawl"
        assert rows["hook_research"][1]["total_cited"] >= 1
        assert rows["brand_patterns"][0] == "db"

        # Re-building (a resume) records NO duplicates (deterministic ids).
        build_ig_brief_block(plan, tenant, run_id=run_id, campaign_id=campaign_id, dsn=DSN)
        with psycopg.connect(DSN, autocommit=True) as c:
            n = c.execute(
                "SELECT count(*) FROM agent_runs WHERE run_id=%s", (run_id,)
            ).fetchone()[0]
        assert n == 4
    finally:
        _cleanup(tenant, [run_id])


def test_brief_block_honest_when_no_artist_and_no_trends(monkeypatch):
    from studio.agui import CampaignPlan
    from studio.ig_pipeline import build_ig_brief_block

    tenant = "test_igempty_" + uuid.uuid4().hex[:8]
    _arm_fake_firecrawl(monkeypatch, [])  # research runs but returns nothing
    block = build_ig_brief_block(CampaignPlan(goal="instagram post"), tenant, dsn=DSN)
    assert "the plan names no artist" in block
    assert "returned NO usable sources" in block
    assert "Do NOT reference or invent any trend" in block


def test_instagram_run_injects_brief_and_enriches_post_actions(monkeypatch):
    from studio import campaign_runner
    from studio.agui import CampaignPlan, _execute_campaign_sync
    from studio.artwork_flow import record_choice

    tenant = "test_igrun_" + uuid.uuid4().hex[:8]
    campaign_id = f"camp_{uuid.uuid4().hex[:12]}"
    run_id = f"team-{campaign_id}-{uuid.uuid4().hex[:12]}"
    monkeypatch.setenv("SCALERS_EMBEDDER", "deterministic")
    _arm_fake_firecrawl(monkeypatch, [
        _Hit("Trend", "fine-line reels are up", "https://ex.com/t2"),
    ])
    briefs: list[str] = []

    def _fake_run_and_trace(**kw):
        briefs.append(kw["brief"])
        # The compose spine would stage post actions; fake ONE staged row.
        from actions.store import ensure_schema, record_pending_action

        ensure_schema(DSN)
        record_pending_action(
            tenant_id=tenant, decision_id=None, type="post", channel="instagram",
            worker="compose", target=None, draft="draft body",
            context="Artwork: pre-existing note", conf=None, threshold=None,
            esc_kind="approval_required", esc_label="t",
            idempotency_key=f"{kw.get('run_id')}:post1", run_id=kw.get("run_id"),
            dsn=DSN,
        )
        return {
            "run_id": kw.get("run_id"), "campaign_id": campaign_id,
            "archetype_id": kw.get("archetype_id"), "agent_runs": [],
            "n_pending": 1, "n_queued": 1, "channels": ["instagram"],
            "step_notes": [], "runs_row": False, "run_status": "completed",
            "failure_summary": [],
        }

    monkeypatch.setattr(campaign_runner, "run_and_trace", _fake_run_and_trace)
    try:
        _seed_artist_world(tenant)
        plan = CampaignPlan(
            goal="create an Instagram post for the studio", audience="followers",
            channels=["instagram"], artist="Bella",
        )
        # Pause on the artwork pick, then resume with it.
        s1 = _execute_campaign_sync(plan, "sess-igd", tenant, DSN, run_id)
        assert s1["run_status"] == "awaiting_selection" and briefs == []
        opt = s1["selection_request"]["options"][0]
        assert record_choice(run_id, opt["assetId"], artifact_id=opt["artifactId"], dsn=DSN)
        s2 = _execute_campaign_sync(plan, "sess-igd", tenant, DSN, run_id)

        # (a) the compose brief carried the grounded blocks + the selected artwork.
        assert len(briefs) == 1
        assert "ARTIST MEMORY — REAL DATA for Bella" in briefs[0]
        assert "https://ex.com/t2" in briefs[0]
        assert "SELECTED ARTWORK (operator-picked, REAL)" in briefs[0]

        # (b) channel-specific crew rows exist on the run.
        import psycopg

        with psycopg.connect(DSN, autocommit=True) as c:
            roles = {r[0] for r in c.execute(
                "SELECT role FROM agent_runs WHERE run_id=%s", (run_id,)
            ).fetchall()}
        assert {"artist_memory", "trend_research"} <= roles

        # (c) the staged post action's context carries artist + artwork + hashtags/cta.
        from actions.store import list_actions_for_run

        acts = [a for a in list_actions_for_run(run_id, dsn=DSN) if a.type == "post"]
        assert len(acts) == 1
        ctx = json.loads(acts[0].context)
        assert ctx["artist"] == "Bella"
        assert ctx["artwork"]["assetId"] == opt["assetId"]
        assert ctx["artwork"]["vlmSummary"] == "fine-line; motif: peony"
        assert "finelinetattoo" in ctx["hashtags"]  # grounded off the REAL style tag
        assert ctx["cta"]  # deterministic angle CTA
        assert ctx["note"] == "Artwork: pre-existing note"  # old context preserved
        assert s2["artwork"]["assetId"] == opt["assetId"]
        assert any("attached artist/artwork/hashtags" in n for n in s2["step_notes"])
    finally:
        _cleanup(tenant, [run_id])
