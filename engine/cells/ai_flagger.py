"""AI-tell flagger — a DETERMINISTIC, pure-code validator for the HARN-02 bank
(skill: human-tone, CustomerAcq-1mk.3).

The operator's hard rule: no AI slop ships. This module enforces the human-tone
bar in *code*, not by hope. It detects the machine-writing tells the "human-tone"
skill targets — em-dashes, contrast framing ("it's not X, it's Y"), the rhetorical
rule-of-three, and generic transitions ("Moreover", "In conclusion", …) — with
**pure regex/string rules and no model call**, so it is fully reproducible and
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

from cells.validators import FieldValidator, Severity, ValidationIssue, _get


class AiTellKind(str, Enum):
    """Categories of machine-writing tell this flagger detects."""

    EM_DASH = "em_dash"
    CONTRAST_FRAMING = "contrast_framing"
    RULE_OF_THREE = "rule_of_three"
    GENERIC_TRANSITION = "generic_transition"


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

# Generic AI transitions / openers (whole-phrase, case-insensitive).
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
    "it's worth noting",
    "it is worth noting",
    "when it comes to",
    "in today's",
    "let's dive in",
    "dive into",
    "in the world of",
    "it's important to note",
    "at the end of the day",
    "needless to say",
)
_TRANSITION_RE = re.compile(
    r"(?<!\w)(" + "|".join(re.escape(p) for p in _TRANSITIONS) + r")(?!\w)", re.IGNORECASE
)


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
    allowlist: tuple[str, ...] = ()
    em_dash_severity: Severity = Severity.ERROR
    contrast_severity: Severity = Severity.ERROR
    transition_severity: Severity = Severity.ERROR
    triad_severity: Severity = Severity.WARN
    # Wordlist-based detectors (contrast/transition) are English-specific; skip
    # them on text that does not look like English to avoid foreign-language FPs.
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

    return sorted(tells, key=lambda t: t.start)


def _severity_for(kind: AiTellKind, config: FlaggerConfig) -> Severity:
    return {
        AiTellKind.EM_DASH: config.em_dash_severity,
        AiTellKind.CONTRAST_FRAMING: config.contrast_severity,
        AiTellKind.RULE_OF_THREE: config.triad_severity,
        AiTellKind.GENERIC_TRANSITION: config.transition_severity,
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
    """Deterministically strip the SAFE tells (em-dash spacing) without a model.

    Only transforms that cannot change meaning: an em/en dash (or double hyphen)
    used as punctuation becomes a comma. Contrast framing, triads, and
    transitions are left for the humanize rewrite cell — rewriting them in code
    risks changing intent. Idempotent.
    """
    # "word — word" / "word--word" -> "word, word"
    out = re.sub(r"\s*(?:—|–|--)\s*", ", ", text)
    out = re.sub(r"\s{2,}", " ", out)
    return out.strip()
