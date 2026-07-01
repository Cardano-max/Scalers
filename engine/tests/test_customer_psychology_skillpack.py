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


def test_pin_is_original_first_party_not_a_fabricated_sha() -> None:
    # First-party pack: no upstream repo exists, so the pin is ORIGINAL (fabricating a
    # 40-hex SHA would itself violate the no-fabrication gate). manifest + SKILL.md agree.
    man = loader.manifest()
    assert man["pinned"] == "ORIGINAL"
    skill_md = (_PACK_DIR / "SKILL.md").read_text(encoding="utf-8")
    m = re.search(r"^pinned:\s*(\S+)", skill_md, re.MULTILINE)
    assert m and m.group(1) == "ORIGINAL"
    # No fabricated 40-hex sha anywhere in the frontmatter.
    assert not re.search(r"\b[0-9a-f]{40}\b", skill_md.split("---", 2)[1])
