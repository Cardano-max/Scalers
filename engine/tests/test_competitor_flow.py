"""Competitor TOP-PICKS + mid-run selection pause (IG competitor-intelligence flow).

Pure parts (DB-free): the never-copy verbatim guard, the ig channel-plan getter
(codes against the forthcoming ``plan.channel_plans`` contract with an honest
fallback), the style-match theme-term merge, and the ``competitor_pick``
selection-request payload shape.

Postgres integration (skipif no ENGINE_DATABASE_URL, same convention as
test_artwork_pause): the request/pause/resume state machine over the durable
``competitor_selections`` row; honest 'skip' on an empty ``competitor_posts``
table (never a pause nobody can answer, never a fabricated post); scoring order
respected (highest total_score first, options capped at 6); the molder step
recording the reference post id WITHOUT copying the competitor caption verbatim
into its draft output; style-matched artwork ranking (the chosen post's
visual_tags + plan image_style feed the EXISTING artwork top-k); the run-state
route surfacing ``competitorSelectionRequest`` and the select-competitor route
validating picks; and the full IG two-pause drive (competitor pick → mold →
style-matched artwork pick → compose), all offline.

No model key needed: the mold's deterministic path is complete on its own — the
clamped cell refinement is honestly skipped keyless.
"""

from __future__ import annotations

import json
import os
import uuid
from types import SimpleNamespace

import pytest

_pg = pytest.mark.skipif(
    not os.environ.get("ENGINE_DATABASE_URL"),
    reason="requires Postgres (set ENGINE_DATABASE_URL)",
)

DSN = os.environ.get(
    "ENGINE_DATABASE_URL", "postgresql://scalers:scalers@localhost:5432/scalers"
)

_CAPTION_TOP = (
    "Would you sit six hours for this piece?\n"
    "A fine-line peony over the forearm, healed and photographed.\n"
    "DM us 'PEONY' today — link in bio."
)


def _cleanup(tenant: str, run_ids: list[str]) -> None:
    import psycopg

    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("DELETE FROM competitor_posts WHERE tenant_id=%s", (tenant,))
        c.execute("DELETE FROM context_artifacts WHERE tenant_id=%s", (tenant,))
        c.execute("DELETE FROM memories WHERE tenant_id=%s", (tenant,))
        c.execute("DELETE FROM assets WHERE campaign_id=%s", (f"portfolio:{tenant}",))
        c.execute("DELETE FROM actions WHERE tenant_id=%s", (tenant,))
        c.execute("DELETE FROM customers WHERE tenant_id=%s", (tenant,))
        for rid in run_ids:
            c.execute("DELETE FROM competitor_selections WHERE run_id=%s", (rid,))
            c.execute("DELETE FROM artwork_selections WHERE run_id=%s", (rid,))
            c.execute("DELETE FROM agent_runs WHERE run_id=%s", (rid,))
            c.execute("DELETE FROM runs WHERE run_id=%s", (rid,))
            c.execute("DELETE FROM campaign_blueprints WHERE run_id=%s", (rid,))


def _seed_competitor_posts(tenant: str, n: int = 7) -> dict:
    """Seed ``n`` operator-provided posts (the ONLY data source — the upload
    seam) with distinct engagement so the deterministic order is knowable: post 0
    has the highest engagement rate, descending from there. Returns the ingest
    counts (deterministic ids derive from the urls)."""
    from studio.competitor_intel import ingest_competitor_csv

    likes = [1000, 800, 600, 400, 300, 200, 100]
    rows = []
    for i in range(n):
        rows.append(
            {
                "handle": f"rivalink{i}",
                "url": f"https://example.com/rival/{tenant}/{i}",
                "platform": "instagram",
                "caption": _CAPTION_TOP if i == 0 else f"Plain studio photo number {i}.",
                "likes": likes[i % len(likes)],
                "views": 10000,
                "visual_tags": ["fine-line", "peony"] if i == 0 else ["blackwork", "skull"],
            }
        )
    return ingest_competitor_csv(tenant, json.dumps(rows), dsn=DSN)


