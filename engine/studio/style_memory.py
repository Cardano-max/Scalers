"""Style-preference memory — learn from operator edits (client direction,
PA meeting 2026-07-11).

The client called the drafts "generic" and framed the system as "a trainable
agent … we can start training it." The training signal we already have is the
operator's EDITS: when they change a draft before approving, the delta between the
draft and the edited version is a preference. This module distills that delta into
reusable style preferences and renders the brief block that ORDERS the next draft
to honor them — so the copy stops being generic and starts sounding like the
operator's own edits.

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

The loop is CLOSED end-to-end:

  1. CAPTURE — the Review Queue's real edit gesture (the ``editActionDraft``
     GraphQL mutation → :func:`obsapi.repo.edit_action_draft`) records each
     (original, edited) pair via :func:`record_style_edit`;
  2. PERSIST — a ``style`` subject on the shared ``memories`` table (subject-type
     CHECK widened idempotently, mirroring :mod:`studio.artist_memory`), idempotent
     per exact edit pair so a retried mutation never double-counts;
  3. FEED BACK — :func:`load_style_preferences` rebuilds the accumulated rules
     deterministically from the stored pairs and
     :func:`studio.ig_pipeline.build_ig_brief_block` appends
     :func:`render_style_preferences_block` to every subsequent drafting brief.

Empty history renders nothing — the block appears only once real edits exist.
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


# --------------------------------------------------------------------------- #
# Persistence — the ``style`` subject on the shared ``memories`` table.
# The write side is triggered by the REAL operator gesture (the Review Queue's
# edit-draft mutation records each (original, edited) pair); the read side feeds
# the accumulated preferences back into the drafting brief. This closes the
# trainable loop the client asked for: edits become durable guidance.
# --------------------------------------------------------------------------- #

STYLE_SUBJECT_TYPE = "style"

# Idempotent widening of the memories subject-type CHECK to admit 'style'. Keeps
# every previously-admitted value (incl. 'artist') so this composes with
# studio.artist_memory's own widen block regardless of run order.
_WIDEN_SQL = """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'memories'::regclass
          AND conname  = 'memories_subject_type_check'
          AND pg_get_constraintdef(oid) NOT LIKE '%%style%%'
    ) THEN
        ALTER TABLE memories DROP CONSTRAINT memories_subject_type_check;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'memories'::regclass
          AND conname  = 'memories_subject_type_check'
    ) THEN
        ALTER TABLE memories ADD CONSTRAINT memories_subject_type_check
            CHECK (subject_type IN
                   ('customer','campaign','conversation','fact','artist','style'));
    END IF;
END $$;
"""


def _dsn(dsn: str | None = None) -> str:
    import os

    return (dsn or os.environ.get("ENGINE_DATABASE_URL")
            or "postgresql://scalers:scalers@localhost:5432/scalers")


def _connect(dsn: str | None = None):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(_dsn(dsn), autocommit=True, row_factory=dict_row)


def ensure_style_memory_schema(dsn: str | None = None) -> None:
    """Ensure ``memories`` exists and its subject-type CHECK admits ``'style'``.
    Idempotent, mirrors :func:`studio.artist_memory.ensure_artist_memory_schema`."""
    from memory import MemoryStore

    MemoryStore(dsn=_dsn(dsn)).ensure_schema()
    with _connect(dsn) as conn:
        conn.execute(_WIDEN_SQL)


def record_style_edit(
    tenant_id: str,
    original: str,
    edited: str,
    *,
    action_id: str | None = None,
    channel: str | None = None,
    dsn: str | None = None,
) -> dict[str, Any]:
    """Distill + persist ONE real operator edit. Returns the distilled preference
    (``{"signals", "removed_phrases"}``) — empty when the edit carried no signal
    (nothing is stored; a non-change trains nothing). Idempotent on the exact
    (original, edited) pair via the memories natural key, so a retried mutation
    never double-counts an edit toward the rule threshold."""
    import hashlib
    import uuid as _uuid

    pref = learn_style_preference(original, edited)
    if not (pref["signals"] or pref["removed_phrases"]):
        return {}
    ensure_style_memory_schema(dsn)
    text = "style edit: " + ", ".join(pref["signals"] or ["phrase-cut"])
    chash = hashlib.sha256(f"{original}\x00{edited}".encode("utf-8")).hexdigest()
    from psycopg.types.json import Json

    with _connect(dsn) as conn:
        conn.execute(
            """
            INSERT INTO memories
                (id, tenant_id, subject_type, subject_id, text, embedding,
                 metadata, content_hash, is_test)
            VALUES (%s, %s, %s, %s, %s, NULL, %s, %s, FALSE)
            ON CONFLICT (tenant_id, subject_type, COALESCE(subject_id, ''), content_hash)
            DO NOTHING
            """,
            ("mem_" + _uuid.uuid4().hex[:16], tenant_id, STYLE_SUBJECT_TYPE,
             channel or "default", text,
             Json({"original": original, "edited": edited,
                   "signals": pref["signals"],
                   "removed_phrases": pref["removed_phrases"],
                   "action_id": action_id}),
             chash),
        )
    return pref


def load_style_preferences(
    tenant_id: str, *, limit: int = 200, dsn: str | None = None
) -> dict[str, Any] | None:
    """The tenant's accumulated style preferences, rebuilt deterministically from
    the stored (original, edited) pairs — or ``None`` when no edits are on file
    (the brief block renders nothing; never a fabricated preference)."""
    try:
        with _connect(dsn) as conn:
            rows = conn.execute(
                "SELECT metadata FROM memories WHERE tenant_id = %s "
                "AND subject_type = %s AND is_test = FALSE "
                "ORDER BY created_at ASC LIMIT %s",
                (tenant_id, STYLE_SUBJECT_TYPE, limit),
            ).fetchall()
    except Exception:
        return None  # no DB / no table — honest nothing
    edits: list[tuple[str, str]] = []
    for r in rows:
        md = r.get("metadata") or {}
        if isinstance(md, dict) and md.get("original") and md.get("edited"):
            edits.append((md["original"], md["edited"]))
    if not edits:
        return None
    return accumulate_preferences(edits)
