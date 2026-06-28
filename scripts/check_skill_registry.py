#!/usr/bin/env python3
"""Skill-registry consistency check (CustomerAcq-1mk.10).

Enforces the 1mk.1 supply-chain HARD RULE in CI: **no registry row -> no skill
use**, plus provenance (a vendored skill's declared pin must match the registry
pin). Offline + deterministic (stdlib only); runs identically on a Windows dev box
and on Linux CI. Exits non-zero on any violation so it gates the build (wired into
``scripts/done_gate.py`` and the CI done-gate job).

A "skill bundle" is any directory containing a ``SKILL.md`` under ``skills/`` or
``engine/skills/``. The registry is ``docs/skills/registry.md`` (sec-owned).

HARD failures (fail the build):
  1. A skill bundle present in the repo has **no** registry row.        [no row -> no use]
  2. The matching row is **REJECTED** or **HELD** — a rejected/held skill must not
     be vendored as a loadable bundle.
  3. The matching row has **no real 40-hex upstream pin** AND is not marked
     ``ORIGINAL`` (e.g. an unfilled ``<PIN-AT-ADOPTION>`` placeholder) — provenance
     must be auditable. A genuinely original / pattern-only skill (no upstream code
     vendored, no repo to pin) records a backtick-quoted ``ORIGINAL`` as its pin in
     both the registry row and the ``SKILL.md`` ``pinned:`` field.
  4. The bundle's declared pin (``SKILL.md`` ``pinned:``) does **not** match the
     registry pin — provenance DRIFT. This includes ORIGINAL-vs-SHA mismatches in
     either direction (registry says ORIGINAL but the bundle pins a SHA, or vice
     versa).
  5. A row marked **REGISTERED -- IN USE** (a) points at a missing on-disk bundle,
     or (b) does not record operator adoption.

Advisory (WARN, never fails) unless ``--strict-pins``:
  - A present bundle whose ``SKILL.md`` declares no ``pinned:`` field. The registry
    remains the source of truth for the pin; this is an authoring-hygiene nudge.
    Once every bundle carries a ``pinned:`` field, run with ``--strict-pins`` (or
    flip the default) to make this a hard failure.

Usage:
  python scripts/check_skill_registry.py [--strict-pins]
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY = REPO_ROOT / "docs" / "skills" / "registry.md"
# Where vendored skill bundles live (a bundle = a dir containing SKILL.md).
SKILL_ROOTS = ("skills", "engine/skills")

_SHA_FULL = re.compile(r"\b[0-9a-f]{40}\b")
_SHA_ANY = re.compile(r"\b[0-9a-f]{7,40}\b")
# Angle-bracket placeholder, e.g. <PIN-AT-ADOPTION> / <org>/<commit ...>.
_PLACEHOLDER = re.compile(r"<[^>]*>")
# Explicit "no upstream code vendored" provenance for original / pattern-only
# skills (a cell + prompt re-authored from practitioner patterns, with no repo to
# pin). Written as a backtick-quoted `ORIGINAL` in the registry pin column so it is
# distinct from a real SHA and from an unfilled <PLACEHOLDER>.
_ORIGINAL = re.compile(r"`\s*ORIGINAL\b", re.IGNORECASE)

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"


class Finding:
    def __init__(self, status: str, msg: str) -> None:
        self.status = status
        self.msg = msg


class Row:
    """One parsed registry-table row."""

    def __init__(self, cells: list[str]) -> None:
        self.name = _strip_md(cells[0])
        self.source_pin = cells[1]
        self.our_path = cells[4]
        self.eval_gate = cells[5]
        self.status = cells[7]
        self.full = " | ".join(cells)
        m = _SHA_FULL.search(cells[1])
        self.pin = m.group(0) if m else None
        self.has_placeholder_pin = self.pin is None and bool(_PLACEHOLDER.search(cells[1]))
        # ORIGINAL provenance: no upstream code vendored (pattern-only). Only when
        # there is no real SHA — a real-SHA row that merely says "ORIGINAL" in prose
        # (e.g. the research family-ref rows) is pinned, not ORIGINAL.
        self.is_original = self.pin is None and bool(_ORIGINAL.search(cells[1]))

    @property
    def status_norm(self) -> str:
        return self.status.upper()

    @property
    def is_in_use(self) -> bool:
        return "REGISTERED" in self.status_norm and "IN USE" in self.status_norm

    @property
    def is_rejected(self) -> bool:
        return "REJECTED" in self.status_norm

    @property
    def is_held(self) -> bool:
        return "HELD" in self.status_norm

    @property
    def records_operator_adoption(self) -> bool:
        text = self.full.upper()
        return "OPERATOR-APPROVED" in text or "ADOPTED" in text


def _strip_md(cell: str) -> str:
    """Drop markdown emphasis + a trailing italic parenthetical for the name."""
    cell = cell.replace("**", "").strip()
    cell = re.sub(r"\*\(.*?\)\*", "", cell)  # drop "*(1mk.3, ...)*"
    return cell.replace("*", "").strip()


def _parse_registry(text: str) -> list[Row]:
    """Parse the rows under the '## Registry table' heading into Row objects."""
    rows: list[Row] = []
    in_table = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_table = "registry table" in stripped.lower()
            continue
        if not in_table or not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        # Skip header + separator rows.
        if not cells or cells[0].lower().startswith("skill (our name)"):
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        if len(cells) >= 8:
            rows.append(Row(cells))
    return rows


def _discover_bundles() -> list[str]:
    """Return repo-relative posix paths of every dir containing a SKILL.md."""
    found: list[str] = []
    for root in SKILL_ROOTS:
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        for skill_md in base.glob("*/SKILL.md"):
            found.append(skill_md.parent.relative_to(REPO_ROOT).as_posix())
    return sorted(found)


def _bundle_declared_pin(bundle_path: str) -> str | None:
    """Extract the `pinned:` SHA from a bundle's SKILL.md frontmatter, if any."""
    skill_md = REPO_ROOT / bundle_path / "SKILL.md"
    try:
        head = skill_md.read_text(encoding="utf-8").splitlines()[:15]
    except OSError:
        return None
    for line in head:
        if line.lower().startswith("pinned:"):
            # Prefer a real SHA even if the comment mentions "original" (e.g. a
            # family-ref pin annotated "ORIGINAL/pattern-only"); only treat the
            # declaration as ORIGINAL when there is no hex pin at all.
            m = _SHA_ANY.search(line)
            if m:
                return m.group(0)
            if re.search(r"\boriginal\b", line, re.IGNORECASE):
                return "ORIGINAL"
            return None
    return None


