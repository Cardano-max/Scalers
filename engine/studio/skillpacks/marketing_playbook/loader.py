"""DORMANT, prompt-only loader for the marketing-playbook skillpack.

This pack is PROMPT-ONLY: its value is the vetted methodology text in ``SKILL.md``. Unlike a
code-wrapping pack (cf. ``customer_psychology``), there is no third-party callable to expose —
every bundled upstream script was STRIPPED (never vendored, never run) per the supply-chain
gate. This loader therefore returns metadata only and is not on any live execution path.

HARD RULE (supply-chain gate): usability is governed solely by the pack's row in
``docs/skills/registry.md``. ``REGISTERED = False`` here is a belt-and-suspenders second layer
so an accidental import cannot route a live path through an unregistered/unauthorized pack. Do
not add network/file/exec here — the pack must stay prompt-only unless a capability is
re-introduced via our OWN vetted adapter (see ``docs/skills/vetting-protocol.md``).
"""

from __future__ import annotations

import json
import os
from typing import Any

_PACK_DIR = os.path.dirname(__file__)

# Prompt-only pack. Stays dormant regardless of registry status.
REGISTERED = False


def manifest() -> dict[str, Any]:
    """The pack manifest (provenance + pinned commit + stripped list), from ``manifest.json``."""
    with open(os.path.join(_PACK_DIR, "manifest.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def load() -> dict[str, Any]:
    """Progressive disclosure: return pack metadata only. There is NO executable capability —
    the pack is prompt-only (the methodology lives in ``SKILL.md``). Returns the manifest so a
    caller can locate the prompt content without importing anything third-party."""
    return {"prompt_only": True, "skill_md": os.path.join(_PACK_DIR, "SKILL.md"), "manifest": manifest()}