def _seed_artwork(tenant: str, artist: str = "Bella") -> tuple[str, str]:
    """Two REAL library pieces: one whose tags overlap the top competitor post's
    visual pattern (fine-line/peony) and one that does not (script). Returns
    (matching_id, other_id)."""
    from team.store import TeamStore

    store = TeamStore(DSN)
    store.setup()
    match_id = f"art_cmptest_{tenant}_match"
    other_id = f"art_cmptest_{tenant}_other"
    for aid, styles, motifs in (
        (match_id, ["fine-line", "floral"], ["peony"]),
        (other_id, ["script", "lettering"], ["quote"]),
    ):
        store.record_asset(
            id=aid,
            campaign_id=f"portfolio:{tenant}",
            asset_type="studio_artwork",
            content={
                "artist": artist,
                "image_ref": f"artifact://{aid}",
                "caption": f"piece {aid}",
                "styles": styles,
                "motifs": motifs,
                "source": "upload",
                "vlm_summary": f"vlm {aid}",
                "artifact_id": f"img_{aid}",
            },
            status="library",
        )
    return match_id, other_id


def _ig_plan(channel_plans: dict, **kw):
    """A CampaignPlan carrying the FORTHCOMING ``channel_plans`` contract
    (another builder lands the real field) — a subclass, exactly the shape the
    production getter reads via getattr."""
    from pydantic import Field

    from studio.agui import CampaignPlan

    class PlanWithChannels(CampaignPlan):
        channel_plans: dict[str, dict] = Field(default_factory=dict)

    base = dict(
        goal="create an Instagram post for Bella",
        audience="followers",
        channels=["instagram"],
        artist="Bella",
        channel_plans=channel_plans,
    )
    base.update(kw)
    return PlanWithChannels(**base)


def _fake_run_and_trace(calls: list, campaign_id: str):
    def _fake(**kw):
        calls.append(kw)
        return {
            "run_id": kw.get("run_id"), "campaign_id": campaign_id,
            "archetype_id": kw.get("archetype_id"), "agent_runs": [],
            "n_pending": 0, "n_queued": 0, "channels": ["instagram"],
            "step_notes": [], "runs_row": False,
            "run_status": "completed", "failure_summary": [],
        }

    return _fake


# --------------------------------------------------------------------------- #
# Pure: never-copy guard, plan getter, theme-term merge, payload shape.
# --------------------------------------------------------------------------- #
def test_copies_verbatim_catches_caption_and_sentence_reuse():
    from studio.competitor_flow import copies_verbatim

    # The whole caption, a single 4+-word sentence, and punctuation/case-mangled
    # reuse are all caught; fresh wording and short common fragments are not.
    assert copies_verbatim(_CAPTION_TOP, _CAPTION_TOP)
    assert copies_verbatim(
        "our take: would you sit six hours for this piece? book now", _CAPTION_TOP
    )
    assert copies_verbatim(
        "WOULD YOU SIT SIX HOURS FOR THIS PIECE", _CAPTION_TOP
    )
    assert not copies_verbatim(
        "Fresh ink in our own voice — fine-line peony season is open.", _CAPTION_TOP
    )
    assert not copies_verbatim("", _CAPTION_TOP)
    assert not copies_verbatim("link in bio", _CAPTION_TOP)  # < 4-word fragment


def test_ig_channel_plan_getter_reads_contract_with_honest_fallback():
    from studio.competitor_flow import ig_channel_plan

    cfg = {"competitor_research": True, "attach_images": False, "image_style": "fine-line"}
    assert ig_channel_plan(SimpleNamespace(channel_plans={"ig": cfg})) == cfg
    # Every degenerate shape reads as {} — existing plans keep today's behavior.
    assert ig_channel_plan(SimpleNamespace()) == {}
    assert ig_channel_plan(SimpleNamespace(channel_plans=None)) == {}
    assert ig_channel_plan(SimpleNamespace(channel_plans="ig")) == {}
    assert ig_channel_plan(SimpleNamespace(channel_plans={"ig": "on"})) == {}
    assert ig_channel_plan(SimpleNamespace(channel_plans={"email": {}})) == {}


def test_competitor_theme_terms_merges_tags_with_image_style():
    from studio.competitor_flow import competitor_theme_terms

    pick = {"visualTags": ["fine-line", "peony", "close-up"]}
    # Chosen post's REAL tags + the plan's image_style words, de-duped, bounded.
    assert competitor_theme_terms(pick, "fine-line botanical") == [
        "fine-line", "peony", "close-up", "botanical"
    ]
    assert competitor_theme_terms(pick, None) == ["fine-line", "peony", "close-up"]
    assert competitor_theme_terms(None, "blackwork") == ["blackwork"]
    assert competitor_theme_terms({}, "") == []
    # Bounded at 16 terms like theme_terms_from_plan.
    many = {"visualTags": [f"tag{i}" for i in range(30)]}
    assert len(competitor_theme_terms(many, "extra")) == 16


