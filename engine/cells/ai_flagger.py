"""AI-tell flagger — a DETERMINISTIC, pure-code validator for the HARN-02 bank
(skill: human-tone, CustomerAcq-1mk.3).

The operator's hard rule: no AI slop ships. This module enforces the human-tone
bar in *code*, not by hope. It detects the machine-writing tells from the
ruleset spec (docs/skills/ai-flagger-validator-spec.md, AF-01..AF-08) — em-dashes,
contrast framing ("it's not X, it's Y"), the rhetorical rule-of-three, generic
transitions ("Moreover", "In conclusion", …), banned-slop lexicon ("unleash",
"elevate your", …), hedging/weasel filler ("arguably", "it's worth noting"),
listicle cadence ("Here are 5 …" / ≥3 bullet lines), and emoji-bullet lines — all
with **pure regex/string rules and no model call**, so it is fully reproducible and
feeds the validator-pass-rate metric.

Two surfaces:

* :func:`ai_flagger` — a :class:`~cells.validators.FieldValidator` for the bank:
  flags tells on a text field (tunable thresholds + allowlist for false positives).
* :func:`normalize_ai_tells` — a deterministic, meaning-preserving strip for the
  *safe* subset (em-dash spacing). Semantic tells (contrast/triad/transition) are
  flagged for the humanize rewrite cell, never auto-rewritten here (meaning risk).

Re-authored from the upstream human-tone skill (Varnan-Tech/opendirectory, MIT)
into our format; see engine/skills/human-tone/. Registration for agent use is
gated on the 1mk.1 sec-vetting + eval gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from cells.validators import DEFAULT_AI_TELLS, FieldValidator, Severity, ValidationIssue, _get


class AiTellKind(str, Enum):
    """Categories of machine-writing tell this flagger detects (AF-01..AF-08)."""

    EM_DASH = "em_dash"                      # AF-01
    CONTRAST_FRAMING = "contrast_framing"    # AF-02
    RULE_OF_THREE = "rule_of_three"          # AF-03
    GENERIC_TRANSITION = "generic_transition"  # AF-04
    BANNED_SLOP = "banned_slop"              # AF-05
    HEDGING = "hedging"                      # AF-06
    LISTICLE = "listicle"                    # AF-07
    EMOJI_BULLET = "emoji_bullet"            # AF-08


@dataclass(frozen=True)
class AiTell:
    """One detected tell, with its location so a rewrite can target it."""

    kind: AiTellKind
    text: str
    start: int
    end: int
    message: str


# --------------------------------------------------------------------------- #
# Patterns (pure regex — deterministic, no model)
# --------------------------------------------------------------------------- #

# Em/en dash, or a double-hyphen used as one. (A plain spaced hyphen is allowed.)
_EM_DASH_RE = re.compile(r"—|–|(?<=\s)--(?=\s)|(?<=\w)--(?=\w)")

# "it's not X, it's Y" / "not just X but Y" / "isn't about X — it's Y" / "not X but rather Y".
_CONTRAST_RES = (
    re.compile(r"\bit'?s\s+not\s+[^.,;:]{1,40}?[,;]\s*(?:but\s+)?it'?s\s+", re.IGNORECASE),
    re.compile(r"\bnot\s+(?:just|only|merely|simply)\s+[^.,;:]{1,50}?\s+but\s+", re.IGNORECASE),
    re.compile(r"\bisn'?t\s+(?:just|about|only)?\s*[^.,;:]{1,40}?[—–,;-]\s*it'?s\b", re.IGNORECASE),
    re.compile(r"\bnot\s+[^.,;:]{1,40}?\s+but\s+rather\b", re.IGNORECASE),
)

# Rhetorical triad: the classic three parallel items "A, B, and C" / "A, B, C"
# with short items (<= 3 words each). Two commas are required, so an ordinary
# "X, Y and Z" (single comma) does not trip it — keeps false positives down.
_TRIAD_RE = re.compile(
    r"\b[\w'-]+(?:\s+[\w'-]+){0,2},\s+[\w'-]+(?:\s+[\w'-]+){0,2},\s+(?:and\s+)?[\w'-]+(?:\s+[\w'-]+){0,2}\b"
)

# AF-04 — Generic AI transitions / openers (whole-phrase, case-insensitive).
# "worth noting" lives in the hedging detector (AF-06), not here, to avoid a
# double-flag.
_TRANSITIONS: tuple[str, ...] = (
    "moreover",
    "furthermore",
    "additionally",
    "in conclusion",
    "in summary",
    "that said",
    "ultimately",
    "notably",
    "importantly",
    "when it comes to",
    "in today's",
    "let's dive in",
    "dive into",
    "in the world of",
    "it's important to note",
    "at the end of the day",
    "needless to say",
    # AF-04 wordlist additions (spec).
    "to be fair",
    "all in all",
    "with that said",
    "first and foremost",
    "last but not least",
    "rest assured",
    "truth be told",
)
_TRANSITION_RE = re.compile(
    r"(?<!\w)(" + "|".join(re.escape(p) for p in _TRANSITIONS) + r")(?!\w)", re.IGNORECASE
)

# AF-05 — Banned-slop lexicon. Derived from the master DEFAULT_AI_TELLS list
# (validators.py), minus any phrase the transition detector already owns, so a
# slop term is never double-flagged as both BANNED_SLOP and GENERIC_TRANSITION.
# Longer phrases first so the regex prefers the most specific match.
_TRANSITION_SET = {p.lower() for p in _TRANSITIONS}


def _owned_by_transition(phrase: str) -> bool:
    """True if a transition phrase is a substring — that detector already owns it."""
    low = phrase.lower()
    return any(t in low for t in _TRANSITION_SET)


# Longer phrases first so the regex prefers the most specific match.
_BANNED_SLOP: tuple[str, ...] = tuple(
    sorted(
        (p for p in DEFAULT_AI_TELLS if not _owned_by_transition(p)),
        key=len,
        reverse=True,
    )
)
_BANNED_SLOP_RE = re.compile(
    r"(?<!\w)(" + "|".join(re.escape(p) for p in _BANNED_SLOP) + r")(?!\w)", re.IGNORECASE
)

# AF-06 — Hedging / weasel filler (whole-phrase, case-insensitive). Separate from
# transitions; "worth noting" is owned here.
_HEDGE_RE = re.compile(
    r"(?<!\w)("
    r"it'?s worth noting|worth noting|arguably|in many ways|to some extent|"
    r"generally speaking|more often than not|for what it'?s worth|it could be argued|"
    r"somewhat|kind of|sort of|perhaps"
    r")(?!\w)",
    re.IGNORECASE,
)

# AF-07 — Listicle cadence. Either signal trips: a "Here are N …" opener, or a
# field with >= N bullet-style lines. Language-agnostic (structural).
_LISTICLE_OPENER_RE = re.compile(r"\bhere(?:'?s| are)\s+\d+\s+\w+", re.IGNORECASE)
# A bullet emoji range (decorative bullets), reused by AF-08.
_BULLET_EMOJI = "\U0001f300-\U0001faff☀-➿•"
_BULLET_LINE_RE = re.compile(
    rf"^[ \t]*(?:[-*•]|\d+[.)]|[{_BULLET_EMOJI}])\s+\S", re.MULTILINE
)

# AF-08 — Emoji-bullet lines: a leading decorative emoji acting as a bullet.
_EMOJI_BULLET_LINE_RE = re.compile(rf"^[ \t]*[{_BULLET_EMOJI}]\s+\S", re.MULTILINE)
# Auto-fix: strip the leading bullet emoji + following whitespace (line preserved).
_EMOJI_BULLET_STRIP_RE = re.compile(rf"(?m)^([ \t]*)[{_BULLET_EMOJI}]\s+")


@dataclass(frozen=True)
class FlaggerConfig:
    """Tunable thresholds + allowlist for the flagger (false-positive control).

    Thresholds let legitimate use through (e.g. one triad, or a permitted
    em-dash budget); ``allowlist`` substrings exempt specific spans. Per-kind
    severities feed the bank: ERROR blocks (and triggers a cell repair), WARN is
    advisory but still reported.
    """

    max_em_dashes: int = 0          # any em-dash flags by default
    max_triads: int = 1             # one rhetorical triad is fine; flag beyond
    flag_contrast: bool = True
    flag_transitions: bool = True
    flag_banned_slop: bool = True
    flag_hedging: bool = True
    flag_listicle: bool = True
    flag_emoji_bullet: bool = True
    allowlist: tuple[str, ...] = ()
    em_dash_severity: Severity = Severity.ERROR
    contrast_severity: Severity = Severity.ERROR
    transition_severity: Severity = Severity.ERROR
    triad_severity: Severity = Severity.WARN
    banned_slop_severity: Severity = Severity.ERROR
    # AF-06/07/08 default to WARN (observable without over-blocking until tuned on
    # the gold set); per-cell config raises hedging to ERROR on hooks/headlines.
    hedge_severity: Severity = Severity.WARN
    listicle_severity: Severity = Severity.WARN
    emoji_bullet_severity: Severity = Severity.WARN
    # Structural thresholds (spec): >= these counts trip the rule.
    listicle_min_bullets: int = 3
    emoji_bullet_min_lines: int = 2
    # Wordlist-based detectors (contrast/transition/banned-slop/hedging) are
    # English-specific; skip them on non-English text to avoid foreign-language
    # FPs. The structural rules (em-dash/listicle/emoji-bullet) are language-agnostic.
    english_only: bool = True


def _looks_english(text: str) -> bool:
    """Heuristic: most letters are ASCII Latin -> treat as English."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return True
    ascii_letters = sum(1 for c in letters if c.isascii())
    return ascii_letters / len(letters) >= 0.9


