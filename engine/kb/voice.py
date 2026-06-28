"""Brand-voice grounding (KNOW-02 / a9m.3, ADR phase-3 Decision 3).

The typed ``VoiceGrounding`` payload the Copywriter / Draft cell (a9m.5) consumes —
NEVER raw exemplars. Grounding has two sources:

* **dimensions** — the tenant's brand-voice rubric (tone / structure / vocabulary),
  EMITTED by the writer-owned brand-voice skill from the per-tenant ``brand-dna.md``
  and shipped as the bundled ``voice-dimensions.json`` (one per tenant, sibling to
  brand-dna.md). The engine *loads* that emission; it never re-derives voice content.
  (Consume, don't rebuild — writer owns the dimension content.)
* **exemplars** — top-k of the tenant's own past content, retrieved by pgvector
  similarity over ``kb_chunks`` (:meth:`kb.store.KbStore.voice_exemplars`).

The payload also carries a **coverage** flag so a thin / new-tenant KB degrades
safely (dimensions-only + ``low_grounding``) instead of fabricating a voice it has
no evidence for. dimensions are ALWAYS present (the pack voice ref always resolves);
exemplars may be empty. If even the skill fails to resolve that is a *misconfig* the
loader surfaces by raising — never a silent generic draft.

Shapes are the arch-locked envelope (a9m.1 ADR Decision 3, PR #38); the dimension
*content* is pmm/writer-owned (voice-grounding-contract §2).
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from config.schema import TenantPack

# Default location of the brand-voice skill bundle: <repo>/skills. kb/voice.py is
# engine/kb/voice.py, so parents[2] == the Scalers src root (sibling of `engine`).
_SKILLS_ROOT = Path(__file__).resolve().parents[2] / "skills"


# ── Typed grounding payload (ADR Decision 3, arch-locked envelope) ────────────


class Exemplar(BaseModel):
    """One retrieved past-content chunk handed to the cell as voice grounding."""

    model_config = ConfigDict(extra="forbid")

    content: str
    metrics: dict = Field(default_factory=dict)  # e.g. {"on_voice": True, "engagement": ...}
    similarity: float  # 1 - cosine distance; higher = closer


class Vocabulary(BaseModel):
    """The lexical half of the rubric — guidance AND the deterministic gate read it.

    ``ban`` + ``approved_claims`` are the canonical per-tenant lists feeding BOTH the
    Copywriter (guidance) AND the validator bank (enforcement) — one definition, two
    consumers (ADR Decision 3 / 4). ``sensitive_ban`` is the escalate-on-fail set
    (Decision 4); empty by default and filled from the global pattern file by the
    separate gate-disposition bead, harmless until that gate reads it.
    """

    model_config = ConfigDict(extra="forbid")

    prefer: list[str] = Field(default_factory=list)
    ban: list[str] = Field(default_factory=list)
    approved_claims: list[str] = Field(default_factory=list)
    sensitive_ban: list[str] = Field(default_factory=list)
    emoji_policy: str = ""
    hashtag_policy: str = ""


class VoiceDimensions(BaseModel):
    """The tenant's brand-voice rubric. SHAPE arch-owned; CONTENT pmm/writer-owned,
    emitted by the brand-voice skill from ``brand-dna.md`` (loaded, not re-derived)."""

    model_config = ConfigDict(extra="forbid")

    tone: list[str] = Field(default_factory=list)
    structure: list[str] = Field(default_factory=list)
    vocabulary: Vocabulary = Field(default_factory=Vocabulary)


class GroundingCoverage(str, Enum):
    """How much real past content backs the voice (drives the degrade ladder)."""

    FULL = "full"        # dimensions present AND >= k exemplars
    PARTIAL = "partial"  # dimensions present but thin / below-k exemplars
    SPARSE = "sparse"    # new tenant / too few exemplars -> low_grounding


class VoiceGrounding(BaseModel):
    """The single typed contract the Copywriter consumes / the eval scores / the
    console can show (ADR Decision 3). Tenant-scoped; one tenant's voice never mixes
    into another's."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    dimensions: VoiceDimensions      # ALWAYS present (from pack.voice.skill)
    exemplars: list[Exemplar]        # top-k from kb_chunks; [] if none / unreachable
    coverage: GroundingCoverage
    low_grounding: bool              # True iff coverage == SPARSE
    exemplar_count: int


@runtime_checkable
class VoiceExemplarSource(Protocol):
    """The retrieval seam :func:`build_voice_grounding` reads — :class:`KbStore`
    satisfies it. A Protocol (not the concrete store) keeps the assembly DB-free to
    unit-test and lets the degrade ladder be exercised with a fake source."""

    def voice_exemplars(self, *, tenant_id: str, query: str, k: int = 5) -> list[Exemplar]:
        ...