def test_selection_request_payload_shape():
    from studio.competitor_flow import selection_request_payload

    opts = [{"postId": "cmp_x", "handle": "rival", "caption": "c", "url": None,
             "metrics": {}, "totalScore": 5.0, "whyItWorked": "w", "visualTags": []}]
    payload = selection_request_payload({"question": "pick one", "options": opts})
    assert payload == {"kind": "competitor_pick", "question": "pick one", "options": opts}
    # Honest-empty row still renders the contract shape.
    assert selection_request_payload({}) == {
        "kind": "competitor_pick", "question": "", "options": []
    }


# --------------------------------------------------------------------------- #
# PG: gate skip / pause / resume state machine.
# --------------------------------------------------------------------------- #
@_pg
def test_gate_skips_honestly_on_empty_table():
    from studio.competitor_flow import NO_COMPETITOR_NOTE, competitor_gate, get_selection

    tenant = "test_cmpskip_" + uuid.uuid4().hex[:8]
    run_id = f"team-camp_{uuid.uuid4().hex[:8]}-{uuid.uuid4().hex[:8]}"
    try:
        state, note = competitor_gate(run_id, tenant, "sess-skip", None, dsn=DSN)
        assert state == "skip"
        assert note == NO_COMPETITOR_NOTE
        assert "upload the competitor export" in note
        # No pause row was persisted — nothing for the operator to answer.
        assert get_selection(run_id, dsn=DSN) is None
    finally:
        _cleanup(tenant, [run_id])


@_pg
def test_pause_then_resume_state_machine():
    from studio.competitor_flow import competitor_gate, get_selection, record_choice

    tenant = "test_cmpsm_" + uuid.uuid4().hex[:8]
    run_id = f"team-camp_{uuid.uuid4().hex[:8]}-{uuid.uuid4().hex[:8]}"
    try:
        _seed_competitor_posts(tenant)
        plan = SimpleNamespace(artist="Bella", goal="book fine-line clients")

        # 1) First drive: PAUSE with the scored options, durably persisted.
        state, payload = competitor_gate(run_id, tenant, "sess-sm", plan, dsn=DSN)
        assert state == "pause"
        assert payload["kind"] == "competitor_pick"
        options = payload["options"]
        assert 1 <= len(options) <= 6
        assert all(
            set(o.keys()) == {"postId", "handle", "caption", "url", "metrics",
                              "totalScore", "whyItWorked", "visualTags"}
            for o in options
        )
        # The operator reviews the REAL post: caption verbatim, evidence attached.
        assert options[0]["caption"] == _CAPTION_TOP
        assert options[0]["metrics"] == {"likes": 1000, "views": 10000}
        assert options[0]["whyItWorked"]
        sel = get_selection(run_id, dsn=DSN)
        assert sel is not None and sel["status"] == "awaiting"

        # 2) A re-entrant drive pauses again on the SAME durable row.
        state2, payload2 = competitor_gate(run_id, tenant, "sess-sm", plan, dsn=DSN)
        assert state2 == "pause"
        assert [o["postId"] for o in payload2["options"]] == [
            o["postId"] for o in options
        ]

        # 3) The operator picks; the gate then CONTINUES with the real post.
        pick_id = options[0]["postId"]
        assert record_choice(run_id, pick_id, dsn=DSN)
        state3, chosen = competitor_gate(run_id, tenant, "sess-sm", plan, dsn=DSN)
        assert state3 == "continue"
        assert chosen["postId"] == pick_id
        assert chosen["caption"] == _CAPTION_TOP  # the REAL row, re-read live
        assert chosen["visualTags"] == ["fine-line", "peony"]

        # 4) A second record is refused (already selected — never re-paused).
        assert record_choice(run_id, pick_id, dsn=DSN) is False
    finally:
        _cleanup(tenant, [run_id])


@_pg
def test_scoring_order_respected_and_capped_at_six():
    from studio.competitor_flow import top_competitor_options

    tenant = "test_cmporder_" + uuid.uuid4().hex[:8]
    try:
        _seed_competitor_posts(tenant, n=7)
        options = top_competitor_options(tenant, dsn=DSN)
        assert len(options) == 6  # 7 on file, capped
        scores = [o["totalScore"] for o in options]
        assert all(s is not None for s in scores)
        assert scores == sorted(scores, reverse=True)  # highest total_score first
        # Post 0 (10% engagement rate, strongest hook/CTA signals) ranks first.
        assert options[0]["handle"] == "rivalink0"
    finally:
        _cleanup(tenant, [])


