"""MockPublisher + approve-path unit tests (POST-03 / a9m.8) — DB-free, hermetic.

The exactly-once behaviour against real Postgres lives in test_mock_publisher_pg.py;
this file covers the pure logic: PublishIntent round-trip, the PLATFORM-QUALIFIED
idempotency key (the 4hj collision fix), the mock connector's keyed idempotency, the
out-of-spec gate guard, and the no-credentials property.
"""

from __future__ import annotations

import asyncio

import pytest

from cells.content_brief import Platform
from cells.post_schemas import MediaKind, MediaSpec, PostDraft
from harness.state import Gate
from sideeffects.posting import (
    MockPostingConnector,
    OutOfSpecError,
    PublishIntent,
    posting_idempotency_key,
    posting_target,
)


def _draft(platform: Platform = Platform.INSTAGRAM, caption: str = "fresh ink, fresh start") -> PostDraft:
    return PostDraft(
        platform=platform,
        caption=caption,
        hashtags=["neotraditional", "austintattoo"],
        call_to_action="DM to book your consult",
        media=MediaSpec(kind=MediaKind.IMAGE, aspect_ratio="4:5", brief="healed floral piece, soft light"),
    )


# ── PublishIntent round-trip ──────────────────────────────────────────────────


def test_intent_from_draft_carries_fields():
    intent = PublishIntent.from_draft(_draft())
    assert intent.platform is Platform.INSTAGRAM
    assert intent.caption == "fresh ink, fresh start"
    assert intent.hashtags == ["neotraditional", "austintattoo"]
    assert intent.media.kind is MediaKind.IMAGE
    assert intent.media_ref is None  # mock: no real asset


def test_intent_payload_round_trips_through_envelope():
    intent = PublishIntent.from_draft(_draft())
    payload = {**intent.to_payload(), "tenant_id": "ladies8391", "run_id": "r1"}
    # from_payload ignores the enqueue-envelope siblings and rebuilds the intent.
    assert PublishIntent.from_payload(payload) == intent


# ── platform-qualified idempotency key (CustomerAcq-4hj) ──────────────────────


def test_target_is_platform_qualified():
    assert posting_target(Platform.INSTAGRAM) == "instagram:feed"
    assert posting_target(Platform.FACEBOOK) == "facebook:feed"


def test_same_content_different_platform_yields_distinct_keys():
    """The 4hj fix: the SAME caption/media to IG and FB must NOT collide, or one
    platform's enqueue dedups away and that platform silently never posts."""
    ig = posting_idempotency_key("t", PublishIntent.from_draft(_draft(Platform.INSTAGRAM)))
    fb = posting_idempotency_key("t", PublishIntent.from_draft(_draft(Platform.FACEBOOK)))
    assert ig != fb
    assert ig.split(":")[2] == "instagram" and fb.split(":")[2] == "facebook"


def test_same_draft_same_key_is_stable():
    """Replay / re-run of the same approved post derives the SAME key (so the
    UNIQUE constraint dedups it) — and run_id/scheduled_at do NOT change the key."""
    a = posting_idempotency_key("t", PublishIntent.from_draft(_draft(), scheduled_at="2026-07-01T10:00:00Z"))
    b = posting_idempotency_key("t", PublishIntent.from_draft(_draft(), scheduled_at="2026-08-09T22:00:00Z"))
    assert a == b


def test_different_caption_yields_different_key():
    a = posting_idempotency_key("t", PublishIntent.from_draft(_draft(caption="one")))
    b = posting_idempotency_key("t", PublishIntent.from_draft(_draft(caption="two")))
    assert a != b


def test_different_creative_same_text_yields_different_key():
    """Regression (adversarial review): two posts with IDENTICAL caption/hashtags/
    CTA/platform but a DIFFERENT media brief (or aspect/duration) are DIFFERENT posts
    and must derive DIFFERENT keys — else the second dedups away and is silently
    never published (the 4hj under-fire class along the creative axis)."""
    base = _draft()
    other_brief = base.model_copy(update={"media": base.media.model_copy(update={"brief": "a totally different creative"})})
    other_aspect = base.model_copy(update={"media": base.media.model_copy(update={"aspect_ratio": "1:1"})})
    k = posting_idempotency_key("t", PublishIntent.from_draft(base))
    assert k != posting_idempotency_key("t", PublishIntent.from_draft(other_brief))
    assert k != posting_idempotency_key("t", PublishIntent.from_draft(other_aspect))


def test_different_tenant_yields_different_key():
    intent = PublishIntent.from_draft(_draft())
    assert posting_idempotency_key("tenant-a", intent) != posting_idempotency_key("tenant-b", intent)


# ── MockPostingConnector keyed idempotency + no creds ─────────────────────────


def test_mock_connector_is_keyed_idempotent():
    conn = MockPostingConnector()
    intent = PublishIntent.from_draft(_draft())
    payload = intent.to_payload()
    key = posting_idempotency_key("t", intent)

    r1 = asyncio.run(conn.send(key, "posting", payload))
    r2 = asyncio.run(conn.send(key, "posting", payload))  # same key -> dedup
    assert r1 == r2
    assert conn.call_count == 1            # ONE distinct effect
    assert conn.invocation_count == 2      # but two raw sends
    assert r1.deep_link == "mock://instagram/mock_instagram_1"
    assert r1.extra["mock"] is True        # audit marker: never a real Meta call


def test_mock_connector_distinct_keys_distinct_effects():
    conn = MockPostingConnector()
    ig = PublishIntent.from_draft(_draft(Platform.INSTAGRAM))
    fb = PublishIntent.from_draft(_draft(Platform.FACEBOOK))
    asyncio.run(conn.send(posting_idempotency_key("t", ig), "posting", ig.to_payload()))
    asyncio.run(conn.send(posting_idempotency_key("t", fb), "posting", fb.to_payload()))
    assert conn.call_count == 2
    assert {p.platform for p in conn.published} == {Platform.INSTAGRAM, Platform.FACEBOOK}


def test_mock_connector_advertises_itself_as_mock():
    # Belt for "no real Meta/Gmail creds touched": the connector is explicitly mock
    # and constructed with no client/secret/token argument.
    assert MockPostingConnector.is_mock is True


# ── out-of-spec guard (approve a failing-gate Action) ─────────────────────────


def test_publish_refuses_failing_gate_before_enqueue():
    from sideeffects.posting import publish_approved_post

    gates = [Gate(name="aspect_ratio", passed=True), Gate(name="banned_phrase", passed=False, detail="'slay'")]

    async def _go():
        # conn is never touched because the guard raises first; pass a sentinel.
        await publish_approved_post(conn=object(), tenant_id="t", draft=_draft(), gates=gates)

    with pytest.raises(OutOfSpecError):
        asyncio.run(_go())


def test_publish_allows_all_passing_gates_path_reaches_enqueue():
    # With all gates passing the guard does NOT raise; it then tries to enqueue and
    # fails on the sentinel conn (AttributeError) — proving the guard let it through.
    from sideeffects.posting import publish_approved_post

    gates = [Gate(name="aspect_ratio", passed=True), Gate(name="caption_length", passed=True)]

    async def _go():
        await publish_approved_post(conn=object(), tenant_id="t", draft=_draft(), gates=gates)

    with pytest.raises(Exception) as exc:
        asyncio.run(_go())
    assert not isinstance(exc.value, OutOfSpecError)  # passed the gate guard
