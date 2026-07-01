"""customer-psychology skillpack SCAFFOLD — offline tests.

Proves the pack is a thin progressive-disclosure wrapper of the LIVE analyst, that it is
NOT registered (the live path must not depend on it), and that its manifest pin matches
the SKILL.md pin (provenance integrity). It does NOT re-test the analyst itself (that is
covered by the 13 psych_profile tests).
"""

from __future__ import annotations

import re
from pathlib import Path

from studio.skillpacks.customer_psychology import loader

_PACK_DIR = Path(loader.__file__).resolve().parent


def test_loader_returns_the_live_analyst_callable() -> None:
    from studio.psych_profile import analyze_customer

    fn = loader.load()
    # The wrapper adds nothing — it returns the EXISTING vetted analyst callable.
    assert fn is analyze_customer


def test_pack_is_not_registered() -> None:
    # Hard rule: the pack is scaffold-only until sec flips its registry row.
    assert loader.REGISTERED is False
    man = loader.manifest()
    assert man["registered"] is False
    assert man["status"] == "IN-VETTING"


def test_manifest_pin_is_real_40hex_and_matches_skill_md() -> None:
    man = loader.manifest()
    pin = man["pinned"]
    assert re.fullmatch(r"[0-9a-f]{40}", pin), f"pin is not a real 40-hex sha: {pin}"
    skill_md = (_PACK_DIR / "SKILL.md").read_text(encoding="utf-8")
    m = re.search(r"^pinned:\s*([0-9a-f]{40})", skill_md, re.MULTILINE)
    assert m and m.group(1) == pin  # registry/manifest/SKILL.md pin are consistent