# --------------------------------------------------------------------------- #
# PG: the MOLD step — reference recorded, caption NEVER copied.
# --------------------------------------------------------------------------- #
@_pg
def test_molder_records_reference_and_never_copies_caption(monkeypatch):
    import psycopg
    from psycopg.rows import dict_row

    from studio.competitor_flow import (
        copies_verbatim,
        mold_competitor_pattern,
        top_competitor_options,
    )

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # deterministic mold
    tenant = "test_cmpmold_" + uuid.uuid4().hex[:8]
    run_id = f"team-camp_{uuid.uuid4().hex[:8]}-{uuid.uuid4().hex[:8]}"
    try:
        _seed_competitor_posts(tenant)
        pick = top_competitor_options(tenant, dsn=DSN)[0]
        plan = SimpleNamespace(artist="Bella", goal="book fine-line clients")

        mold = mold_competitor_pattern(
            tenant, plan, pick, run_id=run_id, campaign_id="camp_x", dsn=DSN
        )
        # The reference is traceable; the output is OURS — never the caption.
        assert mold["reference_post_id"] == pick["postId"]
        assert mold["draft_output"] != pick["caption"]
        for field in ("draft_output", "hook", "cta"):
            assert not copies_verbatim(mold[field], pick["caption"])
        # Shape carried over as LABELS (hook/context/cta), not sentences.
        assert mold["structure"][0] == "hook"
        assert mold["llm_refined"] is False  # keyless: honest deterministic mold

        # ONE role='molder' agent_run, input carrying the reference caption/tags.
        with psycopg.connect(DSN, autocommit=True, row_factory=dict_row) as c:
            rows = c.execute(
                "SELECT input, output FROM agent_runs WHERE run_id=%s AND role='molder'",
                (run_id,),
            ).fetchall()
        assert len(rows) == 1
        assert rows[0]["input"]["reference_post_id"] == pick["postId"]
        assert rows[0]["input"]["caption"] == pick["caption"]
        assert rows[0]["input"]["visual_tags"] == pick["visualTags"]
        assert rows[0]["output"]["draft_output"] == mold["draft_output"]

        # A resume re-record is a no-op (deterministic id, DO NOTHING).
        mold_competitor_pattern(
            tenant, plan, pick, run_id=run_id, campaign_id="camp_x", dsn=DSN
        )
        with psycopg.connect(DSN, autocommit=True) as c:
            n = c.execute(
                "SELECT count(*) FROM agent_runs WHERE run_id=%s AND role='molder'",
                (run_id,),
            ).fetchone()[0]
        assert n == 1
    finally:
        _cleanup(tenant, [run_id])


# --------------------------------------------------------------------------- #
# PG: style-match — the chosen post's visual pattern ranks OUR matching pieces.
# --------------------------------------------------------------------------- #
@_pg
def test_style_match_ranks_overlapping_own_assets_first():
    from studio.artwork_flow import top_artwork_options
    from studio.competitor_flow import competitor_theme_terms

    tenant = "test_cmpstyle_" + uuid.uuid4().hex[:8]
    try:
        match_id, other_id = _seed_artwork(tenant)
        pick = {"visualTags": ["fine-line", "peony"]}
        terms = competitor_theme_terms(pick, "fine-line")
        opts = top_artwork_options(tenant, artist="Bella", theme_terms=terms, dsn=DSN)
        assert [o["assetId"] for o in opts] == [match_id, other_id]
        # The winning why traces to the overlapping tags — grounded, not invented.
        assert "fine-line" in opts[0]["why"]
    finally:
        _cleanup(tenant, [])


