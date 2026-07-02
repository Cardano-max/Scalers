"""Anti-fake-personalization guard (CustomerAcq-ju1.3, anti-theater core).

The audit's finding: the old flow claimed personalization it did not have — "I saw your
Instagram", "since price was your concern", "your last tattoo" — for customers whose rows
carry no such data. 65w.13 made the DETERMINISTIC copy safe by construction (it only
emits a claim when the backing field is present), but the LLM path is prompt-only: a
model can still hallucinate a per-customer claim the prompt told it not to. This is the
deterministic POST-generation net that catches both paths at one chokepoint, mirroring
:mod:`cells.offer_guard`:

  * :func:`find_personalization_claims` — every second-person claim about the customer's
    interests / objection / tattoo-history / social / artist-preference in the copy;
  * :func:`personalization_violations` — a HARD check: any such claim whose backing fact
    is ABSENT for this lead is a violation (fail-closed). With a history-less customer
    (no interests, no conversation, no social) ANY of these claims violates.

Precision-first: the patterns target explicit SECOND-PERSON possessive claims ("your
Instagram", "your last tattoo", "I saw your…"), not generic marketing copy ("reply to
book", "our artists"), so a grounded/honest draft never trips it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

# Claim categories. Each: a human label, the detection regex (second-person, possessive),
# and the ``facts`` key(s) whose presence GROUNDS it. A claim with no grounded fact = fake.
_CLAIM_RULES: tuple[tuple[str, re.Pattern[str], tuple[str, ...]], ...] = (
    (
        "social",
        re.compile(
            r"\b(?:your\s+(?:instagram|insta|ig|facebook|social|profile|feed|posts?|stories|story|dms?)"
            r"|(?:i|we)\s+(?:saw|checked|looked at|noticed|scrolled|browsed)\s+your"
            r"|on\s+your\s+(?:page|profile|feed|instagram|socials?))\b",
            re.IGNORECASE,
        ),
        ("ig_handle", "linkedin_handle", "social_handles"),
    ),
    (
        "interest",
        re.compile(
            r"\b(?:your\s+(?:interest|love|passion|taste|obsession)s?\s+(?:in|for)\b"
            r"|you(?:'re| are)\s+(?:into|a fan of|drawn to|obsessed with)\b"
            r"|based on your\s+(?:interest|taste|style|aesthetic)"
            r"|your\s+(?:favou?rite|preferred)\s+(?:style|design|aesthetic))",
            re.IGNORECASE,
        ),
        ("interests",),
    ),
    (
        "history",
        re.compile(
            r"\b(?:your\s+(?:last|previous|recent|past|first)\s+(?:tattoo|session|piece|visit|appointment|booking|ink)"
            r"|since\s+your\s+(?:last|previous|visit)"
            r"|when\s+you\s+(?:came in|visited|booked|got)"
            r"|your\s+(?:past|prior)\s+(?:bookings?|sessions?|visits?|appointments?|work))\b",
            re.IGNORECASE,
        ),
        ("tattoo_history",),
    ),
    (
        "artist_preference",
        re.compile(
            r"\b(?:your\s+(?:favou?rite|preferred|go-to|usual)\s+artist"
            r"|you\s+(?:love|prefer|always book with|like working with|keep coming back to)\s+[A-Z])",
            re.IGNORECASE,
        ),
        ("artist",),
    ),
    (
        "objection",
        re.compile(
            r"\b(?:(?:i|we)\s+know\s+(?:the\s+)?(?:price|cost|budget)"
            r"|you\s+(?:said|mentioned|told us|felt|were worried|were hesitant|weren't sure)\b"
            r"|your\s+(?:concern|hesitation|worry)\s+(?:about|with|was)"
            r"|since\s+(?:price|cost|budget|timing)\s+was)",
            re.IGNORECASE,
        ),
        ("primary_objection",),
    ),
)


def _fact_present(facts: Mapping[str, Any], key: str) -> bool:
    """Whether ``facts[key]`` is a real, non-empty value. Lists/strings both handled;
    the objection sentinel ``none-found`` counts as ABSENT (no grounded objection)."""
    val = facts.get(key)
    if val is None:
        return False
    if isinstance(val, str):
        v = val.strip().lower()
        return bool(v) and v not in {"none-found", "none", "unknown"}
    if isinstance(val, (list, tuple, set)):
        return any(str(x).strip() for x in val)
    return bool(val)


def find_personalization_claims(text: str) -> list[str]:
    """Every personalization claim category asserted in ``text`` (deduped, in rule
    order). A category appears at most once regardless of how many times it matches."""
    found: list[str] = []
    for label, pattern, _grounds in _CLAIM_RULES:
        if pattern.search(text or ""):
            found.append(label)
    return found


def personalization_violations(
    text: str, facts: Mapping[str, Any] | None = None
) -> list[str]:
    """The HARD check, pure: every personalization claim in ``text`` must be grounded in
    a fact that is actually present for this lead. A claim whose backing field is absent
    is a violation. With ``facts=None``/empty (a history-less customer), ANY claim
    violates (fail-closed — the anti-theater default).

    Returns human-readable violations; empty == clean."""
    f: Mapping[str, Any] = facts or {}
    violations: list[str] = []
    for label, pattern, grounds in _CLAIM_RULES:
        if not pattern.search(text or ""):
            continue
        if not any(_fact_present(f, k) for k in grounds):
            grounded_by = " / ".join(grounds)
            violations.append(
                f"fabricated {label} personalization (copy claims the customer's "
                f"{label}, but no {grounded_by} is on file for this lead)"
            )
    return violations


def personalization_ok(text: str, facts: Mapping[str, Any] | None = None) -> bool:
    """True iff ``text`` makes no ungrounded personalization claim for this lead."""
    return not personalization_violations(text, facts)


def facts_view(
    facts: Mapping[str, Any] | None = None,
    *,
    objection: str | None = None,
    profile: Any = None,
) -> dict[str, Any]:
    """Assemble the grounding view the guard checks: the lead ``facts`` plus the
    grounded objection (from the interview/psych analyst) so an objection-answering
    draft substantiates ONLY when a real objection was measured. ``profile`` (if given)
    supplies ``primary_objection`` when not passed explicitly."""
    view: dict[str, Any] = dict(facts or {})
    obj = objection
    if obj is None and profile is not None:
        pf = getattr(profile, "primary_objection", None)
        obj = getattr(pf, "value", None) if pf is not None else None
    if obj:
        view["primary_objection"] = obj
    return view


# Convenience: expose the claim/facts keys for tests + call-site clarity.
CLAIM_LABELS: Sequence[str] = tuple(label for label, _p, _g in _CLAIM_RULES)