def _allowlisted(span_text: str, allowlist: tuple[str, ...]) -> bool:
    low = span_text.lower()
    return any(a.lower() in low or low in a.lower() for a in allowlist)


def detect_ai_tells(text: str, config: FlaggerConfig = FlaggerConfig()) -> list[AiTell]:
    """Return every AI tell in ``text`` after thresholds + allowlist (pure code)."""
    if not text:
        return []

    tells: list[AiTell] = []
    english = (not config.english_only) or _looks_english(text)

    # Em-dash: flag occurrences beyond the allowed budget.
    em = [m for m in _EM_DASH_RE.finditer(text) if not _allowlisted(m.group(0), config.allowlist)]
    for m in em[config.max_em_dashes :]:
        tells.append(
            AiTell(AiTellKind.EM_DASH, m.group(0), m.start(), m.end(), "em-dash / double-hyphen")
        )

    # Contrast framing (English wordlist).
    if config.flag_contrast and english:
        for rx in _CONTRAST_RES:
            for m in rx.finditer(text):
                if _allowlisted(m.group(0), config.allowlist):
                    continue
                tells.append(
                    AiTell(
                        AiTellKind.CONTRAST_FRAMING,
                        m.group(0).strip(),
                        m.start(),
                        m.end(),
                        "contrast framing ('not X but Y')",
                    )
                )

    # Rule of three: flag triads beyond the allowed budget.
    triads = [m for m in _TRIAD_RE.finditer(text) if not _allowlisted(m.group(0), config.allowlist)]
    for m in triads[config.max_triads :]:
        tells.append(
            AiTell(AiTellKind.RULE_OF_THREE, m.group(0), m.start(), m.end(), "rhetorical rule-of-three")
        )

    # Generic transitions (English wordlist).
    if config.flag_transitions and english:
        for m in _TRANSITION_RE.finditer(text):
            if _allowlisted(m.group(0), config.allowlist):
                continue
            tells.append(
                AiTell(
                    AiTellKind.GENERIC_TRANSITION,
                    m.group(0),
                    m.start(),
                    m.end(),
                    f"generic transition ({m.group(0)!r})",
                )
            )

    # AF-05 — banned slop lexicon (English wordlist).
    if config.flag_banned_slop and english:
        for m in _BANNED_SLOP_RE.finditer(text):
            if _allowlisted(m.group(0), config.allowlist):
                continue
            tells.append(
                AiTell(
                    AiTellKind.BANNED_SLOP,
                    m.group(0),
                    m.start(),
                    m.end(),
                    f"banned slop phrase ({m.group(0)!r})",
                )
            )

    # AF-06 — hedging / weasel filler (English wordlist).
    if config.flag_hedging and english:
        for m in _HEDGE_RE.finditer(text):
            if _allowlisted(m.group(0), config.allowlist):
                continue
            tells.append(
                AiTell(
                    AiTellKind.HEDGING,
                    m.group(0),
                    m.start(),
                    m.end(),
                    f"hedging filler ({m.group(0)!r})",
                )
            )

    # AF-07 — listicle cadence (structural; language-agnostic). Either an opener
    # "Here are N …" or >= listicle_min_bullets bullet-style lines trips it.
    if config.flag_listicle:
        opener = _LISTICLE_OPENER_RE.search(text)
        bullet_lines = _BULLET_LINE_RE.findall(text)
        if opener is not None:
            tells.append(
                AiTell(
                    AiTellKind.LISTICLE, opener.group(0), opener.start(), opener.end(),
                    "listicle opener ('Here are N …')",
                )
            )
        elif len(bullet_lines) >= config.listicle_min_bullets:
            tells.append(
                AiTell(
                    AiTellKind.LISTICLE, "", 0, 0,
                    f"listicle cadence ({len(bullet_lines)} bullet lines)",
                )
            )

    # AF-08 — emoji-bullet lines (structural; language-agnostic).
    if config.flag_emoji_bullet:
        emoji_lines = _EMOJI_BULLET_LINE_RE.findall(text)
        if len(emoji_lines) >= config.emoji_bullet_min_lines:
            tells.append(
                AiTell(
                    AiTellKind.EMOJI_BULLET, "", 0, 0,
                    f"emoji-bullet lines ({len(emoji_lines)})",
                )
            )

    return sorted(tells, key=lambda t: t.start)