# --------------------------------------------------------------------------- #
# PG: run-state route + select-competitor route (mirrors select-artwork).
# --------------------------------------------------------------------------- #
@_pg
def test_run_route_surfaces_competitor_selection_and_select_route_validates(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from studio.agui import mount_studio_agui
    from studio.competitor_flow import request_selection

    tenant = "test_cmproute_" + uuid.uuid4().hex[:8]
    run_id = f"team-camp_{uuid.uuid4().hex[:8]}-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("STUDIO_TENANT_ID", tenant)
    monkeypatch.setenv("ENGINE_DATABASE_URL", DSN)
    app = FastAPI()
    mount_studio_agui(app)
    client = TestClient(app)
    try:
        options = [{"postId": "cmp_p1", "handle": "rival", "caption": "real caption",
                    "url": None, "metrics": {"likes": 10}, "totalScore": 7.5,
                    "whyItWorked": "w", "visualTags": ["fine-line"]}]
        request_selection(
            run_id, tenant, "sess-r",
            question="I scored 1 competitor post — which pattern should I mold?",
            options=options, dsn=DSN,
        )
        # The poller reads the pause from the DURABLE row (registry-independent),
        # beside (not inside) the artwork selectionRequest.
        state = client.get(f"/studio/run/{run_id}").json()
        assert state["status"] == "awaiting_selection"
        assert state["selectionRequest"] is None
        assert state["competitorSelectionRequest"]["kind"] == "competitor_pick"
        assert state["competitorSelectionRequest"]["options"] == options

        # Bad picks are refused; nothing resumes.
        assert client.post(
            f"/studio/campaign/{run_id}/select-competitor", json={}
        ).status_code == 400
        assert client.post(
            f"/studio/campaign/{run_id}/select-competitor", json={"postId": "ghost"}
        ).status_code == 400
        assert client.post(
            "/studio/campaign/team-camp_none-x/select-competitor",
            json={"postId": "cmp_p1"},
        ).status_code == 404
    finally:
        _cleanup(tenant, [run_id])


# --------------------------------------------------------------------------- #
# PG: the full IG drive — pause #1 (competitor) → mold → pause #2 (style-matched
# artwork) → compose. Everything offline; the compose spine is faked.
# --------------------------------------------------------------------------- #
@_pg
def test_instagram_two_pause_flow(monkeypatch):
    import psycopg
    from psycopg.rows import dict_row

    from studio import artwork_flow, campaign_runner, competitor_flow
    from studio.agui import _execute_campaign_sync
    from studio.competitor_flow import copies_verbatim

    monkeypatch.setenv("SCALERS_EMBEDDER", "deterministic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    tenant = "test_cmpig_" + uuid.uuid4().hex[:8]
    campaign_id = f"camp_{uuid.uuid4().hex[:12]}"
    run_id = f"team-{campaign_id}-{uuid.uuid4().hex[:12]}"
    calls: list[dict] = []
    monkeypatch.setattr(campaign_runner, "run_and_trace", _fake_run_and_trace(calls, campaign_id))
    try:
        _seed_competitor_posts(tenant)
        match_id, _other_id = _seed_artwork(tenant)
        plan = _ig_plan({"ig": {"competitor_research": True, "attach_images": True,
                                "image_style": "fine-line"}})

        # 1) PAUSE #1: the scored competitor options, BEFORE molding/drafting —
        #    and BEFORE the artwork gate (no artwork row exists yet).
        summary = _execute_campaign_sync(plan, "sess-cig", tenant, DSN, run_id)
        assert summary["run_status"] == "awaiting_selection"
        req1 = summary["selection_request"]
        assert req1["kind"] == "competitor_pick"
        assert calls == []  # compose spine untouched while paused
        assert artwork_flow.get_selection(run_id, dsn=DSN) is None
        pick = req1["options"][0]
        assert pick["caption"] == _CAPTION_TOP  # verbatim for the OPERATOR only

        # 2) Operator picks the pattern → PAUSE #2: the artwork TOP options,
        #    style-matched to the chosen post's visual_tags + plan image_style,
        #    from OUR OWN portfolio.
        assert competitor_flow.record_choice(run_id, pick["postId"], dsn=DSN)
        summary2 = _execute_campaign_sync(plan, "sess-cig", tenant, DSN, run_id)
        assert summary2["run_status"] == "awaiting_selection"
        req2 = summary2["selection_request"]
        assert req2["kind"] == "artwork"
        assert calls == []
        assert req2["options"][0]["assetId"] == match_id  # style-matched OWN piece

        # 3) Operator picks the artwork → the compose spine runs ONCE with the
        #    molded pattern on the brief and the pick on the summary.
        assert artwork_flow.record_choice(
            run_id, req2["options"][0]["assetId"],
            artifact_id=req2["options"][0]["artifactId"], dsn=DSN,
        )
        summary3 = _execute_campaign_sync(plan, "sess-cig", tenant, DSN, run_id)
        assert len(calls) == 1 and calls[0]["run_id"] == run_id
        assert summary3["artwork"]["assetId"] == match_id
        assert summary3["competitor_pick"]["postId"] == pick["postId"]
        # The brief carries the MOLD orders, never the competitor's sentences.
        brief = calls[0]["brief"]
        assert "COMPETITOR PATTERN (operator-picked, MOLDED to our brand)" in brief
        assert "NEVER copy competitor sentences" in brief
        assert _CAPTION_TOP.splitlines()[0] not in brief

        # The molder step is REAL: one role='molder' agent_run, reference post id
        # recorded, draft output never the competitor caption.
        with psycopg.connect(DSN, autocommit=True, row_factory=dict_row) as c:
            rows = c.execute(
                "SELECT input, output FROM agent_runs WHERE run_id=%s AND role='molder'",
                (run_id,),
            ).fetchall()
        assert len(rows) == 1
        assert rows[0]["output"]["reference_post_id"] == pick["postId"]
        assert rows[0]["output"]["draft_output"] != pick["caption"]
        assert not copies_verbatim(rows[0]["output"]["draft_output"], pick["caption"])
    finally:
        _cleanup(tenant, [run_id])


@_pg
def test_attach_images_false_skips_artwork_pause(monkeypatch):
    from studio import artwork_flow, campaign_runner, competitor_flow
    from studio.agui import _execute_campaign_sync

    monkeypatch.setenv("SCALERS_EMBEDDER", "deterministic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    tenant = "test_cmpnoimg_" + uuid.uuid4().hex[:8]
    campaign_id = f"camp_{uuid.uuid4().hex[:12]}"
    run_id = f"team-{campaign_id}-{uuid.uuid4().hex[:12]}"
    calls: list[dict] = []
    monkeypatch.setattr(campaign_runner, "run_and_trace", _fake_run_and_trace(calls, campaign_id))
    try:
        _seed_competitor_posts(tenant)
        _seed_artwork(tenant)  # artwork EXISTS — the plan still declines pause #2
        plan = _ig_plan({"ig": {"competitor_research": True, "attach_images": False}})

        summary = _execute_campaign_sync(plan, "sess-cni", tenant, DSN, run_id)
        assert summary["run_status"] == "awaiting_selection"
        assert summary["selection_request"]["kind"] == "competitor_pick"
        pick = summary["selection_request"]["options"][0]

        assert competitor_flow.record_choice(run_id, pick["postId"], dsn=DSN)
        summary2 = _execute_campaign_sync(plan, "sess-cni", tenant, DSN, run_id)
        # NO pause #2: the run went straight to compose, honestly noting why.
        assert len(calls) == 1
        assert summary2["run_status"] != "awaiting_selection"
        assert summary2["artwork"] is None
        assert "attach_images=false" in (summary2.get("artwork_note") or "")
        assert artwork_flow.get_selection(run_id, dsn=DSN) is None
    finally:
        _cleanup(tenant, [run_id])


@_pg
def test_competitor_research_with_empty_table_continues_normal_path(monkeypatch):
    from studio import campaign_runner
    from studio.agui import _execute_campaign_sync
    from studio.competitor_flow import NO_COMPETITOR_NOTE, get_selection

    monkeypatch.setenv("SCALERS_EMBEDDER", "deterministic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    tenant = "test_cmpempty_" + uuid.uuid4().hex[:8]
    campaign_id = f"camp_{uuid.uuid4().hex[:12]}"
    run_id = f"team-{campaign_id}-{uuid.uuid4().hex[:12]}"
    calls: list[dict] = []
    monkeypatch.setattr(campaign_runner, "run_and_trace", _fake_run_and_trace(calls, campaign_id))
    try:
        _seed_artwork(tenant)
        plan = _ig_plan({"ig": {"competitor_research": True, "attach_images": True}})

        # No competitor posts on file: pause #1 is SKIPPED with the visible note;
        # the normal IG path (artwork pause included) proceeds unchanged.
        summary = _execute_campaign_sync(plan, "sess-cet", tenant, DSN, run_id)
        assert summary["run_status"] == "awaiting_selection"
        assert summary["selection_request"]["kind"] == "artwork"  # normal IG pause
        assert get_selection(run_id, dsn=DSN) is None  # no competitor row persisted

        from studio import artwork_flow

        req = summary["selection_request"]
        assert artwork_flow.record_choice(
            run_id, req["options"][0]["assetId"],
            artifact_id=req["options"][0]["artifactId"], dsn=DSN,
        )
        summary2 = _execute_campaign_sync(plan, "sess-cet", tenant, DSN, run_id)
        assert len(calls) == 1
        # The skip is VISIBLE, not silent — and nothing was fabricated.
        assert any(NO_COMPETITOR_NOTE in n for n in summary2.get("step_notes", []))
        assert "competitor_pick" not in summary2
    finally:
        _cleanup(tenant, [run_id])
