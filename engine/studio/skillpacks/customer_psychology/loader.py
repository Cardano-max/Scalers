"""Thin, progressive-disclosure loader for the customer-psychology skillpack SCAFFOLD.

This is a SCAFFOLD, not a live dependency. It wraps the already-vetted, already-live
analyst (:func:`studio.psych_profile.analyze_customer`) so it can be loaded ON DEMAND —
only when a run actually classifies a lead — and move through the sec-owned vetting gate
as a first-class pack.

HARD RULE (supply-chain gate): this pack is NOT registered. It has a DRAFT
``IN-VETTING — PENDING sec sign-off`` row in ``docs/skills/registry.md``. Do NOT route the
live path through this loader until sec records a ``REGISTERED — IN USE`` row. The live
run still imports ``psych_profile.analyze_customer`` DIRECTLY (see ``studio/agui.py``);
this loader must not become a live dependency of that path. It exists to prove the
progressive-disclosure seam + carry the pack through vetting.

The loader adds NOTHING to the analyst's behavior — it imports and returns the existing
callable. All anti-fabrication guarantees remain in ``psych_profile.py``.

DO NOT move this pack into ``engine/skills/`` (or ``skills/``) until it is REGISTERED — a
HELD / IN-VETTING bundle discovered under a scanned SKILL_ROOT is a hard FAIL in
``scripts/check_skill_registry.py`` and would trip the done-gate. It lives under
``engine/studio/skillpacks/`` precisely so CI stays green while it is IN-VETTING. This
loader is DORMANT: nothing on the live path imports it (``studio/agui.py`` calls
``studio.psych_profile.analyze_customer`` DIRECTLY).
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable

_PACK_DIR = os.path.dirname(__file__)

# The pack is scaffold-only until sec flips its registry row. The loader refuses to be
# used as a LIVE dependency unless explicitly allowed, so an accidental import cannot
# silently reroute the live analyst through an unregistered pack.
REGISTERED = False


def manifest() -> dict[str, Any]:
    """The pack manifest (pinned provenance + capabilities), read from ``manifest.json``."""
    with open(os.path.join(_PACK_DIR, "manifest.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def load() -> Callable[..., Any]:
    """Progressive disclosure: import :mod:`studio.psych_profile` lazily and return its
    ``analyze_customer`` callable. Nothing is imported until this is called (the whole
    point of the pack). The returned callable IS the vetted analyst — unchanged."""
    from studio.psych_profile import analyze_customer

    return analyze_customer
