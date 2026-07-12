"""Style-preference memory — learn from operator edits (client direction,
PA meeting 2026-07-11).

The client called the drafts "generic" and framed the system as "a trainable
agent … we can start training it." The training signal we already have is the
operator's EDITS: when they change a draft before approving, the delta between the
draft and the edited version is a preference. This module distills that delta into
reusable style preferences and feeds them back into the next draft's brief — so
the copy stops being generic and starts sounding like the operator's own edits.

Two honest layers:

  * :func:`learn_style_preference` — PURE (no DB): compares the original draft to
    the operator's edited version and extracts DETERMINISTIC preferences (length,
    emoji, hype/exclamations, discount mentions, hashtags, and specific
    phrases the operator consistently removed). Nothing is invented — every
    preference traces to an actual edit.
  * :func:`accumulate_preferences` / :func:`render_style_preferences_block` — merge
    preferences across many edits (a signal only becomes a rule once the operator
    does it repeatedly) and render the brief block that ORDERS the next draft to
    honor them.

Persistence (a ``style`` subject on the memories table) is a thin wiring step; the
distillation + accumulation intelligence here is the trainable core.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF←-⇿⬀-⯿]"
)
_DISCOUNT_RE = re.compile(
    r"(\d+\s*%\s*off|\$\s*\d+\s*off|\bdiscount\b|\bpromo\b|\bcoupon\b|\bcode\s+[A-Za-z0-9]+)",
    re.IGNORECASE,
)
_HASHTAG_RE = re.compile(r"#\w+")
# A signal must recur this many times across edits before it becomes a firm rule.
_RULE_THRESHOLD = 2


def _emoji_count(text: str) -> int:
    return len(_EMOJI_RE.findall(text or ""))


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z']+", (text or "").lower())


def learn_style_preference(original: str, edited: str) -> dict[str, Any]:
    """Distill ONE operator edit (draft → approved-with-edits) into style
    preferences. Returns ``{"signals": [str, ...], "removed_phrases": [str, ...]}``.

    Signals are deterministic labels for what the edit did (``shorter`` /
    ``longer`` / ``fewer_emoji`` / ``no_emoji`` / ``less_hype`` / ``drop_discounts``
    / ``fewer_hashtags``); ``removed_phrases`` are specific sentences the operator
    cut. HONEST: an empty edit, or an "edit" identical to the original, yields no
    signals — nothing is inferred from a non-change."""
    orig = (original or "").strip()
    edit = (edited or "").strip()
    signals: list[str] = []
    removed_phrases: list[str] = []
    if not edit or edit == orig:
        return {"signals": signals, "removed_phrases": removed_phrases}

    # Length preference (meaningful deltas only — a few chars is noise).
    if len(edit) <= len(orig) * 0.8:
        signals.append("shorter")
    elif len(edit) >= len(orig) * 1.25:
        signals.append("longer")

    # Emoji: removed entirely vs merely reduced.
    oe, ee = _emoji_count(orig), _emoji_count(edit)
    if oe > 0 and ee == 0:
        signals.append("no_emoji")
    elif ee < oe:
        signals.append("fewer_emoji")

    # Hype: exclamation marks cut.
    if edit.count("!") < orig.count("!"):
        signals.append("less_hype")

    # Discounts/offers the operator removed (a brand-safety signal for artists).
    if _DISCOUNT_RE.search(orig) and not _DISCOUNT_RE.search(edit):
        signals.append("drop_discounts")

    # Hashtags trimmed.
    if len(_HASHTAG_RE.findall(edit)) < len(_HASHTAG_RE.findall(orig)):
        signals.append("fewer_hashtags")

    # Specific sentences the operator cut (verbatim, so the next draft avoids them).
    edit_sentences = {s.strip().lower() for s in re.split(r"(?<=[.!?])\s+", edit) if s.strip()}
    for s in re.split(r"(?<=[.!?])\s+", orig):
        s = s.strip()
        if s and s.lower() not in edit_sentences and len(s.split()) >= 3:
            removed_phrases.append(s)

    return {"signals": signals, "removed_phrases": removed_phrases[:5]}


def accumulate_preferences(edits: list[tuple[str, str]]) -> dict[str, Any]:
    """Merge preferences across many (original, edited) edits into firm RULES.

    A signal is promoted to a rule once the operator has applied it at least
    :data:`_RULE_THRESHOLD` times (a one-off edit is a suggestion, a repeated one
    is a rule) — so the learned voice reflects a consistent preference, not a
    single outlier. Returns ``{"rules": [str], "suggestions": [str],
    "avoid_phrases": [str], "edit_count": int}``."""
    counts: Counter[str] = Counter()
    avoid: Counter[str] = Counter()
    n = 0
    for original, edited in edits or []:
        pref = learn_style_preference(original, edited)
        if pref["signals"] or pref["removed_phrases"]:
            n += 1
        counts.update(pref["signals"])
        avoid.update(p.lower() for p in pref["removed_phrases"])
    rules = sorted(s for s, c in counts.items() if c >= _RULE_THRESHOLD)
    suggestions = sorted(s for s, c in counts.items() if c < _RULE_THRESHOLD)
    avoid_phrases = [p for p, c in avoid.most_common() if c >= _RULE_THRESHOLD]
    return {
        "rules": rules,
        "suggestions": suggestions,
        "avoid_phrases": avoid_phrases,
        "edit_count": n,
    }


_SIGNAL_TEXT: dict[str, str] = {
    "shorter": "keep it shorter — the operator consistently trims length",
    "longer": "the operator expands drafts — give more substance",
    "no_emoji": "no emoji",
    "fewer_emoji": "use emoji sparingly",
    "less_hype": "dial back the hype — fewer exclamations, calmer tone",
    "drop_discounts": "do NOT lead with discounts/promos — the operator removes them",
    "fewer_hashtags": "fewer hashtags",
}


def render_style_preferences_block(prefs: dict[str, Any] | None) -> str:
    """The brief block ordering the next draft to honor the operator's learned
    edits — or ``""`` when nothing has been learned yet (no training signal). Rules
    are firm; suggestions are softer; avoid-phrases are verbatim lines the operator
    cut. This is what makes the agent 'trainable': edits become guidance."""
    if not prefs:
        return ""
    rules = prefs.get("rules") or []
    suggestions = prefs.get("suggestions") or []
    avoid = prefs.get("avoid_phrases") or []
    if not (rules or suggestions or avoid):
        return ""
    lines = [
        "\nOPERATOR STYLE PREFERENCES (learned from "
        f"{prefs.get('edit_count', 0)} past edit(s)) — the operator has edited "
        "drafts toward this voice; match it so this draft doesn't read as generic:",
    ]
    for sig in rules:
        lines.append(f"  - RULE: {_SIGNAL_TEXT.get(sig, sig)}")
    for sig in suggestions:
        lines.append(f"  - tends to: {_SIGNAL_TEXT.get(sig, sig)}")
    for phrase in avoid[:5]:
        lines.append(f"  - avoid phrasing like: \"{phrase[:120]}\"")
    return "\n".join(lines)
