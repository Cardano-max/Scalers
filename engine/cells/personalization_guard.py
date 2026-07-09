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
    # CustomerAcq-wwy.7 (r8, the smoking gun): staged skindesign drafts implied a prior
    # relationship ("work with you again", "the people I have tattooed … want to work
    # with me again") for leads whose row carries name+email ONLY. These are
    # RELATIONSHIP-implying claims — "again"/"back"/"been a while"/prior-conversation
    # phrasing that fabricates a shared history — which the second-person-possessive
    # rules above do not catch. Grounded ONLY by real prior-relationship evidence:
    # tattoo history, a relationship-implying lifecycle value, a win-back persona
    # signal, or a REAL prior conversation. Precision: every pattern is anchored on an
    # explicit relationship phrase — the opt-out line "I won't reach out again" and
    # honest first-contact copy never trip it (no bare "again"/"back").
    (
        "implied_relationship",
        re.compile(
            # -- returning / "again" / "back" (a prior visit) --------------------- #
            r"\b(?:(?:work|working|tattoo|tattooing|ink|inking|see|seeing|create|do)\s+"
            r"with\s+you\s+(?:again|once\s+more|another\s+time)"
            r"|tattoo\s+you\s+again"
            r"|(?:see|seeing)\s+you\s+(?:again|back)"
            r"|(?:have|want|welcome|get|love\s+to\s+have|bring)\s+you\s+back"
            r"|welcome(?:\s+you)?\s+back"
            r"|back\s+in\s+the\s+(?:chair|studio|shop)"
            r"|come\s+back\s+(?:in|and|to|for)\b"
            r"|round\s+(?:two|2|three|3)"
            r"|pick\s+up\s+where\s+we\s+left\s+off"
            r"|once\s+more"
            # -- "been a while" / time-since (a prior relationship in time) -------- #
            r"|it(?:'s| has|s)\s+been\s+(?:a\s+while|too\s+long|some\s+time|ages)"
            r"|been\s+(?:a\s+while|too\s+long|some\s+time|ages)\s+since"
            r"|long\s+time\s+no\s+see"
            r"|haven'?t\s+(?:seen|heard\s+from)\s+you"
            r"|we\s+haven'?t\s+seen\s+you"
            # -- missing / thinking-of (an existing bond) ------------------------- #
            r"|(?:i|we)(?:'ve| have)?\s+miss(?:ed)?\s+(?:seeing\s+|hearing\s+from\s+)?(?:you|ya)"
            r"|been\s+thinking\s+about\s+you"
            r"|thinking\s+of\s+you"
            # -- your next / prior visit possessive ------------------------------- #
            r"|your\s+next\s+(?:tattoo|piece|session|appointment|visit|ink)"
            r"|your\s+(?:first|last)\s+session\s+with\s+us"
            # -- named prior clientele -------------------------------------------- #
            r"|(?:people|clients|customers|everyone|folks|those)\s+(?:i|we)(?:'ve| have)?\s+"
            r"(?:tattooed|inked|worked\s+with|done)"
            r"|(?:returning|repeat|past|former|previous|existing|loyal)\s+"
            r"(?:client|customer|guest|patron)"
            # -- prior CONVERSATION claim family ---------------------------------- #
            r"|(?:per|from|following\s+up\s+on|about|re:?)\s+our\s+"
            r"(?:last\s+|previous\s+|recent\s+)?(?:conversation|chat|call|talk|discussion|message)"
            r"|as\s+(?:we\s+discussed|discussed|promised)"
            r"|like\s+we\s+(?:talked\s+about|discussed|spoke\s+about|said)"
            r"|when\s+we\s+(?:spoke|talked|chatted|met|last\s+spoke)"
            r"|we\s+(?:spoke|talked|chatted|met)\s+(?:earlier|before|last|recently|about)"
            r"|(?:great|good|nice|lovely)\s+to\s+reconnect"
            r"|reconnect(?:ing)?\s+(?:with\s+you|after)"
            r"|(?:great|good|nice|lovely)\s+(?:meeting|seeing|chatting\s+with)\s+you"
            r"|(?:great|good|nice)\s+to\s+(?:see|hear\s+from|catch\s+up\s+with)\s+you\s+again)",
            re.IGNORECASE,
        ),
        # Grounding keys are DERIVED, evidence-gated signals set by facts_view — never
        # a raw field whose mere presence over-grounds (e.g. lifecycle_stage='lead-no-
        # visit' means NEVER visited, so it must NOT ground a "welcome back").
        ("tattoo_history", "win_back_candidate", "returning_lifecycle", "prior_conversation"),
    ),
)


