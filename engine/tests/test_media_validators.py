"""Media/format validator tests (bead a9m.6) — pure code, table-driven.

In-spec creatives pass; out-of-spec (wrong reel aspect/duration, over-length
caption, too many hashtags) fail with a reason; boundaries (5s/90s/9:16/limits)
are inclusive; text-only skips media but keeps caption/hashtag checks; multiple
violations all report; gates land in GraphState.
"""

from __future__ import annotations

import pytest

from cells.content_brief import Platform
from cells.media_validators import (
    CAPTION_MAX_CHARS,
    HASHTAG_MAX_COUNT,
    validate_post_draft,
)
from cells.post_draft import MediaKind, MediaSpec, PostDraft
from harness.nodes import MediaValidateNode


def _draft(*, kind=MediaKind.REEL, aspect="9:16", dur=22.0, caption="ok caption",
           hashtags=None) -> PostDraft:
    return PostDraft(
        platform=Platform.INSTAGRAM, caption=caption,
        hashtags=hashtags if hashtags is not None else ["fineline", "brooklyntattoo"],
        call_to_action="DM to book",
        media=MediaSpec(kind=kind, aspect_ratio=aspect, duration_s=dur, brief="b"),
    )


def _gates(draft):
    return {g.name: g for g in validate_post_draft(draft)}


# ── in-spec ──────────────────────────────────────────────────────────────────


def test_in_spec_reel_all_ok():
    g = _gates(_draft())
    assert all(x.passed for x in g.values())
    assert {"caption_length", "hashtag_count", "reel_aspect", "reel_duration"} <= set(g)


def test_in_spec_image_all_ok():
    g = _gates(_draft(kind=MediaKind.IMAGE, aspect="4:5", dur=None))
    assert all(x.passed for x in g.values())
    assert "image_aspect" in g


# ── out-of-spec (each ok=false + reason) ─────────────────────────────────────


def test_wrong_reel_aspect_fails():
    g = _gates(_draft(aspect="4:5"))
    assert g["reel_aspect"].passed is False and "4:5" in g["reel_aspect"].detail


def test_reel_too_long_fails():
    g = _gates(_draft(dur=120.0))
    assert g["reel_duration"].passed is False and g["reel_duration"].detail


def test_overlength_caption_fails():
    g = _gates(_draft(caption="x" * (CAPTION_MAX_CHARS + 1)))
    assert g["caption_length"].passed is False


def test_too_many_hashtags_fails():
    g = _gates(_draft(hashtags=[f"t{i}" for i in range(HASHTAG_MAX_COUNT + 1)]))
    assert g["hashtag_count"].passed is False


# ── boundaries (inclusive) ───────────────────────────────────────────────────


@pytest.mark.parametrize("dur,ok", [(5.0, True), (90.0, True), (4.9, False), (90.1, False)])
def test_reel_duration_boundaries(dur, ok):
    assert _gates(_draft(dur=dur))["reel_duration"].passed is ok


def test_caption_and_hashtag_at_limit_pass():
    g = _gates(_draft(caption="x" * CAPTION_MAX_CHARS,
                      hashtags=[f"t{i}" for i in range(HASHTAG_MAX_COUNT)]))
    assert g["caption_length"].passed and g["hashtag_count"].passed


# ── text-only + multi-violation ──────────────────────────────────────────────


def test_text_only_skips_media_keeps_caption():
    g = _gates(_draft(kind=MediaKind.TEXT, aspect=None, dur=None))
    assert "reel_aspect" not in g and "image_aspect" not in g
    assert "caption_length" in g and "hashtag_count" in g


def test_multiple_violations_all_reported():
    g = _gates(_draft(aspect="1:1", dur=200.0, caption="x" * 3000,
                      hashtags=[f"t{i}" for i in range(40)]))
    failed = {n for n, x in g.items() if not x.passed}
    assert {"caption_length", "hashtag_count", "reel_aspect", "reel_duration"} <= failed


# ── node wiring ──────────────────────────────────────────────────────────────


async def test_node_writes_gates_and_step_log():
    from harness.state import GraphState

    state = GraphState(tenant_id="ink", run_id="r1", topic="t", draft=_draft())
    out = await MediaValidateNode()(state)
    assert out["gates"] and all(g.passed for g in out["gates"])
    assert out["step_log"] == ["validate_media:ok"]


async def test_node_step_log_flags_failures():
    from harness.state import GraphState

    state = GraphState(tenant_id="ink", run_id="r1", topic="t", draft=_draft(dur=120.0))
    out = await MediaValidateNode()(state)
    assert "fail:reel_duration" in out["step_log"][0]
