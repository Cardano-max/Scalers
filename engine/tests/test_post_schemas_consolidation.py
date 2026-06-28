"""MediaKind/PostDraft consolidation tests (integration gate before a9m.5/a9m.9).

Proves there is exactly ONE MediaKind object across the engine (so identity checks
never fail across module boundaries) and that a valid TEXT post passes its media
gate end-to-end (the bug super flagged: duplicate enums fail-closed a TEXT post).
"""

from __future__ import annotations

import cells.ideate as ideate
import cells.post_draft as post_draft
import cells.post_schemas as post_schemas
from cells.content_brief import Platform
from cells.media_validators import validate_post_draft
from cells.post_schemas import MediaKind, MediaSpec, PostDraft


def test_single_canonical_mediakind_object():
    # all three modules expose THE SAME enum object (no second enum anywhere)
    assert ideate.MediaKind is post_schemas.MediaKind
    assert post_draft.MediaKind is post_schemas.MediaKind
    assert ideate.MediaKind.TEXT is post_draft.MediaKind.TEXT


def test_post_draft_and_mediaspec_are_canonical():
    assert post_draft.PostDraft is post_schemas.PostDraft
    assert post_draft.MediaSpec is post_schemas.MediaSpec


def _text_post() -> PostDraft:
    return PostDraft(
        platform=Platform.INSTAGRAM, caption="A text-only update.",
        hashtags=["studionews"], call_to_action="DM to book",
        media=MediaSpec(kind=MediaKind.TEXT, brief="no asset"),
    )


def test_text_post_passes_media_gate_end_to_end():
    gates = validate_post_draft(_text_post())
    by = {g.name: g for g in gates}
    # TEXT skips media checks -> NO media gate, so it can't fail-closed
    assert not any(n.endswith("_aspect") or n.startswith("reel_") or n == "media_kind" for n in by)
    # caption + hashtag gates still run and pass
    assert by["caption_length"].passed and by["hashtag_count"].passed
    assert all(g.passed for g in gates)
