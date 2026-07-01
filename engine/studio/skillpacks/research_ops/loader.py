"""DORMANT, prompt-only loader for the research-ops skillpack.

Prompt-only pack: the value is the vetted methodology text in ``SKILL.md``. Every bundled
upstream script was STRIPPED (never vendored, never run) per the supply-chain gate, so there
is no third-party callable to expose. This loader returns metadata only and is not on any live
execution path.

HARD RULE (supply-chain gate): usability is governed solely by the pack's row in
``docs/skills/registry.md``. ``REGISTERED = False`` is a belt-and-suspenders second layer. Do
not add network/file/exec here; re-introduce any numeric tooling only via our OWN vetted
adapter (see ``docs/skills/vetting-protocol.md``).
"""

from __future__ import annotations

import json
import os
from typing import Any

_PACK_DIR = os.path.dirname(__file__)

REGISTERED = False


def manifest() -> dict[str, Any]:
    """The pack manifest (provenance + pinned commit + stripped list), from ``manifest.json``."""
    with open(os.path.join(_PACK_DIR, "manifest.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def load() -> dict[str, Any]:
    """Progressive disclosure: return pack metadata only. No executable capability — the pack
    is prompt-only (the methodology lives in ``SKILL.md``)."""
    return {"prompt_only": True, "skill_md": os.path.join(_PACK_DIR, "SKILL.md"), "manifest": manifest()}