def _severity_for(kind: AiTellKind, config: FlaggerConfig) -> Severity:
    return {
        AiTellKind.EM_DASH: config.em_dash_severity,
        AiTellKind.CONTRAST_FRAMING: config.contrast_severity,
        AiTellKind.RULE_OF_THREE: config.triad_severity,
        AiTellKind.GENERIC_TRANSITION: config.transition_severity,
        AiTellKind.BANNED_SLOP: config.banned_slop_severity,
        AiTellKind.HEDGING: config.hedge_severity,
        AiTellKind.LISTICLE: config.listicle_severity,
        AiTellKind.EMOJI_BULLET: config.emoji_bullet_severity,
    }[kind]


def ai_flagger(field_name: str, config: FlaggerConfig = FlaggerConfig()) -> FieldValidator:
    """A validator-bank check that flags AI tells on ``field_name`` (pure code).

    Emits one :class:`ValidationIssue` per detected tell, at the per-kind severity
    from ``config``. Drop it into any :class:`~cells.validators.ValidatorBank`; its
    ERROR issues block the value and feed the validator-pass-rate.
    """

    def _fn(value):
        text = _get(value, field_name)
        if not isinstance(text, str):
            return []
        issues: list[ValidationIssue] = []
        for tell in detect_ai_tells(text, config):
            issues.append(
                ValidationIssue(
                    "ai_flagger",
                    _severity_for(tell.kind, config),
                    f"{field_name!r}: {tell.message} -> {tell.text!r}",
                )
            )
        return issues

    return FieldValidator("ai_flagger", _fn)


# --------------------------------------------------------------------------- #
# Deterministic safe strip (the meaning-preserving subset only)
# --------------------------------------------------------------------------- #


def normalize_ai_tells(text: str) -> str:
    """Deterministically strip the AUTO-FIX tells without a model (AF-01, AF-08).

    Only meaning-preserving transforms:

    * **AF-01** — an em/en dash (or double hyphen) used as punctuation becomes a
      comma.
    * **AF-08** — a leading decorative bullet emoji (and its trailing whitespace)
      is stripped from each line; the line text is preserved and inline emoji are
      left untouched.

    The semantic/structural tells (contrast, triad, transitions, banned slop,
    hedging, listicle) are FLAG-FOR-REGEN — left for the humanize rewrite cell,
    since rewriting them in code risks changing intent. Idempotent.
    """
    # AF-08: strip a leading bullet emoji per line, keeping the line's indentation.
    out = _EMOJI_BULLET_STRIP_RE.sub(r"\1", text)
    # AF-01: "word — word" / "word--word" -> "word, word".
    out = re.sub(r"\s*(?:—|–|--)\s*", ", ", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()
