"""Reference resolver for the `brand-voice` skill (1mk.2).

Turns a tenant's pack voice-ref (`brand-voice/<tenant>`) into the brand-voice
context block a writing cell starts from: the artist's brand DNA plus N on-voice
example captions. This is the *contract* the engine wires in when it loads the
skill "on demand" (systemdesign §5.3, schema.VoiceRef). It is intentionally
stdlib-only (tomllib + json) so it can be demonstrated without the engine venv,
an LLM, or network.

It does NOT call a model and does NOT make any decision — it only assembles
grounding context. The validator bank, jury, and confidence/autonomy gate still
run downstream on whatever the cell produces.

Layout it assumes (repo-relative):
    engine/config/packs/<tenant>.toml          # the pack (voice.skill, examples_uri)
    skills/brand-voice/SKILL.md                # the shared skill (this bundle)
    skills/brand-voice/tenants/<tenant>/brand-dna.md
    skills/brand-voice/tenants/<tenant>/examples.jsonl   # seed for examples_uri/KB
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# A tenant id / artist segment is a SINGLE path component. Reject anything that
# could escape the packs/tenants directory (``..``, ``/``, ``\``, absolute paths,
# drive letters, NUL). Allow only the conservative charset real ids use.
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

# repo root = .../skills/brand-voice/verify/resolve_brand_voice.py -> parents[3]
REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_DIR = Path(__file__).resolve().parents[1]  # skills/brand-voice
DEFAULT_PACKS_DIR = REPO_ROOT / "engine" / "config" / "packs"


class BrandVoiceError(Exception):
    """Resolution failed in a way the caller must handle (not silently)."""


def _safe_segment(value: str, kind: str) -> str:
    """Validate a single path segment (tenant id / artist), or raise.

    Hard stop on path-traversal vectors before the value ever touches the
    filesystem — required before multi-tenant, where tenant ids are untrusted.
    """
    if not isinstance(value, str) or not _SAFE_SEGMENT.match(value):
        raise BrandVoiceError(
            f"unsafe {kind} {value!r}: must match {_SAFE_SEGMENT.pattern} "
            "(no path separators, no '..', no absolute paths)"
        )
    return value


def _within(base: Path, child: Path) -> Path:
    """Defense in depth: confirm ``child`` resolves inside ``base``, or raise."""
    base_r = base.resolve()
    child_r = child.resolve()
    if base_r != child_r and base_r not in child_r.parents:
        raise BrandVoiceError(f"path {child_r} escapes {base_r}")
    return child_r


@dataclass
class BrandVoiceContext:
    """The grounding a writing cell starts from for one artist."""

    tenant_id: str
    skill_ref: str
    brand_dna: str                              # the tenant brand-dna.md, verbatim
    examples: list[dict] = field(default_factory=list)  # on-voice grounding few-shots
    degraded: bool = False                      # True => sparse tenant, positioning-only
    notes: list[str] = field(default_factory=list)

    def system_prompt(self, base_instructions: str, n_examples: int = 4) -> str:
        """Assemble the cell's system prompt: brand context FIRST, then the task.

        Putting the DNA before the task instruction is the whole point — the cell
        reads the artist's voice before it is told what to write.
        """
        parts = [
            "# BRAND VOICE — write as this artist, not as generic AI.",
            "Treat the brand DNA below as the source of truth. Only state claims",
            "that appear in its Approved claims list; a needed claim that is missing",
            "is a blocker (escalate), not a creative gap. The Do-not list is absolute.",
            "",
            self.brand_dna.strip(),
        ]
        shots = [e for e in self.examples if e.get("label") == "on_voice"][:n_examples]
        if shots:
            parts += ["", "## On-voice examples (mirror the rhythm; never copy):"]
            parts += [f"- {e['text']}" for e in shots]
        if self.degraded:
            parts += [
                "",
                "## NOTE: sparse tenant — positioning-only.",
                "No on-voice examples available. Write from positioning + pillars +",
                "voice rules only; do not fabricate personas/examples. Lower",
                "confidence so the router queues this for review.",
            ]
        parts += ["", "---", "", "# TASK", base_instructions.strip()]
        return "\n".join(parts)


def _load_pack(tenant_id: str, packs_dir: Path) -> dict:
    _safe_segment(tenant_id, "tenant_id")
    path = _within(packs_dir, packs_dir / f"{tenant_id}.toml")
    if not path.is_file():
        raise BrandVoiceError(f"no pack for tenant {tenant_id!r} (looked for {path})")
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _load_examples(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def resolve(tenant_id: str, *, packs_dir: Path = DEFAULT_PACKS_DIR,
            skill_dir: Path = SKILL_DIR) -> BrandVoiceContext:
    """Resolve `brand-voice/<tenant>` into a BrandVoiceContext.

    Edge cases handled per SKILL.md:
      * sparse tenant (no/empty DNA, no examples) -> graceful degrade.
      * missing pack or missing voice ref -> BrandVoiceError (never silent).
    """
    pack = _load_pack(tenant_id, packs_dir)
    voice = pack.get("voice") or {}
    skill_ref = voice.get("skill")
    if not skill_ref:
        raise BrandVoiceError(f"pack {tenant_id!r} has no [voice].skill ref")
    if not skill_ref.startswith("brand-voice/"):
        raise BrandVoiceError(
            f"voice.skill {skill_ref!r} is not a brand-voice ref for {tenant_id!r}"
        )

    # Multi-artist tenant: the ref names the artist; load exactly that DNA.
    # Sanitize the artist segment before it touches the filesystem.
    artist = _safe_segment(skill_ref.split("/", 1)[1], "skill_ref artist")
    tenants_root = skill_dir / "tenants"
    tdir = _within(tenants_root, tenants_root / artist)
    dna_path = tdir / "brand-dna.md"
    examples = _load_examples(tdir / "examples.jsonl")
    grounding = [e for e in examples if e.get("split") == "grounding"]

    notes: list[str] = []
    if not dna_path.is_file() or not dna_path.read_text(encoding="utf-8").strip():
        # New artist, little past content -> positioning-only graceful degrade.
        notes.append(f"no brand DNA for {artist!r}; degraded to positioning-only")
        return BrandVoiceContext(
            tenant_id=tenant_id, skill_ref=skill_ref,
            brand_dna="(no brand DNA on file — positioning-only)",
            examples=[], degraded=True, notes=notes,
        )

    if not grounding:
        notes.append("no grounding examples; writing from DNA without few-shots")

    return BrandVoiceContext(
        tenant_id=tenant_id, skill_ref=skill_ref,
        brand_dna=dna_path.read_text(encoding="utf-8"),
        examples=grounding, degraded=False, notes=notes,
    )
