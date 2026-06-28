"""Over-personalization guard (bead 1mk.7) — "personalization that reads creepy is a fail".

Cold outreach should feel relevant, not surveilled. This deterministic guard
inspects the personalization signals a sequence wants to reference and:
- **blocks** references to private/identifying categories (home address, family,
  health, religion, finances, employer-internal, precise real-time location) —
  these are stripped from the brief and flagged,
- **caps** the number of personal references per touch (too many distinct
  personal details in one email reads as creepy even if each is "public"),
- prefers public, professional, opt-in-derived signals (studio style, a past
  inquiry topic, an event the prospect attended).

Pure code; no model. The writer's copy still passes the S3 AI-flagger validator +
jury — this guard governs the *inputs* to personalization, upstream of the copy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Categories that make outreach read as creepy/surveilling — references to these
# are stripped + flagged, never used in copy.
_CREEPY_PATTERNS: dict[str, re.Pattern] = {
    "home_address": re.compile(r"\b(home address|lives at|street|apt\.?|zip ?code)\b", re.I),
    "family": re.compile(r"\b(spouse|wife|husband|kids?|children|daughter|son|divorce)\b", re.I),
    "health": re.compile(r"\b(diagnos|illness|disease|pregnan|medical|therapy|disab)\b", re.I),
    "religion": re.compile(r"\b(church|mosque|synagogue|religio|baptiz)\b", re.I),
    "finances": re.compile(r"\b(salary|income|debt|net worth|mortgage|credit score)\b", re.I),
    "employer_internal": re.compile(r"\b(your boss|internal memo|laid off|fired|resign)\b", re.I),
    "realtime_location": re.compile(r"\b(right now at|currently at|saw you at .* (today|tonight))\b", re.I),
}

# Max distinct personal references to weave into a single touch before it reads
# as over-personalized (even with allowed signals).
_MAX_REFS_PER_TOUCH = 2


@dataclass(frozen=True)
class GuardResult:
    allowed: tuple[str, ...]      # signals safe to reference
    blocked: tuple[str, ...]      # creepy signals stripped
    warnings: tuple[str, ...]     # human-readable flags


def screen_signals(signals: tuple[str, ...]) -> GuardResult:
    """Split signals into allowed vs blocked (creepy) with warnings."""
    allowed: list[str] = []
    blocked: list[str] = []
    warnings: list[str] = []
    for sig in signals:
        hit = next((cat for cat, pat in _CREEPY_PATTERNS.items() if pat.search(sig)), None)
        if hit:
            blocked.append(sig)
            warnings.append(f"creepy personalization blocked ({hit}): stripped from brief")
        else:
            allowed.append(sig)
    return GuardResult(tuple(allowed), tuple(blocked), tuple(warnings))


def brief_for_touch(allowed: tuple[str, ...], touch_index: int) -> tuple[str, ...]:
    """Choose up to _MAX_REFS_PER_TOUCH allowed signals for a touch (rotates by
    index so touches don't all reuse the same one)."""
    if not allowed:
        return ()
    start = (touch_index - 1) % len(allowed)
    rotated = allowed[start:] + allowed[:start]
    return tuple(rotated[:_MAX_REFS_PER_TOUCH])
