"""Cross-industry brand-study tests (client direction, PA meeting 2026-07-11).

Covers the deterministic principle library, the config gate (disabled tenants
keep tattoo-only behavior), the live-enrichment path with a fake provider (real
sources only, never fabricated), and the rendered brief block.
"""

from __future__ import annotations

from research.providers._http import SearchResult
from studio.brand_study import (
    principles_for,
    render_brand_study_block,
    study_cross_industry,
    study_for_tenant,
)


def test_principles_cover_the_three_objectives_and_drop_unknowns():
    p = principles_for(("followers", "engagement", "sales"))
    objs = {x["objective"] for x in p}
    assert objs == {"followers", "engagement", "sales"}
    assert all(x["hook"] and x["why"] for x in p)
    # Unknown objective is dropped, never invented.
    assert principles_for(("nonsense",)) == []
    # Empty request -> all three (the client's 'mix of all of those things').
    assert {x["objective"] for x in principles_for(())} == {
        "followers", "engagement", "sales"
    }


def test_study_without_provider_is_principles_only_and_honest():
    study = study_cross_industry(objectives=("engagement",), env={})
    assert study["principles"] and study["sources"] == []
    assert "principle library only" in study["note"]


class _FakeProvider:
    def __init__(self, hits):
        self._hits = hits
        self.queries = []

    def search(self, query, *, limit=5):
        self.queries.append(query)
        return self._hits[:limit]


def test_study_with_provider_adds_only_real_sources():
    prov = _FakeProvider([
        SearchResult(url="https://skims.com/press/growth", title="Skims growth",
                     snippet="community-first drops"),
    ])
    study = study_cross_industry(
        objectives=("followers", "sales"),
        seed_brands=("Skims",), industries=("fashion",),
        provider=prov,
    )
    assert study["sources"] and all(s["url"] for s in study["sources"])
    assert study["sources"][0]["url"] == "https://skims.com/press/growth"
    # Both a brand and an industry were researched (real queries, nothing faked).
    assert any("Skims" in q for q in prov.queries)
    assert any("fashion" in q for q in prov.queries)


def test_study_for_tenant_is_none_when_disabled(monkeypatch):
    import config.loader as loader
    from config.schema import BrandStudyConfig, TenantPack, VoiceRef

    disabled = TenantPack(tenant_id="t", display_name="T", voice=VoiceRef(skill="v"))
    monkeypatch.setattr(loader, "load_pack", lambda tid, **k: disabled)
    assert study_for_tenant("t") is None

    enabled = TenantPack(
        tenant_id="t", display_name="T", voice=VoiceRef(skill="v"),
        brand_study=BrandStudyConfig(enabled=True, objectives=("engagement",)),
    )
    monkeypatch.setattr(loader, "load_pack", lambda tid, **k: enabled)
    study = study_for_tenant("t", provider=None, env={})
    assert study is not None and study["principles"]


def test_render_block_blends_and_guards_unsourced_claims():
    assert render_brand_study_block(None) == ""
    study = study_cross_industry(objectives=("followers",), env={})
    block = render_brand_study_block(study)
    assert "CROSS-INDUSTRY MARKETING INTELLIGENCE" in block
    assert "base stays tattoo" in block
    assert "grow followers" in block
    # No fabricated 'brand does X' claim — the guard rail is stated to the drafter.
    assert "never state" in block.lower()
