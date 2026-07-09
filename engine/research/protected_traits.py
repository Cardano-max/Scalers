"""protected_traits — the deterministic sensitive-trait guard (spec §7 / §24).

The spec bans INFERRING protected / sensitive traits about a lead — gender, age,
ethnicity/race, health/disability, religion, sexuality, financial status/distress,
immigration status, political views. This module encodes that ban as data + pure
functions so BOTH research surfaces enforce it identically and testably:

  * :data:`PROTECTED_TRAITS` — the module-level registry: category -> the
    deterministic patterns that detect an assertion of that trait in text.
  * :func:`scan_protected_traits` — find every protected-trait assertion in a text.
  * :func:`trait_violations` — the assertions that are NOT permitted, after the two
    honest carve-outs:
      1. **first-party derivation** (:data:`FIRST_PARTY_DERIVATIONS` /
         :func:`allowed_categories`): a category is permitted when the customer
         explicitly provided the field it derives from (e.g. a real ``dob`` column
         -> an age group MAY be derived; no dob -> any age read is dropped).
      2. **first-party verbatim** (:func:`build_first_party_corpus`): the exact
         matched span already appears verbatim in the customer's own first-party
         data (their conversation words, their CSV/DB row, our CRM memories about
         them). Quoting the customer's own words is stated evidence, not an
         inference — e.g. a price objection "I can't afford it right now" survives
         because THEY said it; a model's "she probably can't afford it" does not.
  * :func:`filter_lines` — line-level scrubber for free text (research snippets,
    social context): drops each offending line and returns an honest drop record.

Posture: deterministic and fail-closed. When a pattern is ambiguous the filter
errs toward DROPPING (an over-filtered snippet costs a citation; an under-filtered
one asserts a protected trait about a real person). No LLM is involved: the model
may propose, but this filter is the boundary the output must pass.
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple

# --------------------------------------------------------------------------- #
# The registry — category names + detection patterns.
# --------------------------------------------------------------------------- #
GENDER = "gender"
AGE = "age"
ETHNICITY = "ethnicity"
HEALTH = "health"
RELIGION = "religion"
SEXUALITY = "sexuality"
FINANCIAL_STATUS = "financial_status"
IMMIGRATION_STATUS = "immigration_status"
POLITICAL_VIEWS = "political_views"

# category -> regex pattern strings (case-insensitive). Patterns aim at ASSERTIONS
# ABOUT A PERSON, not craft vocabulary: "japanese woman" matches, "japanese-style
# sleeve" does not; "old" matches, "old-school traditional" does not; a bare
# "black"/"white"/"asian" (ordinary tattoo-style words) only matches when bound to
# a person word ("black woman", "asian man").
PROTECTED_TRAITS: dict[str, tuple[str, ...]] = {
    GENDER: (
        r"\b(?:she|he)\s+(?:is|was|s|seems|looks|appears|sounds|feels|probably|"
        r"likely|may|might|must|could|would|will|has|had|does|doesn'?t|"
        r"can'?t|cannot)\b",
        r"\b(?:she|he)'s\b",
        r"\bshe/her\b",
        r"\bhe/him\b",
        r"\bthey/them\b",
        r"\b(?:woman|women|man|men|female|male|girl|girls|boy|boys|lady|ladies|"
        r"gentleman|gentlemen)\b",
        r"\b(?:non-?binary|transgender|cisgender|trans\s+(?:woman|man))\b",
    ),
    AGE: (
        # "old-school"/"old school" is a real tattoo style, not an age read.
        r"\bold(?!\s*-?\s*school)\b",
        r"\b(?:young|younger|older|elderly|middle-?aged|teen(?:age[rd]?)?s?|"
        r"millennials?|gen\s*-?z|zoomers?|boomers?|senior\s+citizens?)\b",
        r"\bin\s+(?:her|his|their)\s+(?:teens|twenties|thirties|forties|fifties|"
        r"sixties|\d0'?s)\b",
        r"\b\d{1,2}\s*(?:years?|yrs?)[\s-]*old\b",
        r"\bage[ds]?\s+\d{1,2}\b",
        r"\bage\s+(?:group|band|bracket|range)\b",
    ),
    ETHNICITY: (
        r"\b(?:latina|latino|latinx|hispanic|caucasian|african[-\s]american|"
        r"afro[-\s]\w+|biracial|multiracial|mixed[-\s]race|person\s+of\s+colou?r|"
        r"bipoc)\b",
        # Style words become a trait read only when bound to a person word.
        r"\b(?:asian|black|white|brown|indian|native|indigenous|chinese|japanese|"
        r"korean|vietnamese|filipin[oa]|mexican|arab|middle[-\s]eastern|european|"
        r"pacific\s+islander)\s+(?:woman|women|man|men|male|female|person|people|"
        r"girl|boy|guy|lady|folks?|descent|heritage|ethnicity)\b",
        r"\bethnicit(?:y|ies)\b",
        r"\bracial(?:ly)?\b",
    ),
    HEALTH: (
        r"\b(?:disabled|disabilit(?:y|ies)|chronic(?:ally)?\s+ill\w*|illness|"
        r"diagnos\w+|depress\w+|anxiety|bipolar|autis\w+|adhd|ptsd|cancer|"
        r"diabet\w+|pregnan\w+|addict\w+|sobriety|in\s+recovery|wheelchair|"
        r"mental[-\s]health|medicat\w+|therapy|surgery|hospital\w*)\b",
    ),
    RELIGION: (
        r"\b(?:christian(?:ity)?|catholic|protestant|muslim|islam(?:ic)?|jewish|"
        r"judaism|hindu(?:ism)?|buddhis[mt]s?|sikhs?|mormons?|atheists?|agnostics?|"
        r"evangelicals?|devout|church-?go\w+|religious|religion)\b",
        r"\b(?:her|his|their)\s+faith\b",
    ),
    SEXUALITY: (
        r"\b(?:gay|lesbian|bisexual|pansexual|asexual|queer|homosexual|"
        r"heterosexual|lgbtq?(?:ia)?\+?)\b",
        r"\bsexual\s+orientation\b",
        r"\bsexuality\b",
    ),
    FINANCIAL_STATUS: (
        r"\b(?:can'?t|cannot|couldn'?t|unable\s+to)\s+afford\b",
        r"\b(?:broke|poor|low[-\s]income|wealthy|rich|affluent|welfare|"
        r"food\s+stamps|bankrupt\w*|in\s+debt|paycheck\s+to\s+paycheck)\b",
        r"\bfinancial(?:ly)?\s+(?:struggl\w+|distress\w*|trouble[ds]?|hardship|"
        r"unstable|strapped|insecure)\b",
        r"\bmoney\s+(?:trouble|problems|issues|worries)\b",
    ),
    IMMIGRATION_STATUS: (
        r"\b(?:immigrants?|immigration\s+status|undocumented|refugees?|asylum|"
        r"green\s+card|visa\s+status|deport\w+|citizenship)\b",
    ),
    POLITICAL_VIEWS: (
        r"\b(?:democrats?|republicans?|conservatives?|liberals?|leftists?|"
        r"right[-\s]wing|left[-\s]wing|maga|progressives?|libertarians?|"
        r"socialists?|political(?:ly)?|vot(?:es?|ed|ing)\s+for)\b",
    ),
}

# category -> the first-party CSV/DB fields whose explicit presence permits deriving
# that trait (the customer provided the value themselves). Today only age-from-dob:
# a real ``dob`` column on the lead's own row makes an age-group read legitimate.
FIRST_PARTY_DERIVATIONS: dict[str, tuple[str, ...]] = {
    AGE: ("dob",),
}

_COMPILED: dict[str, tuple[re.Pattern[str], ...]] = {
    cat: tuple(re.compile(p, re.IGNORECASE) for p in pats)
    for cat, pats in PROTECTED_TRAITS.items()
}

_NORM_RE = re.compile(r"[^a-z0-9]+")


def _norm(text: str) -> str:
    """Same normalization discipline as the psych corpus gate: lowercase, collapse
    every non-alphanumeric run to one space — so a verbatim-match check survives
    trivial punctuation/casing differences but a genuinely absent span still fails."""
    return _NORM_RE.sub(" ", (text or "").lower()).strip()


class TraitMatch(NamedTuple):
    """One detected protected-trait assertion: its category + the matched span."""

    category: str
    span: str


def scan_protected_traits(text: str) -> list[TraitMatch]:
    """Every protected-trait assertion detected in ``text`` (deduped per category+span).
    Pure + deterministic; an empty text scans clean."""
    if not text:
        return []
    out: list[TraitMatch] = []
    seen: set[tuple[str, str]] = set()
    for cat, patterns in _COMPILED.items():
        for pat in patterns:
            for m in pat.finditer(text):
                key = (cat, m.group(0).lower())
                if key not in seen:
                    seen.add(key)
                    out.append(TraitMatch(category=cat, span=m.group(0)))
    return out


def allowed_categories(facts: dict[str, Any] | None) -> frozenset[str]:
    """The trait categories this lead's OWN first-party fields permit deriving
    (:data:`FIRST_PARTY_DERIVATIONS`). E.g. a present ``dob`` -> {age}. Empty facts
    permit nothing."""
    f = facts or {}
    return frozenset(
        cat
        for cat, fields in FIRST_PARTY_DERIVATIONS.items()
        if any(f.get(field) for field in fields)
    )


# The lead-row fields whose values count as customer-provided first-party data. The
# generated persona (system-inferred) is deliberately EXCLUDED — an inferred persona
# value must never launder a protected trait into the exemption corpus.
_FIRST_PARTY_FACT_FIELDS: tuple[str, ...] = (
    "name", "email", "phone", "city", "state", "notes", "customer_type",
    "lead_stage", "payment_status", "artist", "shop", "dob",
    "ig_handle", "linkedin_handle",
)


def build_first_party_corpus(
    facts: dict[str, Any] | None,
    conversation_turns: list[dict[str, Any]] | None = None,
) -> str:
    """The normalized first-party text a matched trait span may be exempted against:
    the lead's own CSV/DB row values, their real conversation words, our CRM
    memories and tattoo-history notes about them. NEVER includes web/social text or
    the system-generated persona — third-party or inferred text cannot self-exempt."""
    parts: list[str] = []
    f = facts or {}
    for k in _FIRST_PARTY_FACT_FIELDS:
        v = f.get(k)
        if v:
            parts.append(str(v))
    for i in f.get("interests") or []:
        if i:
            parts.append(str(i))
    for t in f.get("tattoo_history") or []:
        for k in ("style", "notes", "artist"):
            v = (t or {}).get(k)
            if v:
                parts.append(str(v))
    for m in f.get("memories") or []:
        v = (m or {}).get("text") if isinstance(m, dict) else None
        if v:
            parts.append(str(v))
    for turn in conversation_turns or []:
        v = (turn or {}).get("text") if isinstance(turn, dict) else None
        if v:
            parts.append(str(v))
    return _norm("\n".join(parts))


def trait_violations(
    text: str,
    *,
    allowed: frozenset[str] | set[str] = frozenset(),
    first_party_corpus: str = "",
) -> list[TraitMatch]:
    """The protected-trait assertions in ``text`` that are NOT permitted.

    A match is permitted only via the two honest carve-outs: its category is in
    ``allowed`` (first-party derivation, e.g. age-from-dob), or its exact span
    appears verbatim in the customer's own ``first_party_corpus`` (they said /
    provided those words themselves). Everything else is a violation — the caller
    must drop or blank the text and record the drop."""
    out: list[TraitMatch] = []
    for match in scan_protected_traits(text):
        if match.category in allowed:
            continue
        span_n = _norm(match.span)
        if span_n and first_party_corpus and span_n in first_party_corpus:
            continue
        out.append(match)
    return out


def _drop_record(viols: list[TraitMatch]) -> dict[str, str]:
    """One honest, auditable drop record: the categories that fired and the short
    matched tokens (so an operator can verify the filter is not over-firing) — never
    the full asserted sentence, which would just re-state the inference."""
    cats = sorted({v.category for v in viols})
    spans = sorted({v.span.strip().lower() for v in viols})
    return {
        "categories": ", ".join(cats),
        "matched": ", ".join(spans)[:120],
    }


def filter_lines(
    text: str,
    *,
    allowed: frozenset[str] | set[str] = frozenset(),
    first_party_corpus: str = "",
) -> tuple[str, list[dict[str, str]]]:
    """Line-level scrub for free text (research snippets / social context): every
    line carrying a non-exempt protected-trait assertion is DROPPED (never rewritten
    — we do not paraphrase third-party text), and each drop is recorded. Returns
    ``(clean_text, drops)``; a fully-offending text comes back as ``("", drops)``."""
    if not text:
        return "", []
    kept: list[str] = []
    drops: list[dict[str, str]] = []
    for line in str(text).splitlines():
        if not line.strip():
            kept.append(line)
            continue
        viols = trait_violations(
            line, allowed=allowed, first_party_corpus=first_party_corpus
        )
        if viols:
            drops.append(_drop_record(viols))
            continue
        kept.append(line)
    return "\n".join(kept).strip(), drops


__all__ = [
    "PROTECTED_TRAITS",
    "FIRST_PARTY_DERIVATIONS",
    "GENDER", "AGE", "ETHNICITY", "HEALTH", "RELIGION", "SEXUALITY",
    "FINANCIAL_STATUS", "IMMIGRATION_STATUS", "POLITICAL_VIEWS",
    "TraitMatch",
    "scan_protected_traits",
    "allowed_categories",
    "build_first_party_corpus",
    "trait_violations",
    "filter_lines",
]
