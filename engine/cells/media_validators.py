"""Media/format validators (bead a9m.6 / POST-02) — pure code, no LLM.

Deterministic platform facts (IG/FB, Graph API **v25.0** — spec §5): a post that
violates them wastes a publish slot or fails at the API, so they are a gate the
router reads, not a model judgement. Each check returns a harness ``Gate``
(``name``/``passed``/``detail`` — the bead's label/ok/reason); ``validate_post_draft``
returns ONE gate per check so multiple violations are all reported, and an
out-of-spec creative yields ``passed=False`` with a reason, blocking auto/approve.

Scope (Phase-3, no real media): the spec carries kind/aspect/duration + caption/
hashtags, so those are validated here. Real-media facts — JPEG bytes, codec/
container, file size — are the real publisher's checks (Phase-6), not present in
``MediaSpec``; noted, not silently skipped.

Pinned to Graph API **v25.0**; plan the **v26 (Sep 2026)** migration so these
limits don't silently drift.
"""

from __future__ import annotations

from cells.post_draft import MediaKind, MediaSpec, PostDraft
from harness.state import Gate

# ── pinned limits (Graph API v25.0; spec §5) ─────────────────────────────────
CAPTION_MAX_CHARS = 2200          # IG/FB caption hard limit
HASHTAG_MAX_COUNT = 30            # IG hashtag cap
REEL_ASPECT = "9:16"             # Reels must be vertical 9:16
REEL_MIN_SECONDS = 5.0            # inclusive
REEL_MAX_SECONDS = 90.0           # inclusive
IMAGE_ASPECTS = frozenset({"1:1", "4:5", "1.91:1"})  # IG feed image/carousel ratios


def _gate(name: str, ok: bool, reason: str | None = None) -> Gate:
    return Gate(name=name, passed=ok, detail=None if ok else reason)


def gate_caption(caption: str) -> Gate:
    n = len(caption)
    return _gate("caption_length", n <= CAPTION_MAX_CHARS,
                 f"caption {n} chars > {CAPTION_MAX_CHARS} limit")


def gate_hashtags(hashtags: list[str]) -> Gate:
    n = len(hashtags)
    return _gate("hashtag_count", n <= HASHTAG_MAX_COUNT,
                 f"{n} hashtags > {HASHTAG_MAX_COUNT} limit")


def gate_media(media: MediaSpec) -> list[Gate]:
    """Media gates for the spec's kind. TEXT has no media checks; an unhandled
    kind fails closed (never pass an unvalidated creative)."""
    kind = media.kind
    if kind is MediaKind.TEXT:
        return []
    if kind is MediaKind.REEL:
        aspect_ok = media.aspect_ratio == REEL_ASPECT
        dur = media.duration_s
        dur_ok = dur is not None and REEL_MIN_SECONDS <= dur <= REEL_MAX_SECONDS
        return [
            _gate("reel_aspect", aspect_ok,
                  f"reel aspect {media.aspect_ratio!r} != {REEL_ASPECT}"),
            _gate("reel_duration", dur_ok,
                  f"reel duration {dur}s not in [{REEL_MIN_SECONDS},{REEL_MAX_SECONDS}]"),
        ]
    if kind in (MediaKind.IMAGE, MediaKind.CAROUSEL):
        ok = media.aspect_ratio in IMAGE_ASPECTS
        return [_gate(f"{kind.value}_aspect", ok,
                      f"{kind.value} aspect {media.aspect_ratio!r} not in {sorted(IMAGE_ASPECTS)}")]
    # Unknown / unhandled media kind — fail closed.
    return [_gate("media_kind", False, f"unhandled media kind {kind!r}")]


def validate_post_draft(draft: PostDraft) -> list[Gate]:
    """All media/format gates for a draft (one per check). Caption + hashtag gates
    always run (text posts keep them); media gates depend on the kind."""
    gates = [gate_caption(draft.caption), gate_hashtags(draft.hashtags)]
    gates.extend(gate_media(draft.media))
    return gates