# ── Dimensions loader — CONSUME the writer-emitted bundle ─────────────────────


class VoiceDimensionsError(RuntimeError):
    """The brand-voice skill did not emit resolvable dimensions for the tenant.

    Raised (never swallowed) so a voice misconfig surfaces loudly instead of the
    engine silently producing un-grounded generic copy (the KNOW-02 failure mode)."""


def _dimensions_path(skill_ref: str, skills_root: Path) -> Path:
    """Map a pack voice skill ref to its bundled ``voice-dimensions.json``.

    ``brand-voice/ladies8391`` -> ``<skills_root>/brand-voice/tenants/ladies8391/
    voice-dimensions.json`` (the layout writer ships: per-tenant, sibling to
    brand-dna.md + examples.jsonl)."""
    family, _, tenant = skill_ref.partition("/")
    if not family or not tenant:
        raise VoiceDimensionsError(
            f"voice skill ref {skill_ref!r} is not 'family/tenant' shaped"
        )
    return skills_root / family / "tenants" / tenant / "voice-dimensions.json"


def load_voice_dimensions(
    pack: TenantPack, *, skills_root: Path | None = None
) -> VoiceDimensions:
    """Load the tenant's typed ``VoiceDimensions`` from the brand-voice skill bundle.

    Consumes writer's emitted ``voice-dimensions.json`` (the machine-readable emission
    of ``brand-dna.md``) — the engine loads the *skill emission*, not the DNA. A
    missing / malformed emission raises :class:`VoiceDimensionsError` (a voice
    misconfig is surfaced, never a silent generic draft — contract §3)."""
    root = skills_root or _SKILLS_ROOT
    path = _dimensions_path(pack.voice.skill, root)
    if not path.is_file():
        raise VoiceDimensionsError(
            f"brand-voice skill {pack.voice.skill!r} emitted no voice-dimensions.json "
            f"(looked at {path}); cannot ground tenant {pack.tenant_id!r}"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VoiceDimensionsError(
            f"voice-dimensions.json for {pack.tenant_id!r} is unreadable: {exc}"
        ) from exc
    # The bundle wraps the rubric under "dimensions" (sibling: tenant_id, source...).
    payload = raw.get("dimensions", raw) if isinstance(raw, dict) else raw
    try:
        return VoiceDimensions.model_validate(payload)
    except Exception as exc:  # pydantic ValidationError -> typed misconfig
        raise VoiceDimensionsError(
            f"voice-dimensions.json for {pack.tenant_id!r} does not match the "
            f"VoiceDimensions contract: {exc}"
        ) from exc


# ── Assembly — the knowledge-layer read the Copywriter consumes ───────────────


def build_voice_grounding(
    pack: TenantPack,
    kb: VoiceExemplarSource,
    *,
    query: str,
    k: int = 5,
    skills_root: Path | None = None,
    sparse_floor: int = 1,
) -> VoiceGrounding:
    """Assemble the typed ``VoiceGrounding`` (ADR Decision 3).

    dimensions <- the brand-voice skill (always present, or raise on misconfig);
    exemplars <- ``kb.voice_exemplars`` (tenant-scoped, holdout-filtered). A KB that
    is empty OR unreachable degrades to dimensions-only rather than failing — the
    pack voice ref is always enough to ground on.

    Degrade ladder (contract §3): ``SPARSE`` when fewer than ``sparse_floor``
    exemplars (new tenant / unreachable KB) -> ``low_grounding``; ``FULL`` at >= ``k``;
    ``PARTIAL`` in between. ``low_grounding`` is a signal, not a failure — the draft
    still runs on dimensions; Check&Score lowers confidence so 439-held routing sends
    it to review rather than auto."""
    dimensions = load_voice_dimensions(pack, skills_root=skills_root)

    try:
        exemplars = list(kb.voice_exemplars(tenant_id=pack.tenant_id, query=query, k=k))
    except Exception:
        # Unreachable / failing KB degrades to the pack ref (dimensions-only) — the
        # voice is never fabricated, and a KB outage never crashes the draft.
        exemplars = []

    count = len(exemplars)
    if count < sparse_floor:
        coverage = GroundingCoverage.SPARSE
    elif count >= k:
        coverage = GroundingCoverage.FULL
    else:
        coverage = GroundingCoverage.PARTIAL

    return VoiceGrounding(
        tenant_id=pack.tenant_id,
        dimensions=dimensions,
        exemplars=exemplars,
        coverage=coverage,
        low_grounding=coverage is GroundingCoverage.SPARSE,
        exemplar_count=count,
    )
