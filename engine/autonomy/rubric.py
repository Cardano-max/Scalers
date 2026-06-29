"""Jury hard-fail catalog + RUBRIC anchor loader (AUTON-01 / 4jx.2).

Loads the committed rubric build-inputs (#80, pmm voice/appr + sec safety) and turns
the prose disqualifiers into a **machine-detectable, closed-set** contract the jury
aggregator enforces:

* ``code_catalog`` — the frozen, append-only set of hard-fail / soft-cap codes
  (``catalog_version``). The jury cell emits codes ONLY from this set; the aggregator
  matches against it as a closed set.
* **FAIL-SAFE (arch ADR #81):** an **unknown/unmatched code** OR a **catalog_version
  mismatch** between the jury cell and the aggregator routes the run to **REVIEW** —
  never a silent pass, never a crash. Catalog drift escalates to a human.
* A hard-fail code on ANY dimension is a **deterministic floor** (non-averageable);
  a soft-cap code caps a dimension's score and warrants review (not a floor).

The RUBRIC anchor corpus (``split=RUBRIC``) is the shared reference the human raters
and jurors score against — it is **excluded from scoring** (anchors are never items
under test), exposed here only for grounding/calibration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# The aggregator's PINNED catalog version. A judge that scored against a different
# version is a drift → fail-safe to review until both sides are re-synced (#81).
EXPECTED_CATALOG_VERSION = 1

# <repo>/evals/gold and <repo>/sec/rubrics — autonomy/rubric.py is engine/autonomy/...
_SRC_ROOT = Path(__file__).resolve().parents[2]
_VOICE_APPR_CATALOG = _SRC_ROOT / "evals" / "gold" / "jury-hard-fails.json"
_SAFETY_CATALOG = _SRC_ROOT / "sec" / "rubrics" / "safety-hard-fails.json"  # sec-owned; optional
_ANCHORS = _SRC_ROOT / "evals" / "gold" / "jury-rubric-anchors.gold.jsonl"

# Registry dimension names -> the engine's JudgeVote dimensions.
_DIM_ALIASES = {"voice": "voice", "appropriateness": "appr", "appr": "appr", "safety": "safety"}


def _normalize_dim(dim: str) -> str:
    return _DIM_ALIASES.get(dim, dim)


@dataclass(frozen=True)
class HardFailCatalog:
    """The closed-set hard-fail / soft-cap contract the aggregator enforces."""

    catalog_version: int
    codes: frozenset[str]                  # the full closed set (hard-fail + soft-cap)
    code_dimension: dict[str, str]         # code -> engine dimension (voice/safety/appr)
    soft_cap_codes: frozenset[str]         # codes that CAP (review), not floor
    soft_cap_max: dict[str, float]         # soft-cap code -> capped [0,1] score

    def is_known(self, code: str) -> bool:
        return code in self.codes

    def dimension_of(self, code: str) -> str | None:
        return self.code_dimension.get(code)


def _anchor_to_unit(anchor_max: int) -> float:
    """A 0-4 rubric anchor cap -> a [0,1] score cap (e.g. appr_max 2 -> 0.5)."""
    return max(0.0, min(1.0, anchor_max / 4.0))


def load_hard_fail_catalog(
    *, voice_appr_path: Path | None = None, safety_path: Path | None = None
) -> HardFailCatalog:
    """Load the union of the pmm voice/appr catalog and (if present) sec's safety
    catalog. The safety file is sec-owned and optional here — its absence degrades
    coverage of the safety codes but never crashes; a safety code the aggregator does
    not know still fails safe via :func:`resolve_codes`."""
    vpath = voice_appr_path or _VOICE_APPR_CATALOG
    raw = json.loads(vpath.read_text(encoding="utf-8"))
    version = int(raw["catalog_version"])

    codes: set[str] = set(raw.get("code_catalog", []))
    code_dim: dict[str, str] = {}
    soft_cap_codes: set[str] = set()
    soft_cap_max: dict[str, float] = {}

    for hf in raw.get("hard_fails", []):
        code_dim[hf["code"]] = _normalize_dim(hf["dimension"])
    for sc in raw.get("soft_caps", []):
        code = sc["code"]
        soft_cap_codes.add(code)
        code_dim[code] = _normalize_dim(sc["dimension"])
        cap = sc.get("cap", {})
        # cap keyed like {"appropriateness_anchor_max": 2}
        for key, val in cap.items():
            if key.endswith("_anchor_max"):
                soft_cap_max[code] = _anchor_to_unit(int(val))

    # Merge sec's safety catalog when vendored (same shape); otherwise skip cleanly.
    spath = safety_path or _SAFETY_CATALOG
    if spath.is_file():
        sraw = json.loads(spath.read_text(encoding="utf-8"))
        codes |= set(sraw.get("code_catalog", []))
        for hf in sraw.get("hard_fails", []):
            code_dim[hf["code"]] = _normalize_dim(hf.get("dimension", "safety"))

    return HardFailCatalog(
        catalog_version=version,
        codes=frozenset(codes),
        code_dimension=code_dim,
        soft_cap_codes=frozenset(soft_cap_codes),
        soft_cap_max=soft_cap_max,
    )


@dataclass(frozen=True)
class HardFailResolution:
    """The aggregator's reading of one judge's emitted codes against the catalog."""

    hard_fail_dims: frozenset[str] = frozenset()   # dimensions with a HARD-FAIL floor
    soft_cap: dict[str, float] = field(default_factory=dict)  # dim -> capped score
    fail_safe: bool = False                        # unknown code / version drift -> REVIEW
    reason: str = ""


def resolve_codes(
    codes: list[str],
    *,
    catalog: HardFailCatalog,
    judge_catalog_version: int,
) -> HardFailResolution:
    """Resolve a judge's emitted hard-fail codes against the closed catalog.

    Fail-safe (→ ``fail_safe=True``, the caller forces REVIEW): the judge scored
    against a different ``catalog_version``, or emitted a code not in the catalog —
    catalog drift must never silently pass. Otherwise maps each code to its dimension:
    hard-fail codes → ``hard_fail_dims`` (the floor), soft-cap codes → ``soft_cap``.
    """
    if judge_catalog_version != catalog.catalog_version:
        return HardFailResolution(
            fail_safe=True,
            reason=f"catalog_version drift (judge v{judge_catalog_version} != aggregator v{catalog.catalog_version})",
        )

    hard_dims: set[str] = set()
    soft_cap: dict[str, float] = {}
    for code in codes:
        if not catalog.is_known(code):
            return HardFailResolution(fail_safe=True, reason=f"unknown hard-fail code {code!r}")
        dim = catalog.dimension_of(code)
        if dim is None:
            return HardFailResolution(fail_safe=True, reason=f"code {code!r} has no dimension")
        if code in catalog.soft_cap_codes:
            cap = catalog.soft_cap_max.get(code, 0.5)
            soft_cap[dim] = min(cap, soft_cap.get(dim, 1.0))
        else:
            hard_dims.add(dim)
    return HardFailResolution(hard_fail_dims=frozenset(hard_dims), soft_cap=soft_cap)


@dataclass(frozen=True)
class RubricAnchor:
    """One RUBRIC anchor (the shared human-rater + juror reference). EXCLUDED from
    scoring — anchors are never items under test (``split=RUBRIC``)."""

    id: str
    tenant_id: str
    dimensions: tuple[str, ...]
    text: str
    expected: dict[str, Any]   # scores/anchors/hard_fail_codes/soft_cap_codes/voice_notes


def load_rubric_anchors(*, path: Path | None = None) -> list[RubricAnchor]:
    """Load the ``split=RUBRIC`` anchor corpus for grounding/calibration. Asserts each
    row is RUBRIC so a mis-split row can never leak into a scored set."""
    apath = path or _ANCHORS
    if not apath.is_file():
        return []
    out: list[RubricAnchor] = []
    for line in apath.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("split") != "RUBRIC":
            raise ValueError(f"anchor {row.get('id')!r} is split={row.get('split')!r}, expected RUBRIC")
        out.append(
            RubricAnchor(
                id=row["id"],
                tenant_id=row["tenant_id"],
                dimensions=tuple(row.get("rubric_dimensions", [])),
                text=row["input"]["text"],
                expected=row.get("expected", {}),
            )
        )
    return out