def _match_row(bundle_path: str, rows: list[Row]) -> Row | None:
    """Find the registry row whose our-format path references this bundle."""
    for row in rows:
        if bundle_path in row.our_path:
            return row
    # Fallback: match anywhere in the row text (path may live outside col 5).
    for row in rows:
        if bundle_path in row.full:
            return row
    return None


def check(strict_pins: bool = False) -> list[Finding]:
    findings: list[Finding] = []

    if not REGISTRY.is_file():
        return [Finding(FAIL, f"registry not found: {REGISTRY.relative_to(REPO_ROOT)}")]

    rows = _parse_registry(REGISTRY.read_text(encoding="utf-8"))
    if not rows:
        return [Finding(FAIL, "no rows parsed under '## Registry table' in registry.md")]

    bundles = _discover_bundles()

    # --- Per-bundle checks (the "no row -> no use" enforcement) ----------------
    for bundle in bundles:
        row = _match_row(bundle, rows)
        if row is None:
            findings.append(
                Finding(FAIL, f"{bundle}: skill bundle present but has NO registry row "
                              f"(no row -> no use). Add a vetted row to docs/skills/registry.md.")
            )
            continue
        if row.is_rejected or row.is_held:
            findings.append(
                Finding(FAIL, f"{bundle}: matched row '{row.name}' is "
                              f"{'REJECTED' if row.is_rejected else 'HELD'} — a "
                              f"non-eligible skill must not be vendored as a loadable bundle.")
            )
        if row.pin is None and not row.is_original:
            findings.append(
                Finding(FAIL, f"{bundle}: registry row '{row.name}' has no real 40-hex "
                              f"upstream pin{' (placeholder)' if row.has_placeholder_pin else ''} "
                              f"and is not marked `ORIGINAL` — provenance must be auditable before use.")
            )
        elif row.is_original:
            # No upstream code vendored. The bundle should declare `pinned: ORIGINAL`
            # (or nothing); a real SHA in the bundle would contradict the registry.
            declared = _bundle_declared_pin(bundle)
            if declared is None:
                findings.append(Finding(
                    FAIL if strict_pins else WARN,
                    f"{bundle}: registry row is ORIGINAL (no upstream code) but SKILL.md "
                    f"declares no `pinned:` field. Add `pinned: ORIGINAL` to frontmatter."))
            elif declared.upper() != "ORIGINAL":
                findings.append(Finding(
                    FAIL,
                    f"{bundle}: PROVENANCE DRIFT — registry marks this ORIGINAL (no upstream "
                    f"code) but SKILL.md pins `{declared}`. Reconcile."))
        else:
            declared = _bundle_declared_pin(bundle)
            if declared is None:
                msg = (f"{bundle}: SKILL.md declares no `pinned:` field "
                       f"(registry pin = {row.pin[:12]}…). Add `pinned: {row.pin}` to "
                       f"frontmatter so the loaded artifact carries its provenance.")
                findings.append(Finding(FAIL if strict_pins else WARN, msg))
            elif declared.upper() == "ORIGINAL":
                findings.append(Finding(
                    FAIL, f"{bundle}: PROVENANCE DRIFT — SKILL.md declares `ORIGINAL` but the "
                          f"registry pins {row.pin[:12]}…. Reconcile."))
            elif not (row.pin == declared or row.pin.startswith(declared)):
                findings.append(
                    Finding(FAIL, f"{bundle}: PROVENANCE DRIFT — SKILL.md pinned:{declared} "
                                  f"!= registry pin {row.pin}. Re-vet or correct the pin.")
                )

    # --- Per-row checks for anything marked IN USE -----------------------------
    bundle_set = set(bundles)
    for row in rows:
        if not row.is_in_use:
            continue
        # The our-path must reference a bundle that actually exists on disk.
        if not any(b in row.our_path or b in row.full for b in bundle_set):
            findings.append(
                Finding(FAIL, f"row '{row.name}' is REGISTERED — IN USE but no matching "
                              f"on-disk skill bundle was found (path: {row.our_path[:60]}…).")
            )
        if not row.records_operator_adoption:
            findings.append(
                Finding(FAIL, f"row '{row.name}' is IN USE but records no operator adoption "
                              f"(IN-USE requires operator sign-off per the 1mk.1 gate).")
            )

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Scalers skill-registry consistency check")
    parser.add_argument("--strict-pins", action="store_true",
                        help="treat a bundle missing a `pinned:` frontmatter field as a failure")
    args = parser.parse_args()

    findings = check(strict_pins=args.strict_pins)

    print("=" * 64)
    print("skill-registry consistency check (1mk.10)")
    print("=" * 64)
    bundles = _discover_bundles()
    print(f"  bundles discovered: {', '.join(bundles) or '(none)'}")
    fails = [f for f in findings if f.status == FAIL]
    warns = [f for f in findings if f.status == WARN]
    for f in findings:
        print(f"  [{f.status}] {f.msg}")
    if not findings:
        print("  [PASS] all skill bundles registered, pinned, and consistent.")
    print("=" * 64)
    if fails:
        print(f"SKILL-REGISTRY: FAIL ({len(fails)} violation(s), {len(warns)} warning(s))")
        return 1
    print(f"SKILL-REGISTRY: PASS ({len(warns)} warning(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
