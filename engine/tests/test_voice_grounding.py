"""Brand-voice grounding assembly + dimensions loader (KNOW-02 / a9m.3).

DB-free (always runs in CI). Exercises the degrade ladder (FULL/PARTIAL/SPARSE),
the dimensions loader consuming the writer-emitted ``voice-dimensions.json`` bundle,
the misconfig-raises contract, and the KB-unreachable degrade. The two-tenant
isolation + empty-KB retrieval edges run against real Postgres in
``test_kb_chunks_pg.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from config.schema import TenantPack, VoiceRef
from kb.voice import (
    Exemplar,
    GroundingCoverage,
    VoiceDimensions,
    VoiceDimensionsError,
    VoiceGrounding,
    build_voice_grounding,
    load_voice_dimensions,
)


# ── fixtures ──────────────────────────────────────────────────────────────────


def _pack(tenant: str = "ladies8391", skill: str | None = None) -> TenantPack:
    return TenantPack(
        tenant_id=tenant,
        display_name=tenant,
        voice=VoiceRef(skill=skill or f"brand-voice/{tenant}"),
    )


# A structurally-faithful fill (matches writer's voice-dimensions.json shape: the
# rubric wrapped under "dimensions", sibling keys ignored by the loader).
_FILL = {
    "tenant_id": "ladies8391",
    "source": "skills/brand-voice/tenants/ladies8391/brand-dna.md",
    "dimensions": {
        "tone": ["warm, direct, playful; first-person 'I' (Rae)"],
        "structure": ["short, one idea per line", "open on the client's story"],
        "vocabulary": {
            "prefer": ["made for you", "your story"],
            "ban": ["unleash", "slay", "boss babe"],
            "approved_claims": ["Woman-owned, appointment-only studio in Austin, TX."],
            "emoji_policy": "0-2 per caption, only 🌸 🌷 🤍",
            "hashtag_policy": "3-6, lowercase, specific",
        },
    },
}


@pytest.fixture
def skills_root(tmp_path: Path) -> Path:
    """A temp skills bundle with ladies8391's voice-dimensions.json emitted."""
    d = tmp_path / "brand-voice" / "tenants" / "ladies8391"
    d.mkdir(parents=True)
    (d / "voice-dimensions.json").write_text(json.dumps(_FILL), encoding="utf-8")
    return tmp_path


class FakeKb:
    """A :class:`VoiceExemplarSource` that returns a fixed exemplar list, and records
    the tenant_id it was queried with (proves tenant-scoping is threaded through)."""

    def __init__(self, exemplars: list[Exemplar]) -> None:
        self._exemplars = exemplars
        self.seen_tenant: str | None = None

    def voice_exemplars(self, *, tenant_id: str, query: str, k: int = 5) -> list[Exemplar]:
        self.seen_tenant = tenant_id
        return self._exemplars[:k]


class BrokenKb:
    """A KB that raises — models an unreachable / failing pgvector backend."""

    def voice_exemplars(self, *, tenant_id: str, query: str, k: int = 5) -> list[Exemplar]:
        raise ConnectionError("kb unreachable")


def _ex(n: int) -> list[Exemplar]:
    return [Exemplar(content=f"past post {i}", metrics={"on_voice": True}, similarity=0.9 - i * 0.01) for i in range(n)]


# ── dimensions loader (consume writer's emission) ─────────────────────────────


def test_load_dimensions_from_bundle(skills_root):
    dims = load_voice_dimensions(_pack(), skills_root=skills_root)
    assert isinstance(dims, VoiceDimensions)
    assert "warm, direct, playful; first-person 'I' (Rae)" in dims.tone
    assert "unleash" in dims.vocabulary.ban
    assert dims.vocabulary.approved_claims == ["Woman-owned, appointment-only studio in Austin, TX."]
    # sensitive_ban defaults empty (filled later by the gate-disposition bead).
    assert dims.vocabulary.sensitive_ban == []


def test_missing_emission_raises_not_silent(skills_root):
    # A tenant whose skill bundle emitted no voice-dimensions.json is a misconfig:
    # the loader RAISES rather than returning empty/generic dimensions.
    with pytest.raises(VoiceDimensionsError):
        load_voice_dimensions(_pack(tenant="no-such-tenant"), skills_root=skills_root)


