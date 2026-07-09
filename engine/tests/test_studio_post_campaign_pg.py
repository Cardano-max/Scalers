"""Real-Postgres integration for the studio post drafter (P2).

Proves the full round-trip on a REAL Postgres: seed the portfolio into the ``assets``
table, pick a piece, and stage HELD IG/FB ``actions`` rows exactly-once. Marked
``integration`` + ``skipif`` no ``ENGINE_DATABASE_URL`` — same convention as the other
``*_pg`` tests, so it neither hides in CI nor breaks the DB-free unit run. Uses a fixed
throwaway tenant so re-runs are idempotent (exactly-once) and never touch live data.
"""

from __future__ import annotations

import os

import pytest

from actions.store import get_action, list_actions_for_run
from studio.artwork_select import ARTWORK_ASSET_TYPE, list_artwork, seed_studio_artwork
from studio.post_campaign import draft_studio_posts

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("ENGINE_DATABASE_URL"),
        reason="requires Postgres (set ENGINE_DATABASE_URL)",
    ),
]

_TENANT = "p2_pg_test"


def test_seed_then_stage_held_ig_fb_exactly_once():
    seeded = seed_studio_artwork(_TENANT)
    assert seeded, "seed should create/ensure portfolio assets"

    arts = list_artwork(_TENANT, "Maya")
    assert arts, "Maya should have seeded portfolio pieces"
    assert all(a.artist == "Maya" for a in arts)

    res = draft_studio_posts(tenant_id=_TENANT, artist_name="Maya", theme="floral")
    assert res["has_artwork"] is True
    assert len(res["drafts"]) == 2

    # Every staged row is a REAL, HELD (pending) post row bound to the chosen asset.
    used_asset = res["artwork"]["asset_id"]
    for d in res["drafts"]:
        row = get_action(d["action_id"])
        assert row is not None
        assert row.type == "post"
        assert row.channel in ("instagram", "facebook")
        assert row.status == "pending"            # HELD — nothing sent
        assert row.is_seeded is False             # a real staged draft, not demo data
        assert used_asset in (row.context or "")  # artwork traceable on the row

    # Exactly-once: a re-run returns the SAME ids and adds no rows.
    ids1 = sorted(d["action_id"] for d in res["drafts"])
    res2 = draft_studio_posts(tenant_id=_TENANT, artist_name="Maya", theme="floral")
    ids2 = sorted(d["action_id"] for d in res2["drafts"])
    assert ids1 == ids2
    run_rows = list_actions_for_run(res["run_id"])
    assert len(run_rows) == 2


def test_asset_rows_are_library_typed_not_a_queued_send():
    seed_studio_artwork(_TENANT)
    from team.store import TeamStore
    from studio.artwork_select import _dsn, _portfolio_campaign_id

    rows = TeamStore(_dsn()).list_assets(_portfolio_campaign_id(_TENANT))
    art_rows = [r for r in rows if r["asset_type"] == ARTWORK_ASSET_TYPE]
    assert art_rows
    for r in art_rows:
        assert r["status"] == "library"  # portfolio item, never 'queued'/'sent'


def test_artist_with_no_portfolio_stages_honest_no_artwork_posts():
    # 'Ghost' is never seeded, so the library is empty for them.
    res = draft_studio_posts(tenant_id=_TENANT, artist_name="Ghost")
    assert res["has_artwork"] is False and res["artwork"] is None
    assert len(res["drafts"]) == 2
    for d in res["drafts"]:
        row = get_action(d["action_id"])
        assert row is not None and row.status == "pending"
        assert "No artwork on file" in (row.context or "")
