"""nmh.5 — artwork_memory pgvector round-trip on real Postgres.

Proves the REAL store: ensure_schema -> record (idempotent upsert) -> list (tenant +
is_test scoped) -> search_artwork top-k by cosine similarity. Uses the DeterministicEmbedder
(no model download) so retrieval mechanics are hermetic. Marked integration + skipif no
ENGINE_DATABASE_URL (same convention as the other *_pg tests — set it or these SKIP).
"""

from __future__ import annotations

import os
import uuid

import pytest

from kb.embedding import DeterministicEmbedder
from studio import artwork_memory as am
from studio.artwork_vision import ArtworkAnalysis

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("ENGINE_DATABASE_URL"),
        reason="requires Postgres (set ENGINE_DATABASE_URL)",
    ),
]

_EMB = DeterministicEmbedder()


def _analysis(motif, tags, **over):
    base = dict(
        style="black-and-grey realism", motif=motif, color_mode="black-and-grey",
        placement=None, vibe="bold", linework="crisp", complexity="complex",
        audience_fit="theme-based", campaign_use="full-day", caption_angle="statement",
        style_tags=tags,
    )
    base.update(over)
    return ArtworkAnalysis(**base)


@pytest.fixture
def artist():
    tenant = "skindesign"
    aid = f"itest_art_{uuid.uuid4().hex[:8]}"
    am.ensure_schema()
    yield tenant, aid
    with am._connect() as c:
        c.execute("DELETE FROM artwork_memory WHERE tenant_id=%s AND artist_id=%s",
                  (tenant, aid))


def test_record_list_search_roundtrip_and_idempotent(artist):
    tenant, aid = artist
    id1 = am.record_artwork(tenant, aid, "upload://a/lion.jpg",
                            _analysis("lion", ["lion", "strength", "realism"]), embedder=_EMB)
    am.record_artwork(tenant, aid, "upload://a/rose.jpg",
                      _analysis("rose", ["floral", "rose", "delicate"]), embedder=_EMB)

    # re-ingesting the same image is idempotent (same deterministic id, no dup row)
    id1b = am.record_artwork(tenant, aid, "upload://a/lion.jpg",
                             _analysis("lion", ["lion", "strength", "realism"]), embedder=_EMB)
    assert id1 == id1b
    assert len(am.list_artwork(tenant, aid)) == 2

    # search ranks the lion piece first for a strength query
    hits = am.search_artwork(tenant, aid, "lion strength symbol", k=4, embedder=_EMB)
    assert hits and hits[0].record.image_ref == "upload://a/lion.jpg"
    assert hits[0].record.analysis.motif == "lion"


def test_tenant_and_is_test_scoping(artist):
    tenant, aid = artist
    am.record_artwork(tenant, aid, "upload://a/real.jpg",
                      _analysis("lion", ["lion"]), is_test=False, embedder=_EMB)
    am.record_artwork(tenant, aid, "upload://a/test.jpg",
                      _analysis("lion", ["lion"]), is_test=True, embedder=_EMB)

    # real reads exclude test rows by default
    real = am.list_artwork(tenant, aid)
    assert [r.image_ref for r in real] == ["upload://a/real.jpg"]
    assert len(am.list_artwork(tenant, aid, include_test=True)) == 2

    # a different tenant never sees this artist's artwork
    assert am.list_artwork("other-tenant", aid) == []
    assert am.search_artwork("other-tenant", aid, "lion", embedder=_EMB) == []

    # search also excludes test rows by default
    hits = am.search_artwork(tenant, aid, "lion", embedder=_EMB)
    assert [h.record.image_ref for h in hits] == ["upload://a/real.jpg"]