def test_malformed_emission_raises(tmp_path):
    d = tmp_path / "brand-voice" / "tenants" / "ladies8391"
    d.mkdir(parents=True)
    (d / "voice-dimensions.json").write_text("{ not json", encoding="utf-8")
    with pytest.raises(VoiceDimensionsError):
        load_voice_dimensions(_pack(), skills_root=tmp_path)


def test_bad_skill_ref_shape_raises(skills_root):
    with pytest.raises(VoiceDimensionsError):
        load_voice_dimensions(_pack(skill="not-family-tenant-shaped"), skills_root=skills_root)


# ── coverage enum case-reconciliation (ADR lowercase vs contract uppercase) ──


def test_coverage_canonical_value_is_lowercase():
    # ADR PR#38 is canonical: values serialize lowercase.
    assert GroundingCoverage.SPARSE.value == "sparse"
    assert [c.value for c in GroundingCoverage] == ["full", "partial", "sparse"]


def test_coverage_accepts_either_case_on_input():
    # Tolerant boundary: fixtures/JSON built to pmm's earlier uppercase Literal
    # ("FULL"/"PARTIAL"/"SPARSE") still resolve — so a9m.5 validates on first wire.
    for up, member in (("FULL", GroundingCoverage.FULL),
                       ("Partial", GroundingCoverage.PARTIAL),
                       ("sparse", GroundingCoverage.SPARSE)):
        assert GroundingCoverage(up) is member


def test_voice_grounding_validates_uppercase_coverage():
    g = VoiceGrounding.model_validate({
        "tenant_id": "t", "dimensions": {}, "exemplars": [],
        "coverage": "SPARSE", "low_grounding": True, "exemplar_count": 0,
    })
    assert g.coverage is GroundingCoverage.SPARSE
    assert g.coverage.value == "sparse"  # re-serializes canonical


# ── degrade ladder (coverage enum) ────────────────────────────────────────────


def test_coverage_full_at_k_exemplars(skills_root):
    g = build_voice_grounding(_pack(), FakeKb(_ex(5)), query="floral cover-up", k=5, skills_root=skills_root)
    assert g.coverage is GroundingCoverage.FULL
    assert g.low_grounding is False
    assert g.exemplar_count == 5
    assert len(g.exemplars) == 5


def test_coverage_partial_below_k(skills_root):
    g = build_voice_grounding(_pack(), FakeKb(_ex(2)), query="x", k=5, skills_root=skills_root)
    assert g.coverage is GroundingCoverage.PARTIAL
    assert g.low_grounding is False
    assert g.exemplar_count == 2


def test_coverage_sparse_when_empty_flags_low_grounding(skills_root):
    g = build_voice_grounding(_pack(), FakeKb([]), query="x", k=5, skills_root=skills_root)
    assert g.coverage is GroundingCoverage.SPARSE
    assert g.low_grounding is True
    assert g.exemplar_count == 0
    # dimensions are STILL present — never a silent un-grounded draft.
    assert g.dimensions.tone


def test_unreachable_kb_degrades_to_dimensions_only(skills_root):
    g = build_voice_grounding(_pack(), BrokenKb(), query="x", k=5, skills_root=skills_root)
    assert g.coverage is GroundingCoverage.SPARSE
    assert g.low_grounding is True
    assert g.exemplars == []
    assert g.dimensions.vocabulary.ban  # still grounded on the pack ref


def test_grounding_is_tenant_scoped(skills_root):
    fake = FakeKb(_ex(3))
    g = build_voice_grounding(_pack(tenant="ladies8391"), fake, query="x", skills_root=skills_root)
    assert g.tenant_id == "ladies8391"
    assert fake.seen_tenant == "ladies8391"  # the retrieval was scoped to this tenant


def test_misconfig_skill_raises_through_assembly(skills_root):
    # If the skill cannot resolve dimensions at all, assembly surfaces it (never a
    # silent generic draft) — the contract's "if even the skill fails, that is a
    # misconfig the slice surfaces" clause.
    with pytest.raises(VoiceDimensionsError):
        build_voice_grounding(_pack(tenant="ghost"), FakeKb(_ex(3)), query="x", skills_root=skills_root)