# Lifecycle values that imply a REAL prior relationship (an actual past visit/booking).
# Deliberately EXCLUDES 'lead-no-visit' / 'lead' / 'prospect' / 'new' — a never-visited
# lead greeted with "welcome back" is exactly the fabrication this guard refuses
# (CustomerAcq-wwy.7 adversarial finding: the research cohort's DEFAULT includes
# 'lead-no-visit', which must not ground implied-history copy).
_RELATIONSHIP_LIFECYCLES = frozenset({
    "lapsing", "lapsed", "churn-risk", "churn_risk", "churn risk", "dormant", "inactive",
    "returning", "repeat", "repeat-client", "loyal", "active", "reactivation",
    "win-back", "winback", "win_back", "past-customer", "former-customer",
})

# customer_type / segment substrings that mark an EXISTING / returning customer (a real
# prior relationship). A generic "customer"/"lead"/"prospect" is NOT here — only tokens
# that assert a prior visit/booking. Substring match so "recurring regular" or "lapsed
# VIP" ground; the name+email-only smoking-gun leads (no such type) never do.
_RETURNING_CUSTOMER_HINTS = (
    "recurring", "returning", "repeat", "regular", "loyal", "vip", "existing",
    "past customer", "past client", "former", "lapsed", "lapsing", "winback",
    "win-back", "win back", "churn", "reactivat", "dormant",
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
    supplies ``primary_objection`` when not passed explicitly.

    Prior-relationship evidence (grounds the ``implied_relationship`` rule) is derived
    into EVIDENCE-GATED boolean signals — never a raw field whose mere presence would
    over-ground:

    * ``win_back_candidate`` — from ``persona_traits.win_back_candidate`` (nested).
    * ``returning_lifecycle`` — True ONLY when the lifecycle value implies a real prior
      visit (:data:`_RELATIONSHIP_LIFECYCLES`); ``lead-no-visit`` (never visited) does
      NOT set it — that lead must not receive "welcome back" copy.
    * ``prior_conversation`` — True ONLY when the psych ``profile`` reports
      ``had_conversation`` (a profile is ALWAYS produced — a deterministic floor read —
      so mere profile-presence is NOT evidence of a real conversation; the analyst's
      ``had_conversation`` flag is).

    Absent evidence stays absent — never a fabricated ground (CustomerAcq-wwy.7)."""
    view: dict[str, Any] = dict(facts or {})
    obj = objection
    if obj is None and profile is not None:
        pf = getattr(profile, "primary_objection", None)
        obj = getattr(pf, "value", None) if pf is not None else None
    if obj:
        view["primary_objection"] = obj

    traits = view.get("persona_traits") or {}
    lifecycle = view.get("lifecycle_stage")
    if isinstance(traits, Mapping):
        if traits.get("win_back_candidate"):
            view["win_back_candidate"] = True
        lifecycle = lifecycle or traits.get("lifecycle_stage")
    if _normalize_lifecycle(lifecycle) in _RELATIONSHIP_LIFECYCLES:
        view["returning_lifecycle"] = True
    # A segment / customer_type that names an EXISTING/returning customer is itself
    # relationship evidence (e.g. "recurring regular", "lapsed VIP"). A generic type
    # ("customer"/"lead"/"prospect") or an absent one is NOT — the smoking-gun leads
    # (name+email only, no type) stay ungrounded.
    ctype = _normalize_lifecycle(view.get("customer_type"))
    if ctype and any(h in ctype for h in _RETURNING_CUSTOMER_HINTS):
        view["returning_lifecycle"] = True

    # A profile is a deterministic floor read that ALWAYS exists — only its
    # ``had_conversation`` flag evidences a REAL prior conversation.
    if _had_conversation(profile):
        view["prior_conversation"] = True
    return view


def _normalize_lifecycle(value: Any) -> str:
    """Lowercased, whitespace-collapsed lifecycle token for membership tests."""
    return " ".join(str(value or "").strip().lower().split())


def _had_conversation(profile: Any) -> bool:
    """Whether ``profile`` evidences a REAL prior conversation (its ``had_conversation``
    flag), handling both the ``PsychProfile`` object and a plain dict. A missing flag or
    a ``None`` profile is False — no fabricated relationship ground."""
    if profile is None:
        return False
    if isinstance(profile, Mapping):
        return bool(profile.get("had_conversation"))
    return bool(getattr(profile, "had_conversation", False))


# Convenience: expose the claim/facts keys for tests + call-site clarity.
CLAIM_LABELS: Sequence[str] = tuple(label for label, _p, _g in _CLAIM_RULES)
