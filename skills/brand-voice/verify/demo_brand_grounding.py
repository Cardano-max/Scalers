"""Demonstration: a draft cell starts from REAL brand context (1mk.2 AC).

Shows the before/after for the existing `content_brief` cell:

  BASELINE  the cell's shipped instruction mentions "brand voice" but carries NO
            actual brand data -> generic output (the emilyxhug "generic SaaS
            voice" failure this skill exists to fix).
  GROUNDED  the same task, prefixed with the brand-voice context resolved from the
            ink-studio pack + DNA + on-voice examples -> the cell now starts from
            Mara Vance's actual positioning, approved claims, bans, and examples.

Also exercises the graceful-degrade edge case (new artist, no DNA).

stdlib-only; no engine venv, no LLM, no network. Run:
    python skills/brand-voice/verify/demo_brand_grounding.py
Exits non-zero if any grounding assertion fails.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

try:  # the DNA/examples contain emoji; force UTF-8 so Windows consoles don't choke.
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from resolve_brand_voice import (  # noqa: E402
    REPO_ROOT, SKILL_DIR, resolve, BrandVoiceContext, BrandVoiceError,
)

CELL = REPO_ROOT / "engine" / "cells" / "content_brief.py"


def baseline_instructions() -> str:
    """Extract the shipped content_brief instruction string from source."""
    src = CELL.read_text(encoding="utf-8")
    m = re.search(r"_INSTRUCTIONS\s*=\s*\((.*?)\)", src, re.DOTALL)
    if not m:
        raise SystemExit("could not find _INSTRUCTIONS in content_brief.py")
    # Join the adjacent string literals into one line.
    return "".join(re.findall(r'"([^"]*)"', m.group(1)))


def banner(title: str) -> None:
    print("\n" + "=" * 72 + f"\n{title}\n" + "=" * 72)


def main() -> int:
    base = baseline_instructions()

    banner("BASELINE — shipped content_brief instruction (no brand data)")
    print(base)

    # The baseline must NOT contain any real brand specifics.
    brand_markers = ["Mara Vance", "fine-line", "Free 20-minute consult", "unleash"]
    leaked = [m for m in brand_markers if m.lower() in base.lower()]
    assert not leaked, f"baseline unexpectedly already contains brand data: {leaked}"
    print("\n[check] baseline carries NO artist-specific brand context  -> generic. OK")

    banner("GROUNDED — same task, prefixed with resolved brand-voice context")
    ctx = resolve("ink-studio")
    prompt = ctx.system_prompt(base, n_examples=4)
    print(prompt)

    # The grounded prompt MUST start from real brand context.
    required = {
        "positioning promise": "quiet personal story",
        "an approved claim": "Free 20-minute consultation before every booking",
        "a do-not ban": "unleash",
        "an on-voice example": "grandmother's handwriting",
        "the artist identity": "Mara Vance",
    }
    print("\n[grounding checks]")
    ok = True
    for label, needle in required.items():
        present = needle.lower() in prompt.lower()
        ok = ok and present
        print(f"  {'PASS' if present else 'FAIL'} — {label}: {needle!r}")
    assert ok, "grounded prompt is missing required brand context"

    # And the task itself must still be present (we ground, we don't replace).
    assert base.split('.')[0].lower() in prompt.lower(), "task instruction was dropped"
    print("  PASS — original task instruction preserved")

    n_shots = len([e for e in ctx.examples if e.get("label") == "on_voice"])
    print(f"\n[loaded] {n_shots} on-voice example(s) from the pack's examples set; "
          f"skill_ref={ctx.skill_ref!r}; degraded={ctx.degraded}")

    banner("EDGE CASE — new artist, no DNA -> graceful degrade to positioning-only")
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        (tdp / "packs").mkdir()
        (tdp / "packs" / "newbie.toml").write_text(
            'tenant_id = "newbie"\ndisplay_name = "New Artist"\n'
            '[voice]\nskill = "brand-voice/newbie"\n', encoding="utf-8")
        # skill_dir with no tenants/newbie -> degrade
        (tdp / "skills").mkdir()
        d = resolve("newbie", packs_dir=tdp / "packs", skill_dir=tdp / "skills")
        assert d.degraded and not d.examples, "expected graceful degrade for sparse tenant"
        sp = d.system_prompt(base)
        assert "positioning-only" in sp and "Lower" in sp, "degrade note missing"
        print(f"  PASS — degraded={d.degraded}; notes={d.notes}")
        print("  PASS — degrade note instructs lower confidence -> review")

    banner("SECURITY — path-traversal in tenant_id / skill_ref is rejected")
    # Via tenant_id (before any filesystem access).
    for bad in ["../../etc/passwd", "..", "a/b", "x.toml", "/abs", "C:\\win"]:
        try:
            resolve(bad)
        except BrandVoiceError:
            print(f"  PASS — tenant_id {bad!r} rejected")
        else:
            raise AssertionError(f"tenant_id {bad!r} was NOT rejected")
    # Via skill_ref artist segment (malicious pack value).
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        (tdp / "packs").mkdir(); (tdp / "skills").mkdir()
        (tdp / "packs" / "evil.toml").write_text(
            'tenant_id = "evil"\ndisplay_name = "Evil"\n'
            '[voice]\nskill = "brand-voice/../../../../secrets"\n', encoding="utf-8")
        try:
            resolve("evil", packs_dir=tdp / "packs", skill_dir=tdp / "skills")
        except BrandVoiceError:
            print("  PASS — skill_ref artist '../../../../secrets' rejected")
        else:
            raise AssertionError("malicious skill_ref artist was NOT rejected")

    banner("VOICE DIMENSIONS — skill emits the a9m.3 VoiceDimensions from the DNA")
    dims = ctx.dimensions
    assert dims is not None, "skill emitted no VoiceDimensions"
    assert set(dims) >= {"tone", "vocabulary", "structure"}, f"missing dims: {set(dims)}"
    assert {"prefer", "ban", "approved_claims", "emoji_policy", "hashtag_policy"} <= set(dims["vocabulary"])
    ref = json.loads((SKILL_DIR / "tenants" / "ink-studio" / "voice-dimensions.json")
                     .read_text(encoding="utf-8"))["dimensions"]
    assert dims == ref, "emitted dimensions != reference fill"
    print(f"  PASS — dimensions emitted (tone[{len(dims['tone'])}], structure[{len(dims['structure'])}], "
          f"ban[{len(dims['vocabulary']['ban'])}], approved_claims[{len(dims['vocabulary']['approved_claims'])}])")
    print("  PASS — emission matches the voice-dimensions.json reference fill (contract §1/§2)")

    banner("RESULT")
    print("All grounding + edge-case assertions passed.")
    print("A draft cell now demonstrably STARTS FROM real brand context.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
